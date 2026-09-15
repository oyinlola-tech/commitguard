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

Versions are immutable rows. Every scan stores the organisation policy version
and the fingerprint of the effective policy set it was evaluated with, so a
historical result keeps showing the policy that produced it. Rolling back is
creating a new version with an earlier document (the history supports it;
there is no rollback button yet).

Concurrency: an update names the version it was based on. If another
administrator saved a newer version first, the update is rejected with a
conflict instead of silently overwriting their change.
"""

import json
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
    ReauthenticationRequiredError,
)
from commitguard.controlplane.results import clean_text
from commitguard.controlplane.rules import CATALOG_BY_ID
from commitguard.controlplane.views import (
    OrganizationPolicyView,
    OrganizationRef,
    PolicyAuthor,
    PolicyChange,
    PolicyRuleView,
    PolicyVersionView,
)
from commitguard.core.decision import Action
from commitguard.github.storage import SqliteStateStore
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
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._audit = audit
        self._service_policy = service_policy
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
        rows = self._store.query(
            "SELECT * FROM organization_policy_versions WHERE account_id = ? "
            "ORDER BY version DESC LIMIT ? OFFSET ?",
            (int(account_id), int(limit), int(offset)),
        )
        return [self.version_view(self._version(row)) for row in rows]

    @staticmethod
    def _version(row: Mapping[str, object]) -> PolicyVersion:
        document = json.loads(str(row["document"]))
        return PolicyVersion(
            account_id=int(str(row["account_id"])),
            version=int(str(row["version"])),
            floors={k: Action(v) for k, v in document.items()},
            fingerprint=str(row["fingerprint"]),
            created_at=datetime.fromtimestamp(float(str(row["created_at"])), UTC),
            created_by_id=int(str(row["created_by_id"]))
            if row["created_by_id"] is not None
            else None,
            created_by_login=str(row["created_by_login"]) if row["created_by_login"] else None,
            reason=str(row["reason"]) if row["reason"] else None,
        )

    @staticmethod
    def version_view(version: PolicyVersion) -> PolicyVersionView:
        return PolicyVersionView(
            version=version.version,
            fingerprint=version.fingerprint or "",
            floors=dict(version.floors),
            created_at=version.created_at or datetime.fromtimestamp(0, UTC),
            created_by=PolicyAuthor(id=version.created_by_id, login=version.created_by_login),
            reason=version.reason,
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
            changes="; ".join(
                f"{c.policy_id}: {c.old.value if c.old else 'repository'} -> "
                f"{c.new.value if c.new else 'repository'}"
                for c in changes
            ),
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
        self._audit.log_stored(stored)
        updated = self.version(account_id, latest + 1)
        assert updated is not None  # noqa: S101 - written in the transaction above
        return updated, changes

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
