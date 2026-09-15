"""Organisation policy: versioned, audited, and only ever a floor.

Policy hierarchy (strongest wins, nothing can weaken a level above it)::

    service policy        COMMITGUARD_APP_MANDATORY_POLICY_FILE (operator), optional
      + organisation policy   this module, edited in the dashboard, versioned
      + repository policy     .commitguard.yaml at the trusted revision (base commit)
      = effective policy      recorded with every scan

The organisation policy is a set of *floors*: for a policy ID it may require
at least ``warn`` or ``block``. It is applied with the same code as the service
policy (:func:`commitguard.policies.mandatory.apply_mandatory_policies`), so a
repository configuration that disables or lowers the policy is still evaluated
at the floor. Removing or lowering a floor is a *weakening* change: it needs an
explicit confirmation and a recent sign-in, and is audited like any change.

Versions are immutable rows (database triggers refuse ``UPDATE`` and
``DELETE``). Every scan stores the organisation policy version and the
fingerprint of the effective policy set it was evaluated with, so a historical
result keeps showing the policy that produced it.

Version lifecycle: the newest version is ``active``; every older version is
``archived``. There are no drafts: a version exists only once it is published.
An archived version becomes the basis of enforcement again only through an
explicit **rollback**, which never edits or deletes anything - it publishes a
*new* version whose document is the restored one and records the lineage::

    v13  archived   Added bot restriction
    v14  active     rollback: restores v12 (replaced v13)

Rollback needs ``policies:rollback``, a reason, an explicit confirmation, the
version the administrator was looking at (``expected_current_version``), and -
when it weakens a floor - a recent sign-in. The target's stored fingerprint must
match its document. Version, audit event and notification are written in one
transaction: a failure leaves the active version unchanged.

Concurrency: an update or rollback names the version it was based on. If
another administrator published a newer version first, the request is rejected
with a conflict instead of silently overwriting their change.

Scans resolve the active version from the database when they start, so a
rollback applies to the next scan in every process and host, and a scan that
started before the rollback keeps reporting the version it was evaluated with.
There is no process-local policy cache to invalidate.
"""

import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from commitguard.audit.models import Actor, AuditEventType
from commitguard.config.schema import CommitGuardConfig, PolicyOverride
from commitguard.config.sources import MandatoryPolicy
from commitguard.controlplane.errors import (
    ConfirmationRequiredError,
    ConflictError,
    InputValidationError,
    PolicyIntegrityError,
    ReauthenticationRequiredError,
)
from commitguard.controlplane.results import clean_text
from commitguard.controlplane.rules import CATALOG_BY_ID
from commitguard.controlplane.views import (
    OrganizationPolicyView,
    OrganizationRef,
    PolicyAuthor,
    PolicyChange,
    PolicyDiffEntry,
    PolicyDiffView,
    PolicyRuleView,
    PolicyVersionView,
)
from commitguard.core.decision import Action
from commitguard.core.result import Severity
from commitguard.github.storage import SqliteStateStore
from commitguard.notifications.deduplication import domain_key
from commitguard.notifications.models import NotificationEvent, NotificationType
from commitguard.notifications.outbox import emit
from commitguard.observability.metrics import (
    POLICY_ROLLBACK_FAILURES,
    POLICY_ROLLBACKS,
    Metrics,
    NullMetrics,
)
from commitguard.policies.defaults import DEFAULT_POLICIES
from commitguard.security.hashing import sha256_hex
from commitguard.services.audit import AuditService

MAX_REASON_CHARS = 500
#: Weakening changes require a sign-in no older than this.
REAUTHENTICATION_WINDOW = timedelta(minutes=15)
FLOOR_ACTIONS = (Action.WARN, Action.BLOCK)


