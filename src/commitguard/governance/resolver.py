"""GovernanceResolver: which governance inputs apply to a repository, cached and propagated.

The resolver *collects* what the organization layer decided for one repository
and hands it to the pure :func:`commitguard.policies.governance.resolve_policy`
(which builds the policy set) - the policy engine then evaluates findings with
that set. Nothing here evaluates a finding.

Inputs, in the order they are layered::

    service policy            operator file (mandatory)
    security baseline         organization settings (mandatory)
    organization policy       the version that applies to this repository:
                              - a staged rollout in progress: the new version for
                                enrolled repositories, the previous one for the rest
                              - otherwise the newest published version
    repository group policies every non-archived group the repository belongs to
                              (rollout-aware per group)
    repository policy         the newest published version for this repository
    approved exceptions       active, unexpired, scoped to the organization, one of
                              its groups, or the repository
    mode                      monitor / enforce

Cache and propagation
=====================

Resolution runs inside one database transaction (a consistent snapshot, and
SQLite serialises writers), and its result is stored in
``repository_effective_policies`` as ``up_to_date``. Every change that affects a
repository marks its row ``stale`` in the change's own transaction
(:mod:`commitguard.governance.cache`), so a scan uses a cached row only when it
is current - otherwise it resolves again. Rows also carry ``valid_until``: the
earliest expiry of an exception they include, after which they are resolved
again even before the expiry worker runs.

The propagation job (:meth:`GovernanceResolver.propagate`) resolves stale rows
and rows of repositories never resolved, in bounded batches. A repository whose
resolution fails is ``error``: administrators are notified and the dashboard
shows it, and a scan of that repository resolves directly - if that also fails,
the scan fails closed. Propagation status is reported as counts; a policy change
is never shown as propagated while any affected repository is stale, syncing or
in error.
"""

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from commitguard.audit.models import SYSTEM_ACTOR, AuditEventType
from commitguard.controlplane.access import Permission, Principal
from commitguard.controlplane.errors import NotFoundError
from commitguard.controlplane.policies import (
    ORGANIZATION_TARGET,
    OrganizationPolicyService,
    PolicyTarget,
    PublishedPolicy,
    parse_document,
)
from commitguard.core.decision import Action
from commitguard.core.result import Severity
from commitguard.github.storage import SqliteStateStore
from commitguard.governance.cache import invalidate_repositories, invalidate_scope
from commitguard.governance.common import (
    account_repositories,
    account_repository,
    dt,
    require,
    ts,
    visible_repository_ids,
)
from commitguard.governance.exceptions import active_grants
from commitguard.governance.inventory import governance_state
from commitguard.governance.settings import OrganizationSettings, load_settings
from commitguard.notifications.deduplication import domain_key
from commitguard.notifications.models import NotificationEvent, NotificationType
from commitguard.notifications.outbox import emit
from commitguard.observability.logging import get_logger
from commitguard.policies.defaults import DEFAULT_POLICIES
from commitguard.policies.governance import (
    EffectivePolicy,
    Enforcement,
    GovernanceInputs,
    PolicyLayer,
    PolicyLevel,
    RepositoryMode,
    RuleRequirement,
    resolve_policy,
)
from commitguard.services.audit import AuditService

log = get_logger(__name__)

PROPAGATION_BATCH = 200
MAX_PROPAGATION_ATTEMPTS = 5
PROPAGATION_STATES = ("up_to_date", "stale", "syncing", "error", "pending")


class GovernanceVersions(BaseModel):
    """Which version of each governance source applied (recorded with scans)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_policy: int | None = None
    settings: int | None = None
    groups: dict[str, int] = {}
    repository_policy: int | None = None
    rollouts: dict[str, int] = {}  # rollout ID -> version applied through it
    exceptions: tuple[str, ...] = ()


class ResolvedGovernance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: int
    repository_id: int
    inputs: GovernanceInputs
    versions: GovernanceVersions
    valid_until: datetime | None
    resolved_at: datetime

    @property
    def fingerprint(self) -> str:
        return self.inputs.fingerprint

    def document(self) -> str:
        return self.model_dump_json()


class PropagationStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_id: int
    repositories: int
    up_to_date: int
    stale: int
    syncing: int
    error: int
    pending: int  # never resolved yet
    complete: bool  # every repository up to date
    failing: tuple[dict[str, object], ...]  # repositories in error (visible ones)
    checked_at: datetime


class EffectivePolicyView(BaseModel):
    """A repository's effective policy with provenance, as the dashboard shows it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_id: int
    repository_id: int
    full_name: str
    mode: RepositoryMode
    effective: EffectivePolicy
    versions: GovernanceVersions
    propagation: str
    resolved_at: datetime
    #: From the latest completed scan: conflicts involving the repository's own
    #: .commitguard.yaml, which is only known at scan time.
    last_scan_id: str | None
    last_scan_completed_at: datetime | None
    last_scan_effective: EffectivePolicy | None
    last_scan_used_current_policy: bool | None


