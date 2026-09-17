"""Policy exceptions: scoped, time-limited, approved, and kept as history.

An exception lowers the action of **one rule** within **one explicit scope** -
a repository, a repository group, or the whole organization - to ``warn`` or
``allow`` until it expires. It is the only way below a mandatory requirement,
which is why it is bounded on every side:

* **Scope is explicit.** A repository exception names a repository of the same
  organization that the requester can see; a group exception names a group of
  the organization. An exception for one repository can never apply to another.
* **Exceptions expire.** ``expires_at`` is required and at most
  ``exception_max_days`` ahead. A permanent exception needs the organization
  setting ``allow_permanent_exceptions``, ``exceptions:approve`` for the
  requester, a justification, *and* approval by someone else.
* **Approval.** An exception needs approval when the rule's severity is at or
  above ``exception_approval_min_severity`` (``high`` by default: every bundled
  AI attribution rule), when it covers a group or the organization, or when it is
  permanent. The approver needs ``exceptions:approve`` and cannot be the
  requester. Otherwise the exception is active immediately.
* **Lifecycle.** ``requested`` -> ``active`` -> ``expired`` / ``revoked``;
  ``requested`` -> ``rejected`` / ``cancelled``. Rows are never deleted (a
  database trigger refuses it); at most one open (requested or active) exception
  exists per rule and scope.

Every transition is audited, invalidates the effective policy of exactly the
repositories in scope (in the same transaction), and notifies through the
notification outbox: approval requests, approvals, expiry warnings (once per
configured threshold) and ends (expired or revoked). The maintenance loop runs
:meth:`PolicyExceptionService.expire_due` and :meth:`warn_expiring`.

What an exception never does: remove detection (findings are still recorded,
with the exception that lowered them), change historical scan results, or
resolve violations.
"""