@dataclass(frozen=True, slots=True)
class PolicyVersion:
    account_id: int
    version: int  # 0: no organisation policy has been saved
    floors: Mapping[str, Action]
    fingerprint: str | None
    created_at: datetime | None
    created_by_id: int | None
    created_by_login: str | None
    reason: str | None
    kind: str = "change"  # change | rollback
    rollback_of: int | None = None  # the version that was active when rolling back
    restored_version: int | None = None  # the version whose document was restored
    document: str | None = None


def _canonical(floors: Mapping[str, Action]) -> str:
    return json.dumps({k: floors[k].value for k in sorted(floors)}, separators=(",", ":"))


def validate_floors(raw: object) -> dict[str, Action]:
    if not isinstance(raw, Mapping):
        raise InputValidationError("floors must be an object of policy IDs", field="floors")
    floors: dict[str, Action] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or key not in DEFAULT_POLICIES:
            raise InputValidationError("unknown policy ID in floors", field="floors")
        if value is None:
            continue  # no floor: the repository decides
        if not isinstance(value, str) or value not in (a.value for a in FLOOR_ACTIONS):
            raise InputValidationError(
                f"the floor for {key} must be warn, block or null", field=f"floors.{key}"
            )
        floors[key] = Action(value)
    return floors


def _change_summary(changes: list[PolicyChange]) -> str:
    return "; ".join(
        f"{c.policy_id}: {c.old.value if c.old else 'repository'} -> "
        f"{c.new.value if c.new else 'repository'}"
        for c in changes
    )


def policy_diff(
    from_version: int,
    old: Mapping[str, Action],
    to_version: int,
    new: Mapping[str, Action],
) -> PolicyDiffView:
    """A structured diff of two versions' floors: added, changed, removed."""
    added, changed, removed = [], [], []
    for change in policy_changes(old, new):
        if change.old is None:
            added.append(PolicyDiffEntry(policy_id=change.policy_id, new=change.new))
        elif change.new is None:
            removed.append(
                PolicyDiffEntry(policy_id=change.policy_id, old=change.old, weakening=True)
            )
        else:
            changed.append(
                PolicyDiffEntry(
                    policy_id=change.policy_id,
                    old=change.old,
                    new=change.new,
                    weakening=change.weakening,
                )
            )
    return PolicyDiffView(
        from_version=from_version,
        to_version=to_version,
        added=tuple(added),
        changed=tuple(changed),
        removed=tuple(removed),
        weakening=any(e.weakening for e in (*changed, *removed)),
    )


def policy_changes(old: Mapping[str, Action], new: Mapping[str, Action]) -> list[PolicyChange]:
    changes = []
    for policy_id in sorted(set(old) | set(new)):
        before, after = old.get(policy_id), new.get(policy_id)
        if before == after:
            continue
        weakening = before is not None and (after is None or after.rank < before.rank)
        changes.append(
            PolicyChange(policy_id=policy_id, old=before, new=after, weakening=weakening)
        )
    return changes