def _service_layer(policies: OrganizationPolicyService) -> PolicyLayer | None:
    service = policies.service_policy
    if service is None or not service.config.policies:
        return None
    rules = {
        rule: RuleRequirement(action=override.action or DEFAULT_POLICIES[rule].action)
        for rule, override in service.config.policies.items()
        if (override.action or DEFAULT_POLICIES[rule].action) is not Action.ALLOW
    }
    return PolicyLayer(
        level=PolicyLevel.SERVICE,
        label=f"mandatory policy ({service.description})"[:200],
        rules=rules,
    )


def _row_version(row: sqlite3.Row | None) -> int:
    return int(row["latest"] or 0) if row is not None else 0


class GovernanceResolver:
    def __init__(
        self,
        store: SqliteStateStore,
        policies: OrganizationPolicyService,
        audit: AuditService,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._policies = policies
        self._audit = audit
        self._now = now
        policies.add_publish_hook(self.on_policy_published)

    # -- invalidation hooks ----------------------------------------------- #
    def on_policy_published(self, db: sqlite3.Connection, published: PublishedPolicy) -> None:
        invalidate_scope(
            db, published.account_id, published.target.type.value, published.target.id,
            published.now,
        )

    def on_settings_saved(
        self, db: sqlite3.Connection, account_id: int, _settings: OrganizationSettings
    ) -> None:
        invalidate_repositories(db, account_id, None, self._now())

    # -- resolution ------------------------------------------------------- #
    def _version_for(
        self,
        db: sqlite3.Connection,
        account_id: int,
        target: PolicyTarget,
        repository_id: int,
        rollouts: dict[str, int],
    ) -> int:
        if target.scoped:
            latest = _row_version(
                db.execute(
                    "SELECT MAX(version) AS latest FROM scoped_policy_versions WHERE "
                    "account_id = ? AND target_type = ? AND target_id = ?",
                    (account_id, target.type.value, target.id),
                ).fetchone()
            )
        else:
            latest = _row_version(
                db.execute(
                    "SELECT MAX(version) AS latest FROM organization_policy_versions "
                    "WHERE account_id = ?",
                    (account_id,),
                ).fetchone()
            )
        rollout = db.execute(
            "SELECT rollout_id, from_version, to_version FROM policy_rollouts WHERE "
            "account_id = ? AND target_type = ? AND target_id = ? AND state IN "
            "('pilot', 'rollout', 'paused')",
            (account_id, target.type.value, target.id),
        ).fetchone()
        if rollout is None or int(rollout["to_version"]) != latest:
            return latest
        enrolled = db.execute(
            "SELECT 1 FROM policy_rollout_repositories WHERE rollout_id = ? AND repository_id = ?",
            (rollout["rollout_id"], repository_id),
        ).fetchone()
        version = latest if enrolled else int(rollout["from_version"])
        rollouts[str(rollout["rollout_id"])] = version
        return version

    def _layer(
        self,
        account_id: int,
        target: PolicyTarget,
        version: int,
        level: PolicyLevel,
        label: str,
    ) -> PolicyLayer | None:
        if version <= 0:
            return None
        stored = self._policies.version(account_id, version, target)
        if stored is None:
            raise RuntimeError(f"policy version {version} is missing")
        floors, defaults = parse_document(stored.document or "{}")
        rules = {
            rule: RuleRequirement(action=action, enforcement=Enforcement.DEFAULT)
            for rule, action in defaults.items()
        }
        rules.update({rule: RuleRequirement(action=action) for rule, action in floors.items()})
        if not rules:
            return None
        return PolicyLayer(
            level=level,
            source_id=target.id,
            label=f"{label} v{version}"[:200],
            version=version,
            rules=rules,
        )

    def resolve_in(
        self, db: sqlite3.Connection, account_id: int, repository_id: int
    ) -> ResolvedGovernance:
        """Resolve from the database (no cache), inside the caller's transaction."""
        now = self._now()
        layers: list[PolicyLayer] = []
        service = _service_layer(self._policies)
        if service is not None:
            layers.append(service)
        stored_settings = load_settings(db, account_id)
        baseline = stored_settings.settings.security_baseline
        if baseline:
            layers.append(
                PolicyLayer(
                    level=PolicyLevel.ORGANIZATION,
                    source_id="baseline",
                    label=f"security baseline (settings v{stored_settings.version})",
                    version=stored_settings.version or None,
                    rules={rule: RuleRequirement(action=a) for rule, a in baseline.items()},
                )
            )
        rollouts: dict[str, int] = {}
        organization_version = self._version_for(
            db, account_id, ORGANIZATION_TARGET, repository_id, rollouts
        )
        organization = self._layer(
            account_id,
            ORGANIZATION_TARGET,
            organization_version,
            PolicyLevel.ORGANIZATION,
            "organization policy",
        )
        if organization is not None:
            layers.append(organization)
        groups: dict[str, int] = {}
        group_rows = db.execute(
            "SELECT g.group_id, g.name FROM repository_group_members m JOIN repository_groups g "
            "ON g.group_id = m.group_id WHERE m.account_id = ? AND m.repository_id = ? "
            "AND g.archived_at IS NULL ORDER BY g.name_key LIMIT 50",
            (account_id, repository_id),
        ).fetchall()
        for group in group_rows:
            target = PolicyTarget.group(str(group["group_id"]))
            version = self._version_for(db, account_id, target, repository_id, rollouts)
            groups[str(group["group_id"])] = version
            layer = self._layer(
                account_id, target, version, PolicyLevel.GROUP, f"group {group['name']} policy"
            )
            if layer is not None:
                layers.append(layer)
        repository_target = PolicyTarget.repository(repository_id)
        repository_version = self._version_for(
            db, account_id, repository_target, repository_id, rollouts
        )
        repository_layer = self._layer(
            account_id,
            repository_target,
            repository_version,
            PolicyLevel.REPOSITORY_POLICY,
            "repository policy",
        )
        if repository_layer is not None:
            layers.append(repository_layer)
        grants = active_grants(
            db, account_id, repository_id, [str(g["group_id"]) for g in group_rows], now
        )
        mode = governance_state(db, account_id, repository_id).mode
        expiries = [g.expires_at for g in grants if g.expires_at is not None]
        return ResolvedGovernance(
            account_id=account_id,
            repository_id=repository_id,
            inputs=GovernanceInputs(layers=tuple(layers), exceptions=tuple(grants), mode=mode),
            versions=GovernanceVersions(
                organization_policy=organization_version or None,
                settings=stored_settings.version or None,
                groups=groups,
                repository_policy=repository_version or None,
                rollouts=rollouts,
                exceptions=tuple(g.exception_id for g in grants),
            ),
            valid_until=min(expiries) if expiries else None,
            resolved_at=now,
        )

    def _store_resolution(self, db: sqlite3.Connection, resolved: ResolvedGovernance) -> None:
        db.execute(
            "INSERT INTO repository_effective_policies (account_id, repository_id, state, "
            "fingerprint, document, computed_at, valid_until, attempts, error) "
            "VALUES (?, ?, 'up_to_date', ?, ?, ?, ?, 0, NULL) "
            "ON CONFLICT (account_id, repository_id) DO UPDATE SET state = 'up_to_date', "
            "fingerprint = excluded.fingerprint, document = excluded.document, "
            "computed_at = excluded.computed_at, valid_until = excluded.valid_until, "
            "attempts = 0, error = NULL",
            (
                resolved.account_id,
                resolved.repository_id,
                resolved.fingerprint,
                resolved.document(),
                ts(resolved.resolved_at),
                ts(resolved.valid_until) if resolved.valid_until else None,
            ),
        )

    def for_repository(self, account_id: int, repository_id: int) -> ResolvedGovernance:
        """Current governance for a repository: the cached row if current, else resolved."""
        now = self._now()
        rows = self._store.query(
            "SELECT state, document, valid_until FROM repository_effective_policies "
            "WHERE account_id = ? AND repository_id = ?",
            (account_id, repository_id),
        )
        if rows and rows[0]["state"] == "up_to_date" and rows[0]["document"]:
            valid_until = dt(rows[0]["valid_until"])
            if valid_until is None or valid_until > now:
                try:
                    return ResolvedGovernance.model_validate_json(str(rows[0]["document"]))
                except ValueError:
                    log.warning("effective_policy_cache_unreadable", repository_id=repository_id)
        with self._store.transaction() as db:
            resolved = self.resolve_in(db, account_id, repository_id)
            self._store_resolution(db, resolved)
        return resolved

    def for_scan(self, installation_id: int, repository_id: int) -> ResolvedGovernance | None:
        """Governance for a scan job; None when the installation has no organization record."""
        rows = self._store.query(
            "SELECT account_id FROM installations WHERE installation_id = ?", (installation_id,)
        )
        if not rows:
            return None
        return self.for_repository(int(rows[0]["account_id"]), repository_id)

    # -- propagation ------------------------------------------------------ #
    def propagate(self, limit: int = PROPAGATION_BATCH) -> dict[str, int]:
        """Resolve stale, failed and never-resolved repositories (bounded)."""
        pending = self._store.query(
            "SELECT account_id, repository_id FROM repository_effective_policies "
            "WHERE state IN ('stale', 'error', 'syncing') AND attempts < ? "
            "ORDER BY invalidated_at LIMIT ?",
            (MAX_PROPAGATION_ATTEMPTS, int(limit)),
        )
        work = [(int(r["account_id"]), int(r["repository_id"])) for r in pending]
        if len(work) < limit:
            missing = self._store.query(
                "SELECT DISTINCT i.account_id, k.repository_id FROM known_repositories k "
                "JOIN installations i ON i.installation_id = k.installation_id "
                "WHERE i.state != 'deleted' AND NOT EXISTS (SELECT 1 FROM "
                "repository_effective_policies e WHERE e.account_id = i.account_id AND "
                "e.repository_id = k.repository_id) LIMIT ?",
                (int(limit) - len(work),),
            )
            work += [(int(r["account_id"]), int(r["repository_id"])) for r in missing]
        counts = {"resolved": 0, "failed": 0}
        failed_accounts: dict[int, list[int]] = {}
        for account_id, repository_id in work:
            now = self._now()
            with self._store.transaction() as db:
                db.execute(
                    "INSERT INTO repository_effective_policies (account_id, repository_id, state, "
                    "invalidated_at) VALUES (?, ?, 'syncing', ?) ON CONFLICT (account_id, "
                    "repository_id) DO UPDATE SET state = 'syncing'",
                    (account_id, repository_id, ts(now)),
                )
            try:
                with self._store.transaction() as db:
                    resolved = self.resolve_in(db, account_id, repository_id)
                    self._store_resolution(db, resolved)
                counts["resolved"] += 1
            except Exception as exc:  # noqa: BLE001 - one repository must not stop propagation
                log.error(
                    "policy_propagation_failed",
                    repository_id=repository_id,
                    error_type=type(exc).__name__,
                )
                with self._store.transaction() as db:
                    db.execute(
                        "UPDATE repository_effective_policies SET state = 'error', "
                        "attempts = attempts + 1, error = ? WHERE account_id = ? "
                        "AND repository_id = ?",
                        (f"resolution failed ({type(exc).__name__})", account_id, repository_id),
                    )
                counts["failed"] += 1
                failed_accounts.setdefault(account_id, []).append(repository_id)
        for account_id, repositories in failed_accounts.items():
            self._propagation_failed(account_id, repositories)
        return counts

    def _propagation_failed(self, account_id: int, repositories: list[int]) -> None:
        now = self._now()
        with self._store.transaction() as db:
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.POLICY_PROPAGATION_FAILED,
                    actor=SYSTEM_ACTOR,
                    account_id=account_id,
                    repositories=len(repositories),
                    repository_ids=",".join(str(i) for i in repositories[:50]),
                ),
            )
            emit(
                db,
                NotificationEvent(
                    type=NotificationType.POLICY_PROPAGATION_FAILED,
                    account_id=account_id,
                    severity=Severity.HIGH,
                    resource_type="organization",
                    resource_id=str(account_id),
                    dedup_key=domain_key(NotificationType.POLICY_PROPAGATION_FAILED, account_id),
                    title="Policy propagation failed for some repositories",
                    body=(
                        f"CommitGuard could not update the effective policy of {len(repositories)} "
                        "repository(ies). The organization policy remains active; scans of these "
                        "repositories resolve the policy directly and fail closed if that fails. "
                        "See the organization dashboard for details."
                    ),
                    metadata={"repositories": len(repositories)},
                ),
                now,
            )
        self._audit.log_stored(stored)

    def propagation_status(self, principal: Principal, account_id: int) -> PropagationStatus:
        require(principal, Permission.POLICIES_READ, account_id)
        repositories = account_repositories(self._store, account_id)
        states = {
            int(r["repository_id"]): (str(r["state"]), r["error"], dt(r["valid_until"]))
            for r in self._store.query(
                "SELECT repository_id, state, error, valid_until FROM "
                "repository_effective_policies WHERE account_id = ?",
                (account_id,),
            )
        }
        now = self._now()
        counts = dict.fromkeys(PROPAGATION_STATES, 0)
        visible = visible_repository_ids(self._store, principal, account_id)
        failing: list[dict[str, object]] = []
        for repository_id, repository in repositories.items():
            state, error, valid_until = states.get(repository_id, ("pending", None, None))
            if state == "up_to_date" and valid_until is not None and valid_until <= now:
                state = "stale"  # an included exception expired; not yet re-resolved
            counts[state] += 1
            if state == "error" and repository_id in visible and len(failing) < 50:
                failing.append(
                    {"repository_id": repository_id, "full_name": repository.full_name,
                     "error": error}
                )
        return PropagationStatus(
            organization_id=account_id,
            repositories=len(repositories),
            up_to_date=counts["up_to_date"],
            stale=counts["stale"],
            syncing=counts["syncing"],
            error=counts["error"],
            pending=counts["pending"],
            complete=counts["up_to_date"] == len(repositories),
            failing=tuple(failing),
            checked_at=now,
        )

    def repository_state(self, account_id: int, repository_id: int) -> str:
        rows = self._store.query(
            "SELECT state, valid_until FROM repository_effective_policies WHERE account_id = ? "
            "AND repository_id = ?",
            (account_id, repository_id),
        )
        if not rows:
            return "pending"
        valid_until = dt(rows[0]["valid_until"])
        expired = valid_until is not None and valid_until <= self._now()
        if rows[0]["state"] == "up_to_date" and expired:
            return "stale"
        return str(rows[0]["state"])

    # -- views ------------------------------------------------------------ #
    def effective_view(
        self, principal: Principal, account_id: int, repository_id: int
    ) -> EffectivePolicyView:
        require(principal, Permission.POLICIES_READ, account_id)
        repository = account_repository(self._store, account_id, repository_id)
        if repository is None or repository_id not in visible_repository_ids(
            self._store, principal, account_id
        ):
            raise NotFoundError()
        resolved = self.for_repository(account_id, repository_id)
        state = self.repository_state(account_id, repository_id)
        effective = resolve_policy(resolved.inputs, None)
        last = self._store.query(
            "SELECT j.job_id, j.completed_at, j.governance, j.governance_fingerprint FROM "
            "scan_jobs j JOIN installations i ON i.installation_id = j.installation_id WHERE "
            "i.account_id = ? AND j.repository_id = ? AND j.state IN ('passed', 'failed') "
            "ORDER BY j.completed_at DESC LIMIT 1",
            (account_id, repository_id),
        )
        last_effective = None
        used_current = None
        if last:
            record = last[0]["governance"]
            if record:
                try:
                    parsed = json.loads(str(record))
                    last_effective = EffectivePolicy.model_validate(parsed.get("effective"))
                except (ValueError, TypeError):
                    last_effective = None
            fingerprint = last[0]["governance_fingerprint"]
            used_current = fingerprint == resolved.fingerprint if fingerprint else False
        return EffectivePolicyView(
            organization_id=account_id,
            repository_id=repository_id,
            full_name=repository.full_name,
            mode=resolved.inputs.mode,
            effective=effective,
            versions=resolved.versions,
            propagation=state,
            resolved_at=resolved.resolved_at,
            last_scan_id=last[0]["job_id"] if last else None,
            last_scan_completed_at=dt(last[0]["completed_at"]) if last else None,
            last_scan_effective=last_effective,
            last_scan_used_current_policy=used_current,
        )


def scan_governance_record(
    resolved: ResolvedGovernance, effective: EffectivePolicy | None
) -> str:
    """The governance record stored with a completed scan (immutable history)."""
    return json.dumps(
        {
            "versions": resolved.versions.model_dump(mode="json"),
            "inputs_fingerprint": resolved.fingerprint,
            "description": resolved.inputs.describe(),
            "mode": resolved.inputs.mode.value,
            "effective": effective.model_dump(mode="json") if effective else None,
        },
        sort_keys=True,
    )