import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from commitguard.audit.models import SYSTEM_ACTOR, Actor, AuditEvent, AuditEventType
from commitguard.controlplane.access import Permission, Principal
from commitguard.controlplane.errors import (
    ConflictError,
    InputValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from commitguard.controlplane.pagination import parse_timestamp
from commitguard.controlplane.rules import CATALOG_BY_ID
from commitguard.core.decision import Action
from commitguard.core.result import Severity
from commitguard.github.storage import SqliteStateStore
from commitguard.governance.cache import invalidate_scope
from commitguard.governance.common import (
    MAX_REASON_CHARS,
    account_repositories,
    dt,
    is_hex_id,
    json_list,
    new_id,
    req_dt,
    require,
    text,
    ts,
    visible_repository_ids,
)
from commitguard.governance.settings import load_settings
from commitguard.notifications.deduplication import domain_key
from commitguard.notifications.models import NotificationEvent, NotificationType
from commitguard.notifications.outbox import emit
from commitguard.policies.defaults import DEFAULT_POLICIES
from commitguard.policies.governance import ExceptionGrant, ExceptionScope
from commitguard.services.audit import AuditService

EXPIRY_BATCH = 200
OPEN_STATUSES = ("requested", "active")
STATUSES = ("requested", "active", "rejected", "cancelled", "revoked", "expired")
EXPIRING_SOON = timedelta(days=7)


class ExceptionScopeView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: ExceptionScope
    id: str
    label: str


class PolicyExceptionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    organization_id: int
    rule_id: str
    rule_name: str
    severity: Severity
    scope: ExceptionScopeView
    action: Action
    reason: str
    status: str
    requires_approval: bool
    permanent: bool
    expires_at: datetime | None
    expiring_soon: bool
    requested_at: datetime
    requested_by: str | None
    decided_at: datetime | None
    decided_by: str | None
    decision_note: str | None
    activated_at: datetime | None
    revoked_at: datetime | None
    revoked_by: str | None
    revoke_reason: str | None
    expired_at: datetime | None
    can_approve: bool
    can_revoke: bool
    can_cancel: bool


def rule_severity(rule_id: str) -> Severity:
    entry = CATALOG_BY_ID.get(rule_id)
    return entry.severity if entry else Severity.HIGH


def scope_label(
    db: sqlite3.Connection | SqliteStateStore, account_id: int, scope: str, scope_id: str
) -> str | None:
    if scope == "organization":
        return "Organization"
    if scope == "group":
        sql = "SELECT name FROM repository_groups WHERE group_id = ? AND account_id = ?"
        params: tuple[object, ...] = (scope_id, int(account_id))
        if isinstance(db, sqlite3.Connection):
            row = db.execute(sql, params).fetchone()
        else:
            rows = db.query(sql, params)
            row = rows[0] if rows else None
        return f"Group {row['name']}" if row else None
    if not scope_id.isdigit():
        return None
    repositories = account_repositories(db, account_id)
    repository = repositories.get(int(scope_id))
    return repository.full_name if repository else None


def active_grants(
    db: sqlite3.Connection | SqliteStateStore,
    account_id: int,
    repository_id: int,
    group_ids: Sequence[str],
    now: datetime,
) -> list[ExceptionGrant]:
    """Active, unexpired exceptions that apply to one repository."""
    sql = (
        "SELECT e.exception_id, e.rule_id, e.action, e.scope_type, e.scope_id, e.expires_at, "
        "e.permanent, (SELECT name FROM repository_groups g WHERE g.group_id = e.scope_id) "
        "AS group_name FROM policy_exceptions e WHERE e.account_id = ? AND e.status = 'active' "
        "AND (e.permanent = 1 OR e.expires_at > ?) AND (e.scope_type = 'organization' OR "
        "(e.scope_type = 'repository' AND e.scope_id = ?) OR (e.scope_type = 'group' AND "
        "e.scope_id IN (SELECT value FROM json_each(?)))) ORDER BY e.exception_id LIMIT 256"
    )
    params = (int(account_id), ts(now), str(int(repository_id)), json.dumps(list(group_ids)))
    if isinstance(db, sqlite3.Connection):
        rows = db.execute(sql, params).fetchall()
    else:
        rows = db.query(sql, params)
    grants = []
    for row in rows:
        scope = ExceptionScope(row["scope_type"])
        label = {
            ExceptionScope.ORGANIZATION: "organization",
            ExceptionScope.GROUP: f"group {row['group_name'] or row['scope_id']}",
            ExceptionScope.REPOSITORY: "repository",
        }[scope]
        grants.append(
            ExceptionGrant(
                exception_id=row["exception_id"],
                rule_id=row["rule_id"],
                action=Action(row["action"]),
                scope=scope,
                scope_label=label,
                expires_at=None if row["permanent"] else dt(row["expires_at"]),
            )
        )
    return grants


class PolicyExceptionService:
    def __init__(
        self,
        store: SqliteStateStore,
        audit: AuditService,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._audit = audit
        self._now = now

    # -- views ------------------------------------------------------------ #
    def _view(self, row: sqlite3.Row, principal: Principal | None) -> PolicyExceptionView:
        account_id = int(row["account_id"])
        now = self._now()
        expires = dt(row["expires_at"])
        status = str(row["status"])
        label = scope_label(self._store, account_id, row["scope_type"], row["scope_id"])
        requester = row["requested_by_id"]
        can_approve = bool(
            principal is not None
            and status == "requested"
            and principal.can(Permission.EXCEPTIONS_APPROVE, account_id)
            and requester != principal.user_id
        )
        return PolicyExceptionView(
            id=row["exception_id"],
            organization_id=account_id,
            rule_id=row["rule_id"],
            rule_name=CATALOG_BY_ID[row["rule_id"]].name
            if row["rule_id"] in CATALOG_BY_ID
            else row["rule_id"],
            severity=Severity(row["severity"]),
            scope=ExceptionScopeView(
                type=ExceptionScope(row["scope_type"]),
                id=row["scope_id"],
                label=label or "(removed)",
            ),
            action=Action(row["action"]),
            reason=row["reason"],
            status=status,
            requires_approval=bool(row["requires_approval"]),
            permanent=bool(row["permanent"]),
            expires_at=expires,
            expiring_soon=bool(
                status == "active" and expires is not None and expires - now <= EXPIRING_SOON
            ),
            requested_at=req_dt(row["requested_at"]),
            requested_by=row["requested_by_login"],
            decided_at=dt(row["decided_at"]),
            decided_by=row["decided_by_login"],
            decision_note=row["decision_note"],
            activated_at=dt(row["activated_at"]),
            revoked_at=dt(row["revoked_at"]),
            revoked_by=row["revoked_by_login"],
            revoke_reason=row["revoke_reason"],
            expired_at=dt(row["expired_at"]),
            can_approve=can_approve,
            can_revoke=bool(
                principal is not None
                and status == "active"
                and principal.can(Permission.EXCEPTIONS_REVOKE, account_id)
            ),
            can_cancel=bool(
                principal is not None
                and status == "requested"
                and (
                    requester == principal.user_id
                    or principal.can(Permission.EXCEPTIONS_REVOKE, account_id)
                )
            ),
        )

    def _visible(self, principal: Principal, row: sqlite3.Row, visible: set[int]) -> bool:
        if row["scope_type"] != "repository":
            return True
        return row["scope_id"].isdigit() and int(row["scope_id"]) in visible

    def list(
        self,
        principal: Principal,
        account_id: int,
        *,
        status: str | None = None,
        rule_id: str | None = None,
        repository_id: int | None = None,
        group_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[PolicyExceptionView], bool]:
        require(principal, Permission.EXCEPTIONS_READ, account_id)
        clauses = ["account_id = ?"]
        params: list[object] = [account_id]
        if status is not None:
            if status not in STATUSES:
                raise InputValidationError("unknown exception status", field="status")
            clauses.append("status = ?")
            params.append(status)
        if rule_id is not None:
            if rule_id not in DEFAULT_POLICIES:
                raise InputValidationError("unknown rule", field="rule")
            clauses.append("rule_id = ?")
            params.append(rule_id)
        if repository_id is not None:
            clauses.append("scope_type = 'repository' AND scope_id = ?")
            params.append(str(repository_id))
        if group_id is not None:
            if not is_hex_id(group_id):
                raise InputValidationError("group must be a group ID", field="group")
            clauses.append("scope_type = 'group' AND scope_id = ?")
            params.append(group_id)
        rows = self._store.query(
            "SELECT * FROM policy_exceptions WHERE "  # noqa: S608 - constant clauses
            + " AND ".join(clauses)
            + " ORDER BY CASE status WHEN 'requested' THEN 0 WHEN 'active' THEN 1 ELSE 2 END, "
            "requested_at DESC LIMIT 5000",
            params,
        )
        visible = visible_repository_ids(self._store, principal, account_id)
        shown = [r for r in rows if self._visible(principal, r, visible)]
        page = shown[offset : offset + limit]
        return [self._view(r, principal) for r in page], len(shown) > offset + limit

    def _row(self, exception_id: str) -> sqlite3.Row:
        if not is_hex_id(exception_id):
            raise NotFoundError()
        rows = self._store.query(
            "SELECT * FROM policy_exceptions WHERE exception_id = ?", (exception_id,)
        )
        if not rows:
            raise NotFoundError()
        return rows[0]

    def _authorized_row(
        self, principal: Principal, exception_id: str, permission: Permission
    ) -> sqlite3.Row:
        row = self._row(exception_id)
        account_id = int(row["account_id"])
        require(principal, Permission.EXCEPTIONS_READ, account_id)
        visible = visible_repository_ids(self._store, principal, account_id)
        if not self._visible(principal, row, visible):
            raise NotFoundError()
        if not principal.can(permission, account_id):
            raise PermissionDeniedError()
        return row

    def get(self, principal: Principal, exception_id: str) -> PolicyExceptionView:
        row = self._authorized_row(principal, exception_id, Permission.EXCEPTIONS_READ)
        return self._view(row, principal)

    # -- requests --------------------------------------------------------- #
    def request(
        self,
        principal: Principal,
        account_id: int,
        *,
        rule_id: object,
        scope_type: object,
        scope_id: object,
        action: object,
        reason: object,
        expires_at: object,
        permanent: object = False,
    ) -> PolicyExceptionView:
        require(principal, Permission.EXCEPTIONS_CREATE, account_id)
        now = self._now()
        if not isinstance(rule_id, str) or rule_id not in DEFAULT_POLICIES:
            raise InputValidationError("rule_id must be a known rule", field="rule_id")
        if not isinstance(scope_type, str) or scope_type not in {s.value for s in ExceptionScope}:
            raise InputValidationError(
                "scope_type must be organization, group or repository", field="scope_type"
            )
        if action not in ("warn", "allow"):
            raise InputValidationError(
                "action must be warn or allow (an exception lowers enforcement)", field="action"
            )
        reason_text = text(reason, "reason", limit=MAX_REASON_CHARS, required=True)
        if not isinstance(permanent, bool):
            raise InputValidationError("permanent must be true or false", field="permanent")
        settings = load_settings(self._store, account_id).settings
        scope = ExceptionScope(scope_type)
        clean_scope_id = self._scope_id(principal, account_id, scope, scope_id)

        expires: datetime | None = None
        if permanent:
            if not settings.allow_permanent_exceptions:
                raise PermissionDeniedError(
                    "This organization does not allow permanent exceptions."
                )
            if not principal.can(Permission.EXCEPTIONS_APPROVE, account_id):
                raise PermissionDeniedError(
                    "Only administrators who can approve exceptions may request a permanent one."
                )
            if expires_at is not None:
                raise InputValidationError(
                    "A permanent exception has no expiry.", field="expires_at"
                )
        else:
            if not isinstance(expires_at, str):
                raise InputValidationError(
                    "expires_at is required: exceptions are temporary", field="expires_at"
                )
            expires = parse_timestamp(expires_at, "expires_at")
            if expires is None or expires <= now + timedelta(minutes=5):
                raise InputValidationError("expires_at must be in the future", field="expires_at")
            if expires > now + timedelta(days=settings.exception_max_days):
                raise InputValidationError(
                    f"An exception may last at most {settings.exception_max_days} days.",
                    field="expires_at",
                )
        severity = rule_severity(rule_id)
        requires_approval = (
            severity.rank >= settings.exception_approval_min_severity.rank
            or scope is not ExceptionScope.REPOSITORY
            or permanent
        )
        status = "requested" if requires_approval else "active"
        exception_id = new_id()
        actor = Actor.user(principal.user_id, principal.login)
        stored: list[AuditEvent] = []
        with self._store.transaction() as db:
            if db.execute(
                "SELECT 1 FROM policy_exceptions WHERE account_id = ? AND rule_id = ? "
                "AND scope_type = ? AND scope_id = ? AND status IN ('requested', 'active')",
                (account_id, rule_id, scope.value, clean_scope_id),
            ).fetchone():
                raise ConflictError(
                    "An open exception for this rule and scope already exists. Revoke or cancel "
                    "it before requesting another."
                )
            db.execute(
                "INSERT INTO policy_exceptions (exception_id, account_id, rule_id, scope_type, "
                "scope_id, action, severity, reason, status, requires_approval, permanent, "
                "expires_at, requested_at, requested_by_id, requested_by_login, activated_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    exception_id,
                    account_id,
                    rule_id,
                    scope.value,
                    clean_scope_id,
                    action,
                    severity.value,
                    reason_text,
                    status,
                    1 if requires_approval else 0,
                    1 if permanent else 0,
                    ts(expires) if expires else None,
                    ts(now),
                    principal.user_id,
                    principal.login,
                    None if requires_approval else ts(now),
                    ts(now),
                ),
            )
            label = scope_label(db, account_id, scope.value, clean_scope_id) or clean_scope_id
            repository = (
                account_repositories(db, account_id).get(int(clean_scope_id))
                if scope is ExceptionScope.REPOSITORY
                else None
            )
            stored.append(
                self._store.insert_audit_event(
                    db,
                    self._audit.build(
                        AuditEventType.EXCEPTION_REQUESTED,
                        actor=actor,
                        account_id=account_id,
                        # Repository events carry the installation too: audit visibility is
                        # scoped by (installation, repository) per session.
                        installation_id=repository.installation_id if repository else None,
                        repository_id=repository.repository_id if repository else None,
                        repository=repository.full_name if repository else None,
                        exception=exception_id,
                        rule=rule_id,
                        scope=scope.value,
                        target=label,
                        exception_action=action,
                        severity=severity.value,
                        expires_at=expires.isoformat() if expires else "permanent",
                        requires_approval=requires_approval,
                        reason=reason_text,
                    ),
                )
            )
            if requires_approval:
                emit(
                    db,
                    self._notification(
                        NotificationType.EXCEPTION_REQUESTED,
                        account_id,
                        exception_id,
                        severity=severity,
                        title=f"Exception requested: {rule_id} ({label})",
                        body=(
                            f"{principal.login} requested an exception lowering {rule_id} to "
                            f"{action} for {label} until "
                            f"{expires.date().isoformat() if expires else 'revoked (permanent)'}. "
                            f"Reason: {reason_text}. It needs approval by someone else."
                        ),
                        key_parts=(exception_id,),
                    ),
                    now,
                )
            else:
                invalidate_scope(db, account_id, scope.value, clean_scope_id, now)
        for event in stored:
            self._audit.log_stored(event)
        return self._view(self._row(exception_id), principal)

    def _scope_id(
        self, principal: Principal, account_id: int, scope: ExceptionScope, raw: object
    ) -> str:
        if scope is ExceptionScope.ORGANIZATION:
            if raw not in (None, ""):
                raise InputValidationError(
                    "an organization exception has no scope_id", field="scope_id"
                )
            return ""
        if scope is ExceptionScope.GROUP:
            if not is_hex_id(raw):
                raise InputValidationError("scope_id must be a group ID", field="scope_id")
            assert isinstance(raw, str)  # noqa: S101 - checked above
            rows = self._store.query(
                "SELECT 1 FROM repository_groups WHERE group_id = ? AND account_id = ? "
                "AND archived_at IS NULL",
                (raw, account_id),
            )
            if not rows:
                raise NotFoundError("The group was not found in this organization.")
            return raw
        if not isinstance(raw, int) or isinstance(raw, bool):
            if isinstance(raw, str) and raw.isdigit() and len(raw) < 17:
                raw = int(raw)
            else:
                raise InputValidationError("scope_id must be a repository ID", field="scope_id")
        repository_id = int(raw)
        visible = visible_repository_ids(self._store, principal, account_id)
        if repository_id not in visible or repository_id not in account_repositories(
            self._store, account_id
        ):
            raise NotFoundError("The repository was not found in this organization.")
        return str(repository_id)

    @staticmethod
    def _notification(
        notification_type: NotificationType,
        account_id: int,
        exception_id: str,
        *,
        severity: Severity,
        title: str,
        body: str,
        key_parts: tuple[str | int, ...],
    ) -> NotificationEvent:
        return NotificationEvent(
            type=notification_type,
            account_id=account_id,
            severity=severity,
            resource_type="exception",
            resource_id=exception_id,
            dedup_key=domain_key(notification_type, account_id, *key_parts),
            title=title,
            body=body,
            metadata={"exception": exception_id},
        )

    # -- decisions -------------------------------------------------------- #
    def approve(self, principal: Principal, exception_id: str, note: object) -> PolicyExceptionView:
        row = self._authorized_row(principal, exception_id, Permission.EXCEPTIONS_APPROVE)
        account_id = int(row["account_id"])
        if row["status"] != "requested":
            raise ConflictError(f"The exception is {row['status']}, not waiting for approval.")
        if row["requested_by_id"] == principal.user_id:
            raise PermissionDeniedError("An exception must be approved by someone else.")
        note_text = text(note, "note", limit=MAX_REASON_CHARS)
        now = self._now()
        expires = dt(row["expires_at"])
        if not row["permanent"] and (expires is None or expires <= now):
            raise ConflictError("The exception's expiry has passed; request a new one.")
        actor = Actor.user(principal.user_id, principal.login)
        with self._store.transaction() as db:
            changed = db.execute(
                "UPDATE policy_exceptions SET status = 'active', decided_at = ?, "
                "decided_by_id = ?, "
                "decided_by_login = ?, decision_note = ?, activated_at = ?, updated_at = ? "
                "WHERE exception_id = ? AND status = 'requested'",
                (
                    ts(now),
                    principal.user_id,
                    principal.login,
                    note_text,
                    ts(now),
                    ts(now),
                    exception_id,
                ),
            ).rowcount
            if changed != 1:
                raise ConflictError("The exception was changed by someone else.")
            label = scope_label(db, account_id, row["scope_type"], row["scope_id"]) or ""
            invalidate_scope(db, account_id, row["scope_type"], row["scope_id"], now)
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.EXCEPTION_APPROVED,
                    actor=actor,
                    account_id=account_id,
                    exception=exception_id,
                    rule=row["rule_id"],
                    scope=row["scope_type"],
                    target=label,
                    requested_by=row["requested_by_login"],
                    expires_at=expires.isoformat() if expires else "permanent",
                    note=note_text,
                ),
            )
            emit(
                db,
                self._notification(
                    NotificationType.EXCEPTION_APPROVED,
                    account_id,
                    exception_id,
                    severity=Severity(row["severity"]),
                    title=f"Exception approved: {row['rule_id']} ({label})",
                    body=(
                        f"{principal.login} approved {row['requested_by_login']}'s exception: "
                        f"{row['rule_id']} is {row['action']} for {label} until "
                        f"{expires.date().isoformat() if expires else 'revoked (permanent)'}. "
                        f"Reason: {row['reason']}"
                    ),
                    key_parts=(exception_id,),
                ),
                now,
            )
        self._audit.log_stored(stored)
        return self._view(self._row(exception_id), principal)

    def reject(self, principal: Principal, exception_id: str, note: object) -> PolicyExceptionView:
        row = self._authorized_row(principal, exception_id, Permission.EXCEPTIONS_APPROVE)
        if row["status"] != "requested":
            raise ConflictError(f"The exception is {row['status']}, not waiting for approval.")
        note_text = text(note, "note", limit=MAX_REASON_CHARS, required=True)
        return self._end(
            principal,
            row,
            status="rejected",
            from_status="requested",
            audit_type=AuditEventType.EXCEPTION_REJECTED,
            note=note_text,
        )

    def cancel(self, principal: Principal, exception_id: str) -> PolicyExceptionView:
        row = self._authorized_row(principal, exception_id, Permission.EXCEPTIONS_READ)
        account_id = int(row["account_id"])
        if row["status"] != "requested":
            raise ConflictError(
                f"The exception is {row['status']}; only requests can be cancelled."
            )
        if row["requested_by_id"] != principal.user_id and not principal.can(
            Permission.EXCEPTIONS_REVOKE, account_id
        ):
            raise PermissionDeniedError()
        return self._end(
            principal,
            row,
            status="cancelled",
            from_status="requested",
            audit_type=AuditEventType.EXCEPTION_CANCELLED,
            note=None,
        )

    def revoke(
        self, principal: Principal, exception_id: str, reason: object
    ) -> PolicyExceptionView:
        row = self._authorized_row(principal, exception_id, Permission.EXCEPTIONS_REVOKE)
        if row["status"] != "active":
            raise ConflictError(f"The exception is {row['status']}; only active ones are revoked.")
        reason_text = text(reason, "reason", limit=MAX_REASON_CHARS, required=True)
        return self._end(
            principal,
            row,
            status="revoked",
            from_status="active",
            audit_type=AuditEventType.EXCEPTION_REVOKED,
            note=reason_text,
        )

    def _end(
        self,
        principal: Principal,
        row: sqlite3.Row,
        *,
        status: str,
        from_status: str,
        audit_type: AuditEventType,
        note: str | None,
    ) -> PolicyExceptionView:
        account_id = int(row["account_id"])
        exception_id = str(row["exception_id"])
        now = self._now()
        actor = Actor.user(principal.user_id, principal.login)
        with self._store.transaction() as db:
            if status == "revoked":
                sql = (
                    "UPDATE policy_exceptions SET status = 'revoked', revoked_at = ?, "
                    "revoked_by_login = ?, revoke_reason = ?, updated_at = ? "
                    "WHERE exception_id = ? AND status = ?"
                )
            else:
                sql = (
                    "UPDATE policy_exceptions SET status = ?, decided_at = ?, "
                    "decided_by_login = ?, "
                    "decision_note = ?, updated_at = ? WHERE exception_id = ? AND status = ?"
                )
            params: tuple[object, ...] = (
                (ts(now), principal.login, note, ts(now), exception_id, from_status)
                if status == "revoked"
                else (status, ts(now), principal.login, note, ts(now), exception_id, from_status)
            )
            if db.execute(sql, params).rowcount != 1:
                raise ConflictError("The exception was changed by someone else.")
            label = scope_label(db, account_id, row["scope_type"], row["scope_id"]) or ""
            if from_status == "active":
                invalidate_scope(db, account_id, row["scope_type"], row["scope_id"], now)
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    audit_type,
                    actor=actor,
                    account_id=account_id,
                    exception=exception_id,
                    rule=row["rule_id"],
                    scope=row["scope_type"],
                    target=label,
                    note=note,
                ),
            )
            if status == "revoked":
                emit(
                    db,
                    self._notification(
                        NotificationType.EXCEPTION_ENDED,
                        account_id,
                        exception_id,
                        severity=Severity(row["severity"]),
                        title=f"Exception revoked: {row['rule_id']} ({label})",
                        body=(
                            f"{principal.login} revoked the exception for {row['rule_id']} on "
                            f"{label}. The policy applies again. Reason: {note}"
                        ),
                        key_parts=(exception_id, "ended"),
                    ),
                    now,
                )
        self._audit.log_stored(stored)
        return self._view(self._row(exception_id), principal)

    # -- background ------------------------------------------------------- #
    def expire_due(self) -> int:
        """ACTIVE -> EXPIRED for exceptions past their expiry; the policy applies again."""
        now = self._now()
        rows = self._store.query(
            "SELECT * FROM policy_exceptions WHERE status = 'active' AND permanent = 0 "
            "AND expires_at <= ? ORDER BY expires_at LIMIT ?",
            (ts(now), EXPIRY_BATCH),
        )
        expired = 0
        for row in rows:
            account_id = int(row["account_id"])
            with self._store.transaction() as db:
                if (
                    db.execute(
                        "UPDATE policy_exceptions SET status = 'expired', expired_at = ?, "
                        "updated_at = ? WHERE exception_id = ? AND status = 'active'",
                        (ts(now), ts(now), row["exception_id"]),
                    ).rowcount
                    != 1
                ):
                    continue
                label = scope_label(db, account_id, row["scope_type"], row["scope_id"]) or ""
                invalidate_scope(db, account_id, row["scope_type"], row["scope_id"], now)
                stored = self._store.insert_audit_event(
                    db,
                    self._audit.build(
                        AuditEventType.EXCEPTION_EXPIRED,
                        actor=SYSTEM_ACTOR,
                        account_id=account_id,
                        exception=row["exception_id"],
                        rule=row["rule_id"],
                        scope=row["scope_type"],
                        target=label,
                        expires_at=req_dt(row["expires_at"]).isoformat(),
                    ),
                )
                emit(
                    db,
                    self._notification(
                        NotificationType.EXCEPTION_ENDED,
                        account_id,
                        row["exception_id"],
                        severity=Severity(row["severity"]),
                        title=f"Exception expired: {row['rule_id']} ({label})",
                        body=(
                            f"The exception lowering {row['rule_id']} to {row['action']} for "
                            f"{label} expired. The policy applies again to new scans."
                        ),
                        key_parts=(row["exception_id"], "ended"),
                    ),
                    now,
                )
            self._audit.log_stored(stored)
            expired += 1
        return expired

    def warn_expiring(self) -> int:
        """One warning per configured threshold crossed; never repeated per polling cycle."""
        now = self._now()
        horizon = now + timedelta(days=90)
        rows = self._store.query(
            "SELECT * FROM policy_exceptions WHERE status = 'active' AND permanent = 0 "
            "AND expires_at > ? AND expires_at <= ? ORDER BY expires_at LIMIT ?",
            (ts(now), ts(horizon), EXPIRY_BATCH),
        )
        warned = 0
        for row in rows:
            account_id = int(row["account_id"])
            thresholds = load_settings(self._store, account_id).settings.exception_warning_days
            expires = req_dt(row["expires_at"])
            sent = {int(d) for d in json_list(row["warnings_sent"]) if isinstance(d, int)}
            crossed = [d for d in thresholds if expires - now <= timedelta(days=d)]
            pending = [d for d in crossed if d not in sent]
            if not pending:
                continue
            day = min(pending)  # the most urgent threshold crossed; larger ones are implied
            with self._store.transaction() as db:
                db.execute(
                    "UPDATE policy_exceptions SET warnings_sent = ?, updated_at = ? "
                    "WHERE exception_id = ? AND status = 'active'",
                    (json.dumps(sorted(sent | set(crossed))), ts(now), row["exception_id"]),
                )
                label = scope_label(db, account_id, row["scope_type"], row["scope_id"]) or ""
                emit(
                    db,
                    self._notification(
                        NotificationType.EXCEPTION_EXPIRING,
                        account_id,
                        row["exception_id"],
                        severity=Severity(row["severity"]),
                        title=f"Exception expires soon: {row['rule_id']} ({label})",
                        body=(
                            f"The exception lowering {row['rule_id']} to {row['action']} for "
                            f"{label} expires on {expires.isoformat(timespec='minutes')} "
                            f"(within {day} day(s)). Afterwards the policy applies again."
                        ),
                        key_parts=(row["exception_id"], "expiring", day),
                    ),
                    now,
                )
            warned += 1
        return warned

    def counts(
        self, account_id: int, repository_id: int, group_ids: Sequence[str]
    ) -> tuple[int, int]:
        """(active exceptions, expiring within 7 days) that apply to a repository."""
        now = self._now()
        grants = active_grants(self._store, account_id, repository_id, group_ids, now)
        soon = sum(
            1 for g in grants if g.expires_at is not None and g.expires_at - now <= EXPIRING_SOON
        )
        return len(grants), soon