class OrganizationPolicyService:
    def __init__(
        self,
        store: SqliteStateStore,
        audit: AuditService,
        *,
        service_policy: MandatoryPolicy | None = None,
        metrics: Metrics | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._audit = audit
        self._service_policy = service_policy
        self._metrics: Metrics = metrics or NullMetrics()
        self._now = now

    # -- reads ----------------------------------------------------------- #
    def current(self, account_id: int) -> PolicyVersion:
        rows = self._store.query(
            "SELECT * FROM organization_policy_versions WHERE account_id = ? "
            "ORDER BY version DESC LIMIT 1",
            (int(account_id),),
        )
        if not rows:
            return PolicyVersion(account_id, 0, {}, None, None, None, None, None)
        return self._version(rows[0])

    def version(self, account_id: int, version: int) -> PolicyVersion | None:
        rows = self._store.query(
            "SELECT * FROM organization_policy_versions WHERE account_id = ? AND version = ?",
            (int(account_id), int(version)),
        )
        return self._version(rows[0]) if rows else None

    def versions(self, account_id: int, *, offset: int, limit: int) -> list[PolicyVersionView]:
        """Newest first. Each version is summarised against the version before it."""
        rows = self._store.query(
            "SELECT * FROM organization_policy_versions WHERE account_id = ? "
            "ORDER BY version DESC LIMIT ? OFFSET ?",
            (int(account_id), int(limit) + 1, int(offset)),
        )
        versions = [self._version(row) for row in rows]
        latest = self.current(account_id).version
        views = []
        for index, version in enumerate(versions[:limit]):
            if index + 1 < len(versions):
                previous: Mapping[str, Action] = versions[index + 1].floors
            elif version.version > 1:
                before = self.version(account_id, version.version - 1)
                previous = before.floors if before else {}
            else:
                previous = {}
            views.append(
                self.version_view(version, previous=previous, active=version.version == latest)
            )
        return views

    @staticmethod
    def _version(row: sqlite3.Row) -> PolicyVersion:
        document = json.loads(str(row["document"]))
        return PolicyVersion(
            account_id=int(row["account_id"]),
            version=int(row["version"]),
            floors={k: Action(v) for k, v in document.items()},
            fingerprint=str(row["fingerprint"]),
            created_at=datetime.fromtimestamp(float(str(row["created_at"])), UTC),
            created_by_id=int(str(row["created_by_id"]))
            if row["created_by_id"] is not None
            else None,
            created_by_login=str(row["created_by_login"]) if row["created_by_login"] else None,
            reason=str(row["reason"]) if row["reason"] else None,
            kind=str(row["kind"] or "change"),
            rollback_of=int(row["rollback_of"]) if row["rollback_of"] is not None else None,
            restored_version=int(row["restored_version"])
            if row["restored_version"] is not None
            else None,
            document=str(row["document"]),
        )

    def version_view(
        self,
        version: PolicyVersion,
        *,
        previous: Mapping[str, Action] | None = None,
        active: bool | None = None,
    ) -> PolicyVersionView:
        if previous is None:
            before = (
                self.version(version.account_id, version.version - 1)
                if version.version > 1
                else None
            )
            previous = before.floors if before else {}
        if active is None:
            active = self.current(version.account_id).version == version.version
        changes = policy_changes(previous, version.floors)
        return PolicyVersionView(
            version=version.version,
            fingerprint=version.fingerprint or "",
            floors=dict(version.floors),
            created_at=version.created_at or datetime.fromtimestamp(0, UTC),
            created_by=PolicyAuthor(id=version.created_by_id, login=version.created_by_login),
            reason=version.reason,
            status="active" if active else "archived",
            kind=version.kind,  # type: ignore[arg-type]
            rollback_of=version.rollback_of,
            restored_version=version.restored_version,
            changes=tuple(changes),
            summary=_change_summary(changes) or "No floor changes",
        )

    def diff(self, account_id: int, from_version: int, to_version: int) -> PolicyDiffView | None:
        """Diff two versions (version 0 is "no organization policy")."""
        old = self.version(account_id, from_version) if from_version else None
        new = self.version(account_id, to_version) if to_version else None
        if (from_version and old is None) or (to_version and new is None):
            return None
        return policy_diff(
            from_version, old.floors if old else {}, to_version, new.floors if new else {}
        )

    def service_floors(self) -> dict[str, Action]:
        if self._service_policy is None:
            return {}
        return {
            policy_id: override.action or DEFAULT_POLICIES[policy_id].action
            for policy_id, override in self._service_policy.config.policies.items()
        }

    def view(self, organization: OrganizationRef, *, can_write: bool) -> OrganizationPolicyView:
        current = self.current(organization.id)
        service = self.service_floors()
        rules = []
        for policy_id, policy in DEFAULT_POLICIES.items():
            org_floor = current.floors.get(policy_id)
            service_floor = service.get(policy_id)
            floors = [a for a in (org_floor, service_floor) if a is not None]
            minimum = Action.most_restrictive(floors) if floors else None
            if minimum is None:
                source = "built_in_default"
            elif (
                service_floor is not None and service_floor.rank >= (org_floor or Action.ALLOW).rank
            ):
                source = "service_policy"
            else:
                source = "organization_policy"
            entry = CATALOG_BY_ID.get(policy_id)
            rules.append(
                PolicyRuleView(
                    policy_id=policy_id,
                    name=entry.name if entry else policy_id,
                    description=policy.description,
                    default_action=policy.action,
                    service_floor=service_floor,
                    organization_floor=org_floor,
                    minimum_action=minimum,
                    repository_override="any" if minimum is None else "stricter_only",
                    source=source,  # type: ignore[arg-type]
                )
            )
        return OrganizationPolicyView(
            organization=organization,
            version=current.version,
            fingerprint=current.fingerprint,
            updated_at=current.created_at,
            updated_by=PolicyAuthor(id=current.created_by_id, login=current.created_by_login)
            if current.version
            else None,
            reason=current.reason,
            service_policy=self._service_policy.description if self._service_policy else None,
            rules=tuple(rules),
            can_write=can_write,
        )

    # -- writes ---------------------------------------------------------- #
    def update(
        self,
        *,
        account_id: int,
        actor: Actor,
        authenticated_at: datetime,
        expected_version: int,
        floors: Mapping[str, Action],
        reason: str | None,
        confirm_weakening: bool,
    ) -> tuple[PolicyVersion, list[PolicyChange]]:
        now = self._now()
        reason_text = (
            clean_text(reason.strip(), MAX_REASON_CHARS) if reason and reason.strip() else None
        )
        current = self.current(account_id)
        if current.version != expected_version:
            raise ConflictError(
                f"The policy was changed by someone else (now version {current.version}). "
                "Reload to see the latest version before saving."
            )
        changes = policy_changes(current.floors, floors)
        if not changes:
            raise InputValidationError("The new policy is identical to the current version.")
        weakening = [c for c in changes if c.weakening]
        if weakening and not confirm_weakening:
            raise ConfirmationRequiredError(
                "This change weakens enforcement and must be confirmed explicitly."
            )
        if weakening and now - authenticated_at > REAUTHENTICATION_WINDOW:
            raise ReauthenticationRequiredError()
        if weakening and reason_text is None:
            raise InputValidationError(
                "A reason is required when weakening enforcement.", field="reason"
            )
        document = _canonical(floors)
        event = self._audit.build(
            AuditEventType.ORGANIZATION_POLICY_CHANGED,
            actor=actor,
            account_id=account_id,
            old_version=current.version,
            new_version=current.version + 1,
            changes=_change_summary(changes),
            weakening=bool(weakening),
            reason=reason_text,
        )
        with self._store.transaction() as db:
            row = db.execute(
                "SELECT MAX(version) AS latest FROM organization_policy_versions "
                "WHERE account_id = ?",
                (int(account_id),),
            ).fetchone()
            latest = int(row["latest"] or 0)
            if latest != expected_version:
                raise ConflictError(
                    f"The policy was changed by someone else (now version {latest}). "
                    "Reload to see the latest version before saving."
                )
            db.execute(
                "INSERT INTO organization_policy_versions (account_id, version, document, "
                "fingerprint, created_at, created_by_id, created_by_login, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(account_id),
                    latest + 1,
                    document,
                    sha256_hex(document.encode("utf-8")),
                    now.timestamp(),
                    actor.id,
                    actor.login,
                    reason_text,
                ),
            )
            stored = self._store.insert_audit_event(db, event)
            emit(
                db,
                self._policy_notification(
                    NotificationType.POLICY_CHANGED,
                    account_id=account_id,
                    new_version=latest + 1,
                    title=f"Organization policy changed: v{latest} → v{latest + 1}",
                    body=(
                        f"{actor.login or 'An administrator'} published organization policy "
                        f"v{latest + 1}. Changes: {_change_summary(changes)}."
                        + (" This change weakens enforcement." if weakening else "")
                        + (f" Reason: {reason_text}" if reason_text else "")
                    ),
                    weakening=bool(weakening),
                    metadata={
                        "previous_version": latest,
                        "new_version": latest + 1,
                        "changed_by": actor.login,
                        "changes": _change_summary(changes),
                        "weakening": bool(weakening),
                        "reason": reason_text,
                    },
                ),
                now,
            )
        self._audit.log_stored(stored)
        updated = self.version(account_id, latest + 1)
        assert updated is not None  # noqa: S101 - written in the transaction above
        return updated, changes

    @staticmethod
    def _policy_notification(
        notification_type: NotificationType,
        *,
        account_id: int,
        new_version: int,
        title: str,
        body: str,
        weakening: bool,
        metadata: dict[str, str | int | bool | None],
    ) -> NotificationEvent:
        return NotificationEvent(
            type=notification_type,
            account_id=account_id,
            severity=Severity.CRITICAL if weakening else Severity.HIGH,
            resource_type="policy",
            resource_id=str(account_id),
            dedup_key=domain_key(notification_type, account_id, new_version),
            title=title,
            body=body,
            metadata=metadata,
        )

    def rollback(
        self,
        *,
        account_id: int,
        actor: Actor,
        authenticated_at: datetime,
        target_version: int,
        expected_current_version: int,
        reason: str | None,
        confirm: bool,
    ) -> tuple[PolicyVersion, PolicyDiffView]:
        """Publish a new version restoring ``target_version``'s document. Atomic."""
        try:
            return self._rollback(
                account_id=account_id,
                actor=actor,
                authenticated_at=authenticated_at,
                target_version=target_version,
                expected_current_version=expected_current_version,
                reason=reason,
                confirm=confirm,
            )
        except Exception:
            self._metrics.increment(POLICY_ROLLBACK_FAILURES)
            raise

    def _rollback(
        self,
        *,
        account_id: int,
        actor: Actor,
        authenticated_at: datetime,
        target_version: int,
        expected_current_version: int,
        reason: str | None,
        confirm: bool,
    ) -> tuple[PolicyVersion, PolicyDiffView]:
        now = self._now()
        reason_text = (
            clean_text(reason.strip(), MAX_REASON_CHARS) if reason and reason.strip() else None
        )
        if reason_text is None:
            raise InputValidationError("A reason is required to roll back policy.", field="reason")
        current = self.current(account_id)
        if current.version != expected_current_version:
            raise ConflictError(
                f"The policy was changed by someone else (now version {current.version}). "
                "Reload to see the latest version before rolling back."
            )
        if not 1 <= target_version < current.version:
            raise InputValidationError(
                "The target must be an earlier version of this organization's policy.",
                field="target_version",
            )
        target = self.version(account_id, target_version)
        if target is None:
            raise InputValidationError(
                f"Version {target_version} does not exist.", field="target_version"
            )
        self._verify_integrity(target)
        diff = policy_diff(current.version, current.floors, target_version, target.floors)
        if not (diff.added or diff.changed or diff.removed):
            raise InputValidationError(
                f"Version {target_version} is identical to the active version.",
                field="target_version",
            )
        if not confirm:
            raise ConfirmationRequiredError(
                "Rolling back changes the effective security policy and must be confirmed."
            )
        if diff.weakening and now - authenticated_at > REAUTHENTICATION_WINDOW:
            raise ReauthenticationRequiredError()
        assert target.document is not None  # noqa: S101 - read from the database
        changes = policy_changes(current.floors, target.floors)
        summary = _change_summary(changes)
        with self._store.transaction() as db:
            row = db.execute(
                "SELECT MAX(version) AS latest FROM organization_policy_versions "
                "WHERE account_id = ?",
                (int(account_id),),
            ).fetchone()
            latest = int(row["latest"] or 0)
            if latest != expected_current_version:
                raise ConflictError(
                    f"The policy was changed by someone else (now version {latest}). "
                    "Reload to see the latest version before rolling back."
                )
            new_version = latest + 1
            db.execute(
                "INSERT INTO organization_policy_versions (account_id, version, document, "
                "fingerprint, created_at, created_by_id, created_by_login, reason, kind, "
                "rollback_of, restored_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'rollback', ?, ?)",
                (
                    int(account_id),
                    new_version,
                    target.document,
                    sha256_hex(target.document.encode("utf-8")),
                    now.timestamp(),
                    actor.id,
                    actor.login,
                    reason_text,
                    latest,
                    target_version,
                ),
            )
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.ORGANIZATION_POLICY_ROLLED_BACK,
                    actor=actor,
                    account_id=account_id,
                    previous_version=latest,
                    target_version=target_version,
                    new_version=new_version,
                    changes=summary,
                    weakening=diff.weakening,
                    reason=reason_text,
                ),
            )
            emit(
                db,
                self._policy_notification(
                    NotificationType.POLICY_ROLLED_BACK,
                    account_id=account_id,
                    new_version=new_version,
                    title=(
                        f"Organization policy rolled back: v{latest} → v{new_version} "
                        f"(restores v{target_version})"
                    ),
                    body=(
                        f"{actor.login or 'An administrator'} rolled back the organization "
                        f"policy. Previous: v{latest}. Restored: v{target_version}. Effective: "
                        f"v{new_version}. Changes: {summary}. Reason: {reason_text}"
                    ),
                    weakening=diff.weakening,
                    metadata={
                        "previous_version": latest,
                        "target_version": target_version,
                        "new_version": new_version,
                        "changed_by": actor.login,
                        "changes": summary,
                        "weakening": diff.weakening,
                        "reason": reason_text,
                    },
                ),
                now,
            )
        self._audit.log_stored(stored)
        self._metrics.increment(POLICY_ROLLBACKS)
        created = self.version(account_id, new_version)
        assert created is not None  # noqa: S101 - written in the transaction above
        return created, diff

    @staticmethod
    def _verify_integrity(version: PolicyVersion) -> None:
        document = version.document or ""
        if sha256_hex(document.encode("utf-8")) != version.fingerprint:
            raise PolicyIntegrityError(
                f"Version {version.version} failed its integrity check and cannot be restored."
            )
        try:
            floors = validate_floors(json.loads(document))
        except (ValueError, InputValidationError):
            raise PolicyIntegrityError(
                f"Version {version.version} is not a valid policy and cannot be restored."
            ) from None
        if _canonical(floors) != document:
            raise PolicyIntegrityError(
                f"Version {version.version} is not in canonical form and cannot be restored."
            )

    # -- enforcement ----------------------------------------------------- #
    def mandatory_for_installation(
        self, installation_id: int
    ) -> tuple[MandatoryPolicy | None, int | None]:
        """The combined floor (service + organisation) a scan must be evaluated with."""
        rows = self._store.query(
            "SELECT account_id FROM installations WHERE installation_id = ?",
            (int(installation_id),),
        )
        organization = self.current(int(rows[0]["account_id"])) if rows else None
        org_floors = dict(organization.floors) if organization else {}
        service = self.service_floors()
        combined = {
            policy_id: Action.most_restrictive(
                [a for a in (org_floors.get(policy_id), service.get(policy_id)) if a is not None]
            )
            for policy_id in sorted(set(org_floors) | set(service))
        }
        version = organization.version if organization and organization.version else None
        if not combined:
            return None, version
        parts = []
        if self._service_policy is not None:
            parts.append(f"service policy {self._service_policy.description}")
        if version:
            parts.append(f"organization policy v{version}")
        config = CommitGuardConfig(
            version=1,
            policies={pid: PolicyOverride(action=action) for pid, action in combined.items()},
        )
        return (
            MandatoryPolicy(
                config=config,
                description=" + ".join(parts),
                fingerprint=sha256_hex(_canonical(combined).encode("utf-8")),
            ),
            version,
        )
