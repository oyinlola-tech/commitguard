"""Versioned policy for organizations, repository groups and repositories.

Policy hierarchy (see :mod:`commitguard.policies.governance` for the exact
per-rule precedence)::

    service policy        COMMITGUARD_APP_MANDATORY_POLICY_FILE (operator), optional
      + organization policy   versioned in the dashboard (this module)
      + repository group policy  versioned, per group (this module)
      + repository policy     versioned, per repository in the dashboard (this module)
      + repository configuration  .commitguard.yaml at the trusted revision (base commit)
      + approved exceptions, monitor mode
      = effective policy      resolved per repository, recorded with every scan

A policy version is a set of entries, one per rule, each with a **strength**:

* ``mandatory`` (``warn`` or ``block``): a *floor*. It is applied with the same
  code as the service policy (:func:`commitguard.policies.mandatory.apply_mandatory_policies`),
  so a repository configuration - or a narrower dashboard policy - that disables
  or lowers the rule is still evaluated at the floor, and the attempt is shown
  as a policy conflict. Removing or lowering a floor is a *weakening* change: it
  needs an explicit confirmation and a recent sign-in, and is audited like any change.
* ``default`` (``allow``, ``warn`` or ``block``): a baseline that narrower
  levels and the repository configuration may replace in either direction.

The stored document is canonical JSON: a mandatory entry is its action
(``{"ai_coauthor": "block"}``, the Phase 6 format, so existing versions keep
their fingerprints); a default entry is ``{"action": "warn", "enforcement": "default"}``.

Versions are immutable rows (database triggers refuse ``UPDATE`` and
``DELETE``). The organization's versions live in ``organization_policy_versions``,
group and repository versions in ``scoped_policy_versions``. Every scan stores the
policy versions and the fingerprint of the effective policy set it was evaluated
with, so a historical result keeps showing the policy that produced it.

Version lifecycle: the newest version of a target is its latest *published*
version; older versions are ``archived``. Proposed changes live in drafts
(:mod:`commitguard.governance.workflow`) until they are published. A staged
rollout (:mod:`commitguard.governance.rollouts`) may apply the newest version to
pilot repositories first; everything else keeps the previous version until the
rollout expands. An archived version becomes the basis of enforcement again only
through an explicit **rollback**, which never edits or deletes anything - it
publishes a *new* version whose document is the restored one and records the
lineage::

    v13  archived   Added bot restriction
    v14  active     rollback: restores v12 (replaced v13)

Rollback needs ``policies:rollback``, a reason, an explicit confirmation, the
version the administrator was looking at (``expected_current_version``), and -
when it weakens a floor - a recent sign-in. The target's stored fingerprint must
match its document. Version, audit event, notification and the invalidation of
affected repositories' effective policies are written in one transaction: a
failure leaves the active version unchanged.

Concurrency: an update or rollback names the version it was based on. If
another administrator published a newer version first, the request is rejected
with a conflict instead of silently overwriting their change.

Scans resolve policy from the database when they start (through the
governance resolver's cache, which every publication invalidates in the same
transaction), so a change applies to the next scan in every process and host,
and a scan that started before the change keeps reporting the version it was
evaluated with.
"""

