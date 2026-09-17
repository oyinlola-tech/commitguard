"""Policy workflow: draft -> simulate -> review -> approve -> publish.

::

    draft ──submit──► pending_approval ──approve──► approved ──publish──► published
      │                    │                           │                     │
      │                    └──reject──► rejected       └──edit──► draft      └─ immutable
      └──cancel──► cancelled                                                    version

A draft holds a *proposed* document for one policy target (organization,
repository group or repository). Nothing about it is enforced: the effective
policy changes only when a draft is published, which writes an immutable
version through :class:`~commitguard.controlplane.policies.OrganizationPolicyService`
(the Phase 6/7 code path, with its confirmation, re-authentication, conflict
and rollback rules).

Whether approval is required is an organization setting. With
``require_policy_approval`` off, an administrator with ``policies:publish`` can
publish a draft directly (and the Phase 6 "save policy" endpoint keeps working).
With it on, a draft must be approved first, and direct saves are refused with
``APPROVAL_REQUIRED``.

Separation of duties (``require_separate_approver``, on by default): the person
who created or submitted a draft cannot approve it. Approval binds to the
draft's document fingerprint - editing an approved draft cancels the approval,
so no one can get one document approved and publish another.

Emergency publication (``policies:emergency``, owners) skips the approval
workflow. It requires a reason, is recorded on the version itself, and produces
a critical audit event and notification: it is never silent.
"""

import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from commitguard.audit.models import Actor, AuditEventType
from commitguard.controlplane.access import Permission, Principal
from commitguard.controlplane.errors import (
    ApprovalRequiredError,
    ConflictError,
    InputValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from commitguard.controlplane.policies import (
    MAX_DOCUMENT_BYTES,
    ORGANIZATION_TARGET,
    OrganizationPolicyService,
    PolicyTarget,
    PolicyTargetType,
    PublishedPolicy,
    canonical_document,
    change_summary,
    parse_document,
    policy_changes,
    policy_diff,
    target_label,
    validate_defaults,
    validate_floors,
)
from commitguard.controlplane.views import PolicyChange, PolicyDiffView, PolicyTargetView
from commitguard.core.decision import Action
from commitguard.core.result import Severity
from commitguard.github.storage import SqliteStateStore
from commitguard.governance.common import (
    MAX_NAME_CHARS,
    MAX_REASON_CHARS,
    dt,
    is_hex_id,
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
from commitguard.security.hashing import sha256_hex
from commitguard.services.audit import AuditService

DRAFT_STATES = ("draft", "pending_approval", "approved", "rejected", "cancelled", "published")
OPEN_DRAFT_STATES = ("draft", "pending_approval", "approved")
MAX_OPEN_DRAFTS = 50


class PolicyApprovalView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    status: str
    requested_by: str | None
    requested_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    reason: str | None


class PolicyDraftView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    organization_id: int
    target: PolicyTargetView
    title: str
    reason: str | None
    state: str
    revision: int
    base_version: int
    current_version: int  # the target's published version right now
    floors: dict[str, Action]
    defaults: dict[str, Action]
    changes: tuple[PolicyChange, ...]
    diff: PolicyDiffView
    weakening: bool
    rebase_required: bool
    requires_approval: bool
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    submitted_by: str | None
    submitted_at: datetime | None
    published_version: int | None
    published_at: datetime | None
    published_by: str | None
    emergency: bool
    rollout_id: str | None
    approvals: tuple[PolicyApprovalView, ...]
    can_edit: bool
    can_submit: bool
    can_approve: bool
    can_publish: bool
    can_cancel: bool
    can_emergency_publish: bool


def parse_target(target_type: object, target_id: object) -> PolicyTarget:
    if not isinstance(target_type, str) or target_type not in {t.value for t in PolicyTargetType}:
        raise InputValidationError(
            "target_type must be organization, group or repository", field="target_type"
        )
    kind = PolicyTargetType(target_type)
    if kind is PolicyTargetType.ORGANIZATION:
        if target_id not in (None, ""):
            raise InputValidationError("an organization policy has no target_id", field="target_id")
        return ORGANIZATION_TARGET
    if kind is PolicyTargetType.GROUP:
        if not is_hex_id(target_id):
            raise InputValidationError("target_id must be a group ID", field="target_id")
        assert isinstance(target_id, str)  # noqa: S101 - checked above
        return PolicyTarget.group(target_id)
    if isinstance(target_id, bool) or not isinstance(target_id, int):
        if isinstance(target_id, str) and target_id.isdigit() and len(target_id) < 17:
            target_id = int(target_id)
        else:
            raise InputValidationError("target_id must be a repository ID", field="target_id")
    return PolicyTarget.repository(int(target_id))


def parse_rules(body: Mapping[str, Any]) -> tuple[dict[str, Action], dict[str, Action]]:
    """``{"floors": {...}, "defaults": {...}}`` -> mandatory and default entries."""
    floors = validate_floors(body.get("floors") or {})
    defaults = validate_defaults(body.get("defaults") or {})
    overlap = sorted(set(floors) & set(defaults))
    if overlap:
        raise InputValidationError(
            f"a rule is either mandatory or a default, not both: {', '.join(overlap)}",
            field="defaults",
        )
    if not floors and not defaults:
        raise InputValidationError("a policy must set at least one rule", field="floors")
    return floors, defaults


class PolicyWorkflowService:
    def __init__(
        self,
        store: SqliteStateStore,
        audit: AuditService,
        policies: OrganizationPolicyService,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._audit = audit
        self._policies = policies
        self._now = now

    # -- helpers ---------------------------------------------------------- #
    def _target_of(self, row: sqlite3.Row) -> PolicyTarget:
        return PolicyTarget(PolicyTargetType(row["target_type"]), str(row["target_id"]))

    def _label(self, account_id: int, target: PolicyTarget) -> str:
        with self._store.transaction() as db:
            label = target_label(db, account_id, target)
        if label is None:
            raise NotFoundError()
        return label

    def _require_target_access(
        self, principal: Principal, account_id: int, target: PolicyTarget
    ) -> str:
        label = self._label(account_id, target)
        if target.type is PolicyTargetType.REPOSITORY:
            visible = visible_repository_ids(self._store, principal, account_id)
            if int(target.id) not in visible:
                raise NotFoundError()
        return label

    def _draft_row(self, draft_id: str) -> sqlite3.Row:
        if not is_hex_id(draft_id):
            raise NotFoundError()
        rows = self._store.query("SELECT * FROM policy_drafts WHERE draft_id = ?", (draft_id,))
        if not rows:
            raise NotFoundError()
        return rows[0]

    def _authorized_draft(
        self, principal: Principal, draft_id: str, permission: Permission
    ) -> sqlite3.Row:
        row = self._draft_row(draft_id)
        account_id = int(row["account_id"])
        require(principal, Permission.POLICIES_READ, account_id)
        self._require_target_access(principal, account_id, self._target_of(row))
        if not principal.can(permission, account_id):
            raise PermissionDeniedError()
        return row

    def authorize(self, principal: Principal, draft_id: str, permission: Permission) -> int:
        """The draft's organization after checking ``permission`` (404 across tenants)."""
        return int(self._authorized_draft(principal, draft_id, permission)["account_id"])

    def _view(self, row: sqlite3.Row, principal: Principal) -> PolicyDraftView:
        account_id = int(row["account_id"])
        target = self._target_of(row)
        floors, defaults = parse_document(str(row["document"]))
        current = self._policies.current(account_id, target)
        state = str(row["state"])
        published_version = row["published_version"]
        if state == "published" and published_version is not None:
            # What this draft changed when it was published, not against today's policy.
            before = (
                self._policies.version(account_id, int(published_version) - 1, target)
                if int(published_version) > 1
                else None
            )
            old_version, new_version = int(published_version) - 1, int(published_version)
            old_floors = before.floors if before else {}
            old_defaults = before.defaults if before else {}
        else:
            old_version, new_version = current.version, current.version + 1
            old_floors, old_defaults = current.floors, current.defaults
        changes = policy_changes(old_floors, floors, old_defaults, defaults)
        diff = policy_diff(old_version, old_floors, new_version, floors, old_defaults, defaults)
        settings = load_settings(self._store, account_id).settings
        approvals = tuple(
            PolicyApprovalView(
                id=a["approval_id"],
                status=a["status"],
                requested_by=a["requested_by_login"],
                requested_at=req_dt(a["requested_at"]),
                decided_by=a["decided_by_login"],
                decided_at=dt(a["decided_at"]),
                reason=a["reason"],
            )
            for a in self._store.query(
                "SELECT * FROM policy_approvals WHERE draft_id = ? ORDER BY requested_at DESC",
                (row["draft_id"],),
            )
        )
        author = row["created_by_id"] in (principal.user_id,) or row["submitted_by_id"] in (
            principal.user_id,
        )
        can_approve = (
            state == "pending_approval"
            and principal.can(Permission.POLICIES_APPROVE, account_id)
            and not (settings.require_separate_approver and author)
        )
        return PolicyDraftView(
            id=row["draft_id"],
            organization_id=account_id,
            target=PolicyTargetView(
                type=target.type.value,
                id=target.id,
                label=self._label(account_id, target),
            ),
            title=row["title"],
            reason=row["reason"],
            state=state,
            revision=int(row["revision"]),
            base_version=int(row["base_version"]),
            current_version=current.version,
            floors=floors,
            defaults=defaults,
            changes=tuple(changes),
            diff=diff,
            weakening=any(c.weakening for c in changes),
            rebase_required=state != "published" and current.version != int(row["base_version"]),
            requires_approval=settings.require_policy_approval,
            created_by=row["created_by_login"],
            created_at=req_dt(row["created_at"]),
            updated_at=req_dt(row["updated_at"]),
            submitted_by=row["submitted_by_login"],
            submitted_at=dt(row["submitted_at"]),
            published_version=row["published_version"],
            published_at=dt(row["published_at"]),
            published_by=row["published_by_login"],
            emergency=bool(row["emergency"]),
            rollout_id=row["rollout_id"],
            approvals=approvals,
            can_edit=state in ("draft", "approved", "rejected")
            and principal.can(Permission.POLICIES_WRITE, account_id),
            can_submit=state == "draft" and principal.can(Permission.POLICIES_WRITE, account_id),
            can_approve=can_approve,
            can_publish=state in ("draft", "approved")
            and principal.can(Permission.POLICIES_PUBLISH, account_id)
            and (state == "approved" or not settings.require_policy_approval),
            can_cancel=state in ("draft", "pending_approval", "approved", "rejected")
            and principal.can(Permission.POLICIES_WRITE, account_id),
            can_emergency_publish=state in ("draft", "pending_approval", "approved")
            and principal.can(Permission.POLICIES_EMERGENCY, account_id),
        )

    # -- reads ------------------------------------------------------------ #
    def list_drafts(
        self, principal: Principal, account_id: int, *, state: str | None = None
    ) -> list[PolicyDraftView]:
        require(principal, Permission.POLICIES_READ, account_id)
        if state is not None and state not in DRAFT_STATES:
            raise InputValidationError("unknown draft state", field="state")
        sql = "SELECT * FROM policy_drafts WHERE account_id = ?"
        params: list[object] = [account_id]
        if state is not None:
            sql += " AND state = ?"
            params.append(state)
        rows = self._store.query(sql + " ORDER BY updated_at DESC LIMIT 200", params)
        views = []
        visible = visible_repository_ids(self._store, principal, account_id)
        for row in rows:
            target = self._target_of(row)
            if target.type is PolicyTargetType.REPOSITORY and int(target.id) not in visible:
                continue
            views.append(self._view(row, principal))
        return views

    def get(self, principal: Principal, draft_id: str) -> PolicyDraftView:
        row = self._authorized_draft(principal, draft_id, Permission.POLICIES_READ)
        return self._view(row, principal)

    # -- writes ----------------------------------------------------------- #
    def create(
        self,
        principal: Principal,
        account_id: int,
        *,
        target_type: object,
        target_id: object,
        floors: dict[str, Action],
        defaults: dict[str, Action],
        title: object,
        reason: object,
    ) -> PolicyDraftView:
        require(principal, Permission.POLICIES_WRITE, account_id)
        target = parse_target(target_type, target_id)
        label = self._require_target_access(principal, account_id, target)
        clean_title = text(title, "title", limit=MAX_NAME_CHARS) or f"{label} policy change"
        clean_reason = text(reason, "reason", limit=MAX_REASON_CHARS)
        document = canonical_document(floors, defaults)
        if len(document.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise InputValidationError("The policy document is too large.")
        current = self._policies.current(account_id, target)
        now = self._now()
        draft_id = new_id()
        with self._store.transaction() as db:
            open_drafts = db.execute(
                "SELECT COUNT(*) AS n FROM policy_drafts WHERE account_id = ? AND state IN "
                "('draft', 'pending_approval', 'approved')",
                (account_id,),
            ).fetchone()["n"]
            if int(open_drafts) >= MAX_OPEN_DRAFTS:
                raise ConflictError(
                    f"There are already {MAX_OPEN_DRAFTS} open policy drafts. "
                    "Publish or cancel some before creating another."
                )
            db.execute(
                "INSERT INTO policy_drafts (draft_id, account_id, target_type, target_id, "
                "base_version, document, fingerprint, title, reason, state, created_at, "
                "created_by_id, created_by_login, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)",
                (
                    draft_id,
                    account_id,
                    target.type.value,
                    target.id,
                    current.version,
                    document,
                    sha256_hex(document.encode("utf-8")),
                    clean_title,
                    clean_reason,
                    ts(now),
                    principal.user_id,
                    principal.login,
                    ts(now),
                ),
            )
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.POLICY_DRAFT_CREATED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=account_id,
                    draft=draft_id,
                    target_type=target.type.value,
                    target=label,
                    base_version=current.version,
                ),
            )
        self._audit.log_stored(stored)
        return self.get(principal, draft_id)

    def update(
        self,
        principal: Principal,
        draft_id: str,
        *,
        expected_revision: object,
        floors: dict[str, Action] | None,
        defaults: dict[str, Action] | None,
        title: object,
        reason: object,
        rebase: bool = False,
    ) -> PolicyDraftView:
        """Edit a draft. ``rebase`` bases it on the target's current version."""
        row = self._authorized_draft(principal, draft_id, Permission.POLICIES_WRITE)
        account_id = int(row["account_id"])
        state = str(row["state"])
        if state not in ("draft", "approved", "rejected"):
            raise ConflictError(f"A {state} draft cannot be edited.")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise InputValidationError(
                "expected_revision must be an integer", field="expected_revision"
            )
        current_floors, current_defaults = parse_document(str(row["document"]))
        document = canonical_document(
            current_floors if floors is None else floors,
            current_defaults if defaults is None else defaults,
        )
        if len(document.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise InputValidationError("The policy document is too large.")
        clean_title = text(title, "title", limit=MAX_NAME_CHARS) or row["title"]
        clean_reason = (
            text(reason, "reason", limit=MAX_REASON_CHARS) if reason is not None else row["reason"]
        )
        base_version = int(row["base_version"])
        if rebase:
            base_version = self._policies.current(account_id, self._target_of(row)).version
        now = self._now()
        with self._store.transaction() as db:
            changed = db.execute(
                "UPDATE policy_drafts SET document = ?, fingerprint = ?, title = ?, reason = ?, "
                "base_version = ?, state = 'draft', revision = revision + 1, updated_at = ? "
                "WHERE draft_id = ? AND revision = ? AND state IN ('draft', 'approved', "
                "'rejected')",
                (
                    document,
                    sha256_hex(document.encode("utf-8")),
                    clean_title,
                    clean_reason,
                    base_version,
                    ts(now),
                    draft_id,
                    expected_revision,
                ),
            ).rowcount
            if changed != 1:
                raise ConflictError("The draft was changed by someone else. Reload before editing.")
            # An edited draft loses its approval: what was approved is no longer what would
            # be published.
            db.execute(
                "UPDATE policy_approvals SET status = 'cancelled', decided_at = ?, "
                "decided_by_login = ?, reason = 'draft edited after approval' "
                "WHERE draft_id = ? AND status IN ('pending', 'approved')",
                (ts(now), principal.login, draft_id),
            )
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.POLICY_DRAFT_UPDATED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=account_id,
                    draft=draft_id,
                    revision=int(row["revision"]) + 1,
                    base_version=base_version,
                    rebased=base_version != int(row["base_version"]),
                ),
            )
        self._audit.log_stored(stored)
        return self.get(principal, draft_id)

    def submit(self, principal: Principal, draft_id: str) -> PolicyDraftView:
        row = self._authorized_draft(principal, draft_id, Permission.POLICIES_WRITE)
        account_id = int(row["account_id"])
        if str(row["state"]) != "draft":
            raise ConflictError(f"The draft is {row['state']}, not a draft.")
        now = self._now()
        target = self._target_of(row)
        label = self._label(account_id, target)
        approval_id = new_id()
        with self._store.transaction() as db:
            if (
                db.execute(
                    "UPDATE policy_drafts SET state = 'pending_approval', submitted_at = ?, "
                    "submitted_by_id = ?, submitted_by_login = ?, updated_at = ? "
                    "WHERE draft_id = ? AND state = 'draft'",
                    (ts(now), principal.user_id, principal.login, ts(now), draft_id),
                ).rowcount
                != 1
            ):
                raise ConflictError("The draft was changed by someone else.")
            db.execute(
                "INSERT INTO policy_approvals (approval_id, draft_id, account_id, fingerprint, "
                "requested_by_id, requested_by_login, requested_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
                (
                    approval_id,
                    draft_id,
                    account_id,
                    row["fingerprint"],
                    principal.user_id,
                    principal.login,
                    ts(now),
                ),
            )
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.POLICY_APPROVAL_REQUESTED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=account_id,
                    draft=draft_id,
                    approval=approval_id,
                    target=label,
                    title=str(row["title"]),
                ),
            )
            emit(
                db,
                NotificationEvent(
                    type=NotificationType.POLICY_APPROVAL_REQUESTED,
                    account_id=account_id,
                    severity=Severity.HIGH,
                    resource_type="draft",
                    resource_id=draft_id,
                    dedup_key=domain_key(
                        NotificationType.POLICY_APPROVAL_REQUESTED, account_id, approval_id
                    ),
                    title=f"Policy approval requested: {row['title']}",
                    body=(
                        f"{principal.login} submitted a policy change for {label} for approval. "
                        "It is not in effect until an administrator approves and publishes it."
                    ),
                    metadata={"draft": draft_id, "target": label},
                ),
                now,
            )
        self._audit.log_stored(stored)
        return self.get(principal, draft_id)

    def decide(
        self, principal: Principal, draft_id: str, *, approve: bool, reason: object
    ) -> PolicyDraftView:
        row = self._authorized_draft(principal, draft_id, Permission.POLICIES_APPROVE)
        account_id = int(row["account_id"])
        if str(row["state"]) != "pending_approval":
            raise ConflictError(f"The draft is {row['state']}, not waiting for approval.")
        settings = load_settings(self._store, account_id).settings
        if settings.require_separate_approver and principal.user_id in {
            row["created_by_id"],
            row["submitted_by_id"],
        }:
            raise PermissionDeniedError(
                "Separation of duties: the author of a policy change cannot approve it."
            )
        note = text(reason, "reason", limit=MAX_REASON_CHARS, required=not approve)
        now = self._now()
        with self._store.transaction() as db:
            approval = db.execute(
                "SELECT approval_id, fingerprint FROM policy_approvals WHERE draft_id = ? "
                "AND status = 'pending'",
                (draft_id,),
            ).fetchone()
            if approval is None:
                raise ConflictError("There is no approval request for this draft.")
            if approval["fingerprint"] != row["fingerprint"]:
                raise ConflictError("The draft changed after it was submitted; submit it again.")
            db.execute(
                "UPDATE policy_approvals SET status = ?, decided_by_id = ?, decided_by_login = ?, "
                "decided_at = ?, reason = ? WHERE approval_id = ? AND status = 'pending'",
                (
                    "approved" if approve else "rejected",
                    principal.user_id,
                    principal.login,
                    ts(now),
                    note,
                    approval["approval_id"],
                ),
            )
            db.execute(
                "UPDATE policy_drafts SET state = ?, updated_at = ? WHERE draft_id = ? "
                "AND state = 'pending_approval'",
                ("approved" if approve else "rejected", ts(now), draft_id),
            )
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.POLICY_APPROVED if approve else AuditEventType.POLICY_REJECTED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=account_id,
                    draft=draft_id,
                    approval=str(approval["approval_id"]),
                    requested_by=row["submitted_by_login"],
                    reason=note,
                ),
            )
        self._audit.log_stored(stored)
        return self.get(principal, draft_id)

    def cancel(self, principal: Principal, draft_id: str) -> PolicyDraftView:
        row = self._authorized_draft(principal, draft_id, Permission.POLICIES_WRITE)
        account_id = int(row["account_id"])
        if str(row["state"]) in ("published", "cancelled"):
            raise ConflictError(f"The draft is {row['state']}.")
        now = self._now()
        with self._store.transaction() as db:
            db.execute(
                "UPDATE policy_drafts SET state = 'cancelled', updated_at = ? WHERE draft_id = ? "
                "AND state != 'published'",
                (ts(now), draft_id),
            )
            db.execute(
                "UPDATE policy_approvals SET status = 'cancelled', decided_at = ?, "
                "decided_by_login = ?, reason = 'draft cancelled' WHERE draft_id = ? "
                "AND status = 'pending'",
                (ts(now), principal.login, draft_id),
            )
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.POLICY_DRAFT_CANCELLED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=account_id,
                    draft=draft_id,
                ),
            )
        self._audit.log_stored(stored)
        return self.get(principal, draft_id)

    def publish(
        self,
        principal: Principal,
        draft_id: str,
        *,
        confirm_weakening: bool,
        emergency: bool = False,
        reason: object = None,
        rollout: Callable[[sqlite3.Connection, PublishedPolicy], None] | None = None,
    ) -> PolicyDraftView:
        """Publish a draft as an immutable version. Optionally starts a staged rollout."""
        permission = Permission.POLICIES_EMERGENCY if emergency else Permission.POLICIES_PUBLISH
        row = self._authorized_draft(principal, draft_id, permission)
        account_id = int(row["account_id"])
        state = str(row["state"])
        if state in ("published", "cancelled", "rejected"):
            raise ConflictError(f"A {state} draft cannot be published.")
        settings = load_settings(self._store, account_id).settings
        if settings.require_policy_approval and state != "approved" and not emergency:
            raise ApprovalRequiredError(
                "This organization requires policy changes to be approved before they are "
                "published. Submit the draft for approval, or use an emergency publication."
            )
        publish_reason = text(reason, "reason", limit=MAX_REASON_CHARS, required=emergency)
        target = self._target_of(row)
        floors, defaults = parse_document(str(row["document"]))
        now = self._now()

        def mark_published(db: sqlite3.Connection, published: PublishedPolicy) -> None:
            db.execute(
                "UPDATE policy_drafts SET state = 'published', published_version = ?, "
                "published_at = ?, published_by_login = ?, emergency = ?, updated_at = ? "
                "WHERE draft_id = ?",
                (
                    published.version,
                    ts(now),
                    principal.login,
                    1 if emergency else 0,
                    ts(now),
                    draft_id,
                ),
            )
            if rollout is not None:
                rollout(db, published)
                db.execute(
                    "UPDATE policy_drafts SET rollout_id = (SELECT rollout_id FROM "
                    "policy_rollouts WHERE account_id = ? AND target_type = ? AND target_id = ? "
                    "AND to_version = ?) WHERE draft_id = ?",
                    (account_id, target.type.value, target.id, published.version, draft_id),
                )

        self._policies.update(
            account_id=account_id,
            actor=Actor.user(principal.user_id, principal.login),
            authenticated_at=principal.authenticated_at,
            expected_version=int(row["base_version"]),
            floors=floors,
            defaults=defaults,
            reason=publish_reason or row["reason"],
            confirm_weakening=confirm_weakening,
            target=target,
            draft_id=draft_id,
            emergency=emergency,
            hooks=[mark_published],
        )
        return self.get(principal, draft_id)

    def summarize(self, account_id: int, target: PolicyTarget, document: str) -> str:
        current = self._policies.current(account_id, target)
        floors, defaults = parse_document(document)
        return change_summary(policy_changes(current.floors, floors, current.defaults, defaults))