import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from commitguard.audit.models import Actor, AuditEventType
from commitguard.config.schema import CommitGuardConfig, PolicyOverride
from commitguard.config.sources import MandatoryPolicy
from commitguard.controlplane.errors import (
    ConfirmationRequiredError,
    ConflictError,
    InputValidationError,
    NotFoundError,
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
    PolicyTargetView,
    PolicyVersionView,
    ScopedPolicyView,
    ScopedRuleView,
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
from commitguard.policies.governance import Enforcement, RuleRequirement
from commitguard.security.hashing import sha256_hex
from commitguard.services.audit import AuditService

MAX_REASON_CHARS = 500
#: Weakening changes require a sign-in no older than this.
REAUTHENTICATION_WINDOW = timedelta(minutes=15)
FLOOR_ACTIONS = (Action.WARN, Action.BLOCK)
#: Largest accepted policy document (resource exhaustion protection).
MAX_DOCUMENT_BYTES = 16_384


class PolicyTargetType(StrEnum):
    ORGANIZATION = "organization"
    GROUP = "group"
    REPOSITORY = "repository"


@dataclass(frozen=True, slots=True)
class PolicyTarget:
    type: PolicyTargetType
    id: str = ""  # "" for the organization; a group ID or a repository ID

    @property
    def scoped(self) -> bool:
        return self.type is not PolicyTargetType.ORGANIZATION

    @classmethod
    def group(cls, group_id: str) -> "PolicyTarget":
        return cls(PolicyTargetType.GROUP, group_id)

    @classmethod
    def repository(cls, repository_id: int) -> "PolicyTarget":
        return cls(PolicyTargetType.REPOSITORY, str(int(repository_id)))


ORGANIZATION_TARGET = PolicyTarget(PolicyTargetType.ORGANIZATION)


@dataclass(frozen=True, slots=True)
class PolicyVersion:
    account_id: int
    version: int  # 0: no policy has been saved for this target
    floors: Mapping[str, Action]  # mandatory entries
    fingerprint: str | None
    created_at: datetime | None
    created_by_id: int | None
    created_by_login: str | None
    reason: str | None
    kind: str = "change"  # change | rollback
    rollback_of: int | None = None  # the version that was active when rolling back
    restored_version: int | None = None  # the version whose document was restored
    document: str | None = None
    defaults: Mapping[str, Action] = field(default_factory=dict)  # default entries
    target: PolicyTarget = ORGANIZATION_TARGET
    draft_id: str | None = None
    emergency: bool = False

    @property
    def requirements(self) -> dict[str, RuleRequirement]:
        entries = {
            rule: RuleRequirement(action=action, enforcement=Enforcement.DEFAULT)
            for rule, action in self.defaults.items()
        }
        entries.update(
            {rule: RuleRequirement(action=action) for rule, action in self.floors.items()}
        )
        return entries


@dataclass(frozen=True, slots=True)
class PublishedPolicy:
    """Passed to publish hooks, inside the publishing transaction."""

    account_id: int
    target: PolicyTarget
    version: int
    previous_version: int
    kind: str
    now: datetime


type PublishHook = Callable[[sqlite3.Connection, PublishedPolicy], None]


def canonical_document(floors: Mapping[str, Action], defaults: Mapping[str, Action]) -> str:
    overlap = sorted(set(floors) & set(defaults))
    if overlap:
        raise InputValidationError(
            f"a rule is either mandatory or a default, not both: {', '.join(overlap)}",
            field="defaults",
        )
    document: dict[str, object] = {k: floors[k].value for k in floors}
    document.update({k: {"action": defaults[k].value, "enforcement": "default"} for k in defaults})
    return json.dumps({k: document[k] for k in sorted(document)}, separators=(",", ":"))


# Phase 7 name, kept for callers that only deal with floors.
def _canonical(floors: Mapping[str, Action]) -> str:
    return canonical_document(floors, {})


def parse_document(document: str) -> tuple[dict[str, Action], dict[str, Action]]:
    """Split a stored document into (mandatory floors, defaults). Raises ``ValueError``."""
    raw = json.loads(document)
    if not isinstance(raw, dict):
        raise ValueError("policy document must be an object")
    floors: dict[str, Action] = {}
    defaults: dict[str, Action] = {}
    for key, value in raw.items():
        if key not in DEFAULT_POLICIES:
            raise ValueError("unknown policy ID")
        if isinstance(value, str):
            action = Action(value)
            if action not in FLOOR_ACTIONS:
                raise ValueError("a mandatory entry must be warn or block")
            floors[key] = action
        elif isinstance(value, dict) and set(value) == {"action", "enforcement"}:
            if value["enforcement"] != "default" or not isinstance(value["action"], str):
                raise ValueError("invalid default entry")
            defaults[key] = Action(value["action"])
        else:
            raise ValueError("invalid policy entry")
    return floors, defaults


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


def validate_defaults(raw: object) -> dict[str, Action]:
    if not isinstance(raw, Mapping):
        raise InputValidationError("defaults must be an object of policy IDs", field="defaults")
    defaults: dict[str, Action] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or key not in DEFAULT_POLICIES:
            raise InputValidationError("unknown policy ID in defaults", field="defaults")
        if value is None:
            continue
        if not isinstance(value, str) or value not in (a.value for a in Action):
            raise InputValidationError(
                f"the default for {key} must be allow, warn, block or null",
                field=f"defaults.{key}",
            )
        defaults[key] = Action(value)
    return defaults


def _label(change: PolicyChange) -> str:
    suffix = " (default)" if change.enforcement == "default" else ""
    return f"{change.policy_id}{suffix}"


def change_summary(changes: Sequence[PolicyChange]) -> str:
    return "; ".join(
        f"{_label(c)}: {c.old.value if c.old else 'repository'} -> "
        f"{c.new.value if c.new else 'repository'}"
        for c in changes
    )


_change_summary = change_summary


def policy_changes(
    old: Mapping[str, Action],
    new: Mapping[str, Action],
    old_defaults: Mapping[str, Action] | None = None,
    new_defaults: Mapping[str, Action] | None = None,
) -> list[PolicyChange]:
    changes = []
    for policy_id in sorted(set(old) | set(new)):
        before, after = old.get(policy_id), new.get(policy_id)
        if before == after:
            continue
        weakening = before is not None and (after is None or after.rank < before.rank)
        changes.append(
            PolicyChange(policy_id=policy_id, old=before, new=after, weakening=weakening)
        )
    old_defaults = old_defaults or {}
    new_defaults = new_defaults or {}
    for policy_id in sorted(set(old_defaults) | set(new_defaults)):
        before, after = old_defaults.get(policy_id), new_defaults.get(policy_id)
        if before == after:
            continue
        builtin = DEFAULT_POLICIES[policy_id].action
        # Weaker when the baseline repositories fall back to is lower than before.
        weakening = (after or builtin).rank < (before or builtin).rank
        changes.append(
            PolicyChange(
                policy_id=policy_id,
                old=before,
                new=after,
                weakening=weakening,
                enforcement="default",
            )
        )
    return changes


def policy_diff(
    from_version: int,
    old: Mapping[str, Action],
    to_version: int,
    new: Mapping[str, Action],
    old_defaults: Mapping[str, Action] | None = None,
    new_defaults: Mapping[str, Action] | None = None,
) -> PolicyDiffView:
    """A structured diff of two versions: added, changed, removed entries."""
    added, changed, removed = [], [], []
    for change in policy_changes(old, new, old_defaults, new_defaults):
        entry = PolicyDiffEntry(
            policy_id=change.policy_id,
            old=change.old,
            new=change.new,
            weakening=change.weakening
            if change.old is not None or change.enforcement == "default"
            else False,
            enforcement=change.enforcement,
        )
        if change.old is None:
            added.append(entry)
        elif change.new is None:
            removed.append(
                entry.model_copy(update={"weakening": True})
                if change.enforcement == "mandatory"
                else entry
            )
        else:
            changed.append(entry)
    return PolicyDiffView(
        from_version=from_version,
        to_version=to_version,
        added=tuple(added),
        changed=tuple(changed),
        removed=tuple(removed),
        weakening=any(e.weakening for e in (*added, *changed, *removed)),
    )


# Static statements per storage table: no SQL is assembled from input.
_ORG_LATEST = (
    "SELECT * FROM organization_policy_versions WHERE account_id = ? ORDER BY version DESC LIMIT 1"
)
_ORG_VERSION = "SELECT * FROM organization_policy_versions WHERE account_id = ? AND version = ?"
_ORG_PAGE = (
    "SELECT * FROM organization_policy_versions WHERE account_id = ? "
    "ORDER BY version DESC LIMIT ? OFFSET ?"
)
_ORG_MAX = "SELECT MAX(version) AS latest FROM organization_policy_versions WHERE account_id = ?"
_ORG_INSERT = (
    "INSERT INTO organization_policy_versions (account_id, version, document, fingerprint, "
    "created_at, created_by_id, created_by_login, reason, kind, rollback_of, restored_version, "
    "draft_id, emergency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_SCOPED_LATEST = (
    "SELECT * FROM scoped_policy_versions WHERE account_id = ? AND target_type = ? "
    "AND target_id = ? ORDER BY version DESC LIMIT 1"
)
_SCOPED_VERSION = (
    "SELECT * FROM scoped_policy_versions WHERE account_id = ? AND target_type = ? "
    "AND target_id = ? AND version = ?"
)
_SCOPED_PAGE = (
    "SELECT * FROM scoped_policy_versions WHERE account_id = ? AND target_type = ? "
    "AND target_id = ? ORDER BY version DESC LIMIT ? OFFSET ?"
)
_SCOPED_MAX = (
    "SELECT MAX(version) AS latest FROM scoped_policy_versions WHERE account_id = ? "
    "AND target_type = ? AND target_id = ?"
)
_SCOPED_INSERT = (
    "INSERT INTO scoped_policy_versions (account_id, target_type, target_id, version, document, "
    "fingerprint, created_at, created_by_id, created_by_login, reason, kind, rollback_of, "
    "restored_version, draft_id, emergency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def target_label(db: sqlite3.Connection, account_id: int, target: PolicyTarget) -> str | None:
    """A display label for ``target`` within the account, or None if it is not the account's."""
    if target.type is PolicyTargetType.ORGANIZATION:
        return "Organization"
    if target.type is PolicyTargetType.GROUP:
        row = db.execute(
            "SELECT name FROM repository_groups WHERE group_id = ? AND account_id = ? "
            "AND archived_at IS NULL",
            (target.id, int(account_id)),
        ).fetchone()
        return f"Group {row['name']}" if row else None
    if not target.id.isdigit():
        return None
    row = db.execute(
        "SELECT k.owner, k.name FROM known_repositories k "
        "JOIN installations i ON i.installation_id = k.installation_id "
        "WHERE i.account_id = ? AND k.repository_id = ? ORDER BY k.last_seen_at DESC LIMIT 1",
        (int(account_id), int(target.id)),
    ).fetchone()
    return f"{row['owner']}/{row['name']}" if row else None


class OrganizationPolicyService:
    """Versions, publication and rollback of policy for any target of an account."""

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
        self._publish_hooks: list[PublishHook] = []

    @property
    def service_policy(self) -> MandatoryPolicy | None:
        return self._service_policy

    def add_publish_hook(self, hook: PublishHook) -> None:
        """Run ``hook`` inside every publishing transaction (e.g. cache invalidation)."""
        self._publish_hooks.append(hook)

    # -- storage --------------------------------------------------------- #
    def _latest_rows(self, account_id: int, target: PolicyTarget) -> list[sqlite3.Row]:
        if target.scoped:
            return self._store.query(
                _SCOPED_LATEST, (int(account_id), target.type.value, target.id)
            )
        return self._store.query(_ORG_LATEST, (int(account_id),))

    def _version_rows(
        self, account_id: int, target: PolicyTarget, version: int
    ) -> list[sqlite3.Row]:
        if target.scoped:
            return self._store.query(
                _SCOPED_VERSION, (int(account_id), target.type.value, target.id, int(version))
            )
        return self._store.query(_ORG_VERSION, (int(account_id), int(version)))

    @staticmethod
    def _max_version(db: sqlite3.Connection, account_id: int, target: PolicyTarget) -> int:
        if target.scoped:
            row = db.execute(
                _SCOPED_MAX, (int(account_id), target.type.value, target.id)
            ).fetchone()
        else:
            row = db.execute(_ORG_MAX, (int(account_id),)).fetchone()
        return int(row["latest"] or 0)

    @staticmethod
    def _insert_version(
        db: sqlite3.Connection,
        *,
        account_id: int,
        target: PolicyTarget,
        version: int,
        document: str,
        now: datetime,
        actor: Actor,
        reason: str | None,
        kind: str = "change",
        rollback_of: int | None = None,
        restored_version: int | None = None,
        draft_id: str | None = None,
        emergency: bool = False,
    ) -> None:
        values = (
            version,
            document,
            sha256_hex(document.encode("utf-8")),
            now.timestamp(),
            actor.id,
            actor.login,
            reason,
            kind,
            rollback_of,
            restored_version,
            draft_id,
            1 if emergency else 0,
        )
        if target.scoped:
            db.execute(_SCOPED_INSERT, (int(account_id), target.type.value, target.id, *values))
        else:
            db.execute(_ORG_INSERT, (int(account_id), *values))

    # -- reads ----------------------------------------------------------- #
    def current(self, account_id: int, target: PolicyTarget = ORGANIZATION_TARGET) -> PolicyVersion:
        rows = self._latest_rows(account_id, target)
        if not rows:
            return PolicyVersion(account_id, 0, {}, None, None, None, None, None, target=target)
        return self._version(rows[0], target)

    def version(
        self, account_id: int, version: int, target: PolicyTarget = ORGANIZATION_TARGET
    ) -> PolicyVersion | None:
        rows = self._version_rows(account_id, target, version)
        return self._version(rows[0], target) if rows else None

    def versions(
        self,
        account_id: int,
        *,
        offset: int,
        limit: int,
        target: PolicyTarget = ORGANIZATION_TARGET,
    ) -> list[PolicyVersionView]:
        """Newest first. Each version is summarised against the version before it."""
        if target.scoped:
            rows = self._store.query(
                _SCOPED_PAGE,
                (int(account_id), target.type.value, target.id, int(limit) + 1, int(offset)),
            )
        else:
            rows = self._store.query(_ORG_PAGE, (int(account_id), int(limit) + 1, int(offset)))
        versions = [self._version(row, target) for row in rows]
        latest = self.current(account_id, target).version
        views = []
        for index, version in enumerate(versions[:limit]):
            if index + 1 < len(versions):
                previous: PolicyVersion | None = versions[index + 1]
            elif version.version > 1:
                previous = self.version(account_id, version.version - 1, target)
            else:
                previous = None
            views.append(
                self.version_view(
                    version,
                    previous=previous.floors if previous else {},
                    previous_defaults=previous.defaults if previous else {},
                    active=version.version == latest,
                )
            )
        return views

    @staticmethod
    def _version(row: sqlite3.Row, target: PolicyTarget = ORGANIZATION_TARGET) -> PolicyVersion:
        try:
            floors, defaults = parse_document(str(row["document"]))
        except (ValueError, KeyError):
            floors, defaults = {}, {}
        keys = row.keys()
        return PolicyVersion(
            account_id=int(row["account_id"]),
            version=int(row["version"]),
            floors=floors,
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
            defaults=defaults,
            target=target,
            draft_id=str(row["draft_id"]) if "draft_id" in keys and row["draft_id"] else None,
            emergency=bool(row["emergency"]) if "emergency" in keys else False,
        )

    def version_view(
        self,
        version: PolicyVersion,
        *,
        previous: Mapping[str, Action] | None = None,
        previous_defaults: Mapping[str, Action] | None = None,
        active: bool | None = None,
    ) -> PolicyVersionView:
        if previous is None:
            before = (
                self.version(version.account_id, version.version - 1, version.target)
                if version.version > 1
                else None
            )
            previous = before.floors if before else {}
            previous_defaults = before.defaults if before else {}
        if active is None:
            active = self.current(version.account_id, version.target).version == version.version
        changes = policy_changes(
            previous, version.floors, previous_defaults or {}, version.defaults
        )
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
            summary=change_summary(changes) or "No policy changes",
            defaults=dict(version.defaults),
            draft_id=version.draft_id,
            emergency=version.emergency,
        )

    def diff(
        self,
        account_id: int,
        from_version: int,
        to_version: int,
        target: PolicyTarget = ORGANIZATION_TARGET,
    ) -> PolicyDiffView | None:
        """Diff two versions (version 0 is "no policy")."""
        old = self.version(account_id, from_version, target) if from_version else None
        new = self.version(account_id, to_version, target) if to_version else None
        if (from_version and old is None) or (to_version and new is None):
            return None
        return policy_diff(
            from_version,
            old.floors if old else {},
            to_version,
            new.floors if new else {},
            old.defaults if old else {},
            new.defaults if new else {},
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
            org_default = current.defaults.get(policy_id)
            source: Literal[
                "built_in_default", "service_policy", "organization_policy", "organization_default"
            ]
            if minimum is None:
                source = "organization_default" if org_default else "built_in_default"
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
                    source=source,
                    organization_default=org_default,
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

    def repository_targets(self, account_id: int) -> list[int]:
        """Repositories of the account that have a dashboard repository policy."""
        return [
            int(r["target_id"])
            for r in self._store.query(
                "SELECT DISTINCT target_id FROM scoped_policy_versions WHERE account_id = ? "
                "AND target_type = 'repository'",
                (int(account_id),),
            )
            if str(r["target_id"]).isdigit()
        ]

    def target_label(self, account_id: int, target: PolicyTarget) -> str | None:
        with self._store.transaction() as db:
            return target_label(db, account_id, target)

    def scoped_view(
        self, organization: OrganizationRef, target: PolicyTarget, *, can_write: bool
    ) -> ScopedPolicyView:
        label = self.target_label(organization.id, target)
        if label is None:
            raise NotFoundError()
        current = self.current(organization.id, target)
        rules = tuple(
            ScopedRuleView(
                policy_id=policy_id,
                name=CATALOG_BY_ID[policy_id].name if policy_id in CATALOG_BY_ID else policy_id,
                mandatory=current.floors.get(policy_id),
                default=current.defaults.get(policy_id),
            )
            for policy_id in DEFAULT_POLICIES
        )
        return ScopedPolicyView(
            organization=organization,
            target=PolicyTargetView(type=target.type.value, id=target.id, label=label),
            version=current.version,
            fingerprint=current.fingerprint,
            updated_at=current.created_at,
            updated_by=PolicyAuthor(id=current.created_by_id, login=current.created_by_login)
            if current.version
            else None,
            reason=current.reason,
            rules=rules,
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
        defaults: Mapping[str, Action] | None = None,
        target: PolicyTarget = ORGANIZATION_TARGET,
        draft_id: str | None = None,
        emergency: bool = False,
        hooks: Sequence[PublishHook] = (),
    ) -> tuple[PolicyVersion, list[PolicyChange]]:
        """Publish a new version of ``target``'s policy.

        ``defaults=None`` keeps the current default entries (the Phase 6 API only
        sends floors). The caller has already applied the organization's approval
        requirements: this method publishes.
        """
        now = self._now()
        reason_text = (
            clean_text(reason.strip(), MAX_REASON_CHARS) if reason and reason.strip() else None
        )
        current = self.current(account_id, target)
        if current.version != expected_version:
            raise ConflictError(
                f"The policy was changed by someone else (now version {current.version}). "
                "Reload to see the latest version before saving."
            )
        if defaults is None:
            # Floors-only callers keep the defaults; a rule that became mandatory
            # is no longer also a default.
            new_defaults = {k: v for k, v in current.defaults.items() if k not in floors}
        else:
            new_defaults = dict(defaults)
        changes = policy_changes(current.floors, floors, current.defaults, new_defaults)
        if not changes:
            raise InputValidationError("The new policy is identical to the current version.")
        weakening = [c for c in changes if c.weakening]
        if weakening and not confirm_weakening:
            raise ConfirmationRequiredError(
                "This change weakens enforcement and must be confirmed explicitly."
            )
        if weakening and now - authenticated_at > REAUTHENTICATION_WINDOW:
            raise ReauthenticationRequiredError()
        if (weakening or emergency) and reason_text is None:
            raise InputValidationError(
                "A reason is required when weakening enforcement or publishing in an emergency.",
                field="reason",
            )
        document = canonical_document(floors, new_defaults)
        if len(document.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise InputValidationError("The policy document is too large.")
        summary = change_summary(changes)
        with self._store.transaction() as db:
            label = target_label(db, account_id, target)
            if label is None:
                raise NotFoundError()
            latest = self._max_version(db, account_id, target)
            if latest != expected_version:
                raise ConflictError(
                    f"The policy was changed by someone else (now version {latest}). "
                    "Reload to see the latest version before saving."
                )
            self._insert_version(
                db,
                account_id=account_id,
                target=target,
                version=latest + 1,
                document=document,
                now=now,
                actor=actor,
                reason=reason_text,
                draft_id=draft_id,
                emergency=emergency,
            )
            target_data = self._target_data(target, label)
            stored = [
                self._store.insert_audit_event(
                    db,
                    self._audit.build(
                        AuditEventType.POLICY_PUBLISHED
                        if target.scoped
                        else AuditEventType.ORGANIZATION_POLICY_CHANGED,
                        actor=actor,
                        account_id=account_id,
                        old_version=latest,
                        new_version=latest + 1,
                        changes=summary,
                        weakening=bool(weakening),
                        reason=reason_text,
                        extra={**target_data, **({"draft": draft_id} if draft_id else {})},
                    ),
                )
            ]
            title_label = "Organization policy" if not target.scoped else f"{label} policy"
            emit(
                db,
                self._policy_notification(
                    NotificationType.POLICY_CHANGED,
                    account_id=account_id,
                    new_version=latest + 1,
                    title=f"{title_label} changed: v{latest} → v{latest + 1}",
                    body=(
                        f"{actor.login or 'An administrator'} published "
                        f"{title_label.lower()} v{latest + 1}. Changes: {summary}."
                        + (" This change weakens enforcement." if weakening else "")
                        + (f" Reason: {reason_text}" if reason_text else "")
                    ),
                    weakening=bool(weakening),
                    target=target,
                    metadata={
                        "previous_version": latest,
                        "new_version": latest + 1,
                        "changed_by": actor.login,
                        "changes": summary,
                        "weakening": bool(weakening),
                        "reason": reason_text,
                        **target_data,
                    },
                ),
                now,
            )
            if emergency:
                stored.append(
                    self._store.insert_audit_event(
                        db,
                        self._audit.build(
                            AuditEventType.POLICY_EMERGENCY_PUBLISHED,
                            actor=actor,
                            account_id=account_id,
                            new_version=latest + 1,
                            changes=summary,
                            reason=reason_text,
                            extra=target_data,
                        ),
                    )
                )
                emit(
                    db,
                    NotificationEvent(
                        type=NotificationType.POLICY_EMERGENCY_PUBLISHED,
                        account_id=account_id,
                        severity=Severity.CRITICAL,
                        resource_type="policy",
                        resource_id=str(account_id),
                        dedup_key=domain_key(
                            NotificationType.POLICY_EMERGENCY_PUBLISHED,
                            account_id,
                            target.type.value,
                            target.id or "-",
                            latest + 1,
                        ),
                        title=f"Emergency policy publication: {title_label} v{latest + 1}",
                        body=(
                            f"{actor.login or 'An administrator'} published {title_label.lower()} "
                            f"v{latest + 1} without the approval workflow. Changes: {summary}. "
                            f"Reason: {reason_text}"
                        ),
                        metadata={"new_version": latest + 1, "changes": summary, **target_data},
                    ),
                    now,
                )
            published = PublishedPolicy(account_id, target, latest + 1, latest, "change", now)
            for hook in (*self._publish_hooks, *hooks):
                hook(db, published)
        for event in stored:
            self._audit.log_stored(event)
        updated = self.version(account_id, latest + 1, target)
        assert updated is not None  # noqa: S101 - written in the transaction above
        return updated, changes

    @staticmethod
    def _target_data(target: PolicyTarget, label: str) -> dict[str, str | int | bool | None]:
        if not target.scoped:
            return {}
        return {"target_type": target.type.value, "target_id": target.id, "target": label}

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
        target: PolicyTarget = ORGANIZATION_TARGET,
    ) -> NotificationEvent:
        key = (
            domain_key(notification_type, account_id, target.type.value, target.id, new_version)
            if target.scoped
            else domain_key(notification_type, account_id, new_version)
        )
        return NotificationEvent(
            type=notification_type,
            account_id=account_id,
            severity=Severity.CRITICAL if weakening else Severity.HIGH,
            resource_type="policy",
            resource_id=str(account_id),
            dedup_key=key,
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
        target: PolicyTarget = ORGANIZATION_TARGET,
        hooks: Sequence[PublishHook] = (),
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
                target=target,
                hooks=hooks,
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
        target: PolicyTarget,
        hooks: Sequence[PublishHook],
    ) -> tuple[PolicyVersion, PolicyDiffView]:
        now = self._now()
        reason_text = (
            clean_text(reason.strip(), MAX_REASON_CHARS) if reason and reason.strip() else None
        )
        if reason_text is None:
            raise InputValidationError("A reason is required to roll back policy.", field="reason")
        current = self.current(account_id, target)
        if current.version != expected_current_version:
            raise ConflictError(
                f"The policy was changed by someone else (now version {current.version}). "
                "Reload to see the latest version before rolling back."
            )
        if not 1 <= target_version < current.version:
            raise InputValidationError(
                "The target must be an earlier version of this policy.",
                field="target_version",
            )
        restored = self.version(account_id, target_version, target)
        if restored is None:
            raise InputValidationError(
                f"Version {target_version} does not exist.", field="target_version"
            )
        self._verify_integrity(restored)
        diff = policy_diff(
            current.version,
            current.floors,
            target_version,
            restored.floors,
            current.defaults,
            restored.defaults,
        )
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
        assert restored.document is not None  # noqa: S101 - read from the database
        changes = policy_changes(
            current.floors, restored.floors, current.defaults, restored.defaults
        )
        summary = change_summary(changes)
        with self._store.transaction() as db:
            label = target_label(db, account_id, target)
            if label is None:
                raise NotFoundError()
            latest = self._max_version(db, account_id, target)
            if latest != expected_current_version:
                raise ConflictError(
                    f"The policy was changed by someone else (now version {latest}). "
                    "Reload to see the latest version before rolling back."
                )
            new_version = latest + 1
            self._insert_version(
                db,
                account_id=account_id,
                target=target,
                version=new_version,
                document=restored.document,
                now=now,
                actor=actor,
                reason=reason_text,
                kind="rollback",
                rollback_of=latest,
                restored_version=target_version,
            )
            target_data = self._target_data(target, label)
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.POLICY_ROLLED_BACK
                    if target.scoped
                    else AuditEventType.ORGANIZATION_POLICY_ROLLED_BACK,
                    actor=actor,
                    account_id=account_id,
                    previous_version=latest,
                    target_version=target_version,
                    new_version=new_version,
                    changes=summary,
                    weakening=diff.weakening,
                    reason=reason_text,
                    extra=target_data,
                ),
            )
            title_label = "Organization policy" if not target.scoped else f"{label} policy"
            emit(
                db,
                self._policy_notification(
                    NotificationType.POLICY_ROLLED_BACK,
                    account_id=account_id,
                    new_version=new_version,
                    title=(
                        f"{title_label} rolled back: v{latest} → v{new_version} "
                        f"(restores v{target_version})"
                    ),
                    body=(
                        f"{actor.login or 'An administrator'} rolled back the "
                        f"{title_label.lower()}. Previous: v{latest}. Restored: v{target_version}. "
                        f"Effective: v{new_version}. Changes: {summary}. Reason: {reason_text}"
                    ),
                    weakening=diff.weakening,
                    target=target,
                    metadata={
                        "previous_version": latest,
                        "target_version": target_version,
                        "new_version": new_version,
                        "changed_by": actor.login,
                        "changes": summary,
                        "weakening": diff.weakening,
                        "reason": reason_text,
                        **target_data,
                    },
                ),
                now,
            )
            published = PublishedPolicy(account_id, target, new_version, latest, "rollback", now)
            for hook in (*self._publish_hooks, *hooks):
                hook(db, published)
        self._audit.log_stored(stored)
        self._metrics.increment(POLICY_ROLLBACKS)
        created = self.version(account_id, new_version, target)
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
            floors, defaults = parse_document(document)
            validate_floors({k: v.value for k, v in floors.items()})
            canonical = canonical_document(floors, defaults)
        except (ValueError, InputValidationError):
            raise PolicyIntegrityError(
                f"Version {version.version} is not a valid policy and cannot be restored."
            ) from None
        if canonical != document:
            raise PolicyIntegrityError(
                f"Version {version.version} is not in canonical form and cannot be restored."
            )

    # -- enforcement ----------------------------------------------------- #
    def mandatory_for_installation(
        self, installation_id: int
    ) -> tuple[MandatoryPolicy | None, int | None]:
        """The combined floor (service + organisation) for an installation.

        Kept for adapters without organization governance; the GitHub App resolves
        the complete governance inputs per repository instead
        (:class:`commitguard.governance.resolver.GovernanceResolver`).
        """
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
