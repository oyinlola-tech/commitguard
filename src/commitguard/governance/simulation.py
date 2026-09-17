"""Policy simulation: what a draft policy would have decided, on real past scans.

::

    draft document
        │
        ▼
    for every repository in the target's scope:
        current governance inputs (resolver)  ──►  resolve_policy  ──►  policy set A
        the same inputs with the draft layer  ──►  resolve_policy  ──►  policy set B
        │
        ▼  the findings stored with its recent scans, re-evaluated by the
           real PolicyEvaluator under A and under B
        ▼
    projected: new blocks, new warnings, findings no longer blocked, unchanged

A simulation is **read-only analysis**. It never writes a policy version, never
touches a GitHub check, never changes violations or enforcement, and never
runs a detector: it reuses the recorded findings and the same policy engine
that produced the original results.

It is an **estimate**, and the result says so:

* it uses the scans that exist in the selected period, not future commits;
* scans recorded before Phase 8 did not store the repository's own
  ``.commitguard.yaml`` overrides, so built-in defaults are assumed for them
  and the result reports how many;
* repositories with no scan in the period are reported as "no data".

Large organizations run it in the background: a simulation is queued, claimed
by the maintenance loop with a lease, and bounded by
:data:`MAX_SCANS` scans and :data:`MAX_FINDINGS` findings.
"""

import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

from commitguard.audit.models import Actor, AuditEventType
from commitguard.config.schema import CommitGuardConfig, PolicyOverride
from commitguard.controlplane.access import Permission, Principal
from commitguard.controlplane.errors import ConflictError, InputValidationError, NotFoundError
from commitguard.controlplane.policies import PolicyTarget, PolicyTargetType, parse_document
from commitguard.controlplane.views import PolicyTargetView
from commitguard.core.decision import Action
from commitguard.core.result import (
    Confidence,
    DetectionResult,
    Evidence,
    EvidenceSource,
    Finding,
    Severity,
)
from commitguard.github.storage import SqliteStateStore
from commitguard.governance.cache import group_member_ids
from commitguard.governance.common import (
    account_repositories,
    dt,
    is_hex_id,
    new_id,
    req_dt,
    require,
    ts,
    visible_repository_ids,
)
from commitguard.governance.resolver import GovernanceResolver
from commitguard.observability.logging import get_logger
from commitguard.policies.evaluator import PolicyEvaluator
from commitguard.policies.governance import (
    Enforcement,
    GovernanceInputs,
    PolicyLayer,
    PolicyLevel,
    RuleRequirement,
    resolve_policy,
)
from commitguard.services.audit import AuditService

log = get_logger(__name__)

MAX_SCANS = 5_000
MAX_FINDINGS = 50_000
MAX_PERIOD_DAYS = 90
DEFAULT_PERIOD_DAYS = 30
MAX_OPEN_SIMULATIONS = 3
SIMULATION_LEASE = timedelta(minutes=10)
DISCLAIMER = (
    "SIMULATION - an estimate from recorded scans in the selected period, not the current "
    "security state and not a prediction of future commits."
)


class RepositoryImpact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repository_id: int
    full_name: str | None  # None: not visible to the caller
    scans: int
    new_blocks: int
    new_warnings: int
    no_longer_blocked: int


class SimulationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repositories_analyzed: int
    repositories_without_data: int
    scans_analyzed: int
    findings_analyzed: int
    new_blocks: int
    new_warnings: int
    no_longer_blocked: int
    unchanged: int
    scans_newly_blocked: int
    scans_no_longer_blocked: int
    scans_assumed_defaults: int  # no recorded repository configuration (pre-Phase 8 scans)
    most_affected: tuple[RepositoryImpact, ...]
    truncated: bool
    disclaimer: str = DISCLAIMER


class SimulationView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    organization_id: int
    target: PolicyTargetView
    draft_id: str | None
    current_version: int
    state: str
    parameters: dict[str, object]
    requested_by: str | None
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    result: SimulationResult | None
    error: str | None


def _config_from_overrides(document: str | None) -> tuple[list[CommitGuardConfig], bool]:
    """The repository configuration recorded with a scan, if any."""
    if not document:
        return [], False
    try:
        raw = json.loads(document)
    except ValueError:
        return [], False
    if not isinstance(raw, dict):
        return [], False
    policies: dict[str, PolicyOverride] = {}
    for rule, fields in raw.items():
        if not isinstance(fields, dict):
            continue
        try:
            policies[rule] = PolicyOverride.model_validate(fields)
        except ValueError:
            continue
    return [CommitGuardConfig(version=1, policies=policies)], True


def _finding(row: sqlite3.Row) -> Finding | None:
    try:
        evidence_items = json.loads(str(row["evidence"]))
    except ValueError:
        evidence_items = []
    evidence = []
    for item in evidence_items if isinstance(evidence_items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            evidence.append(
                Evidence(
                    source=EvidenceSource(str(item.get("source", ""))),
                    value=str(item.get("value", ""))[:512] or "(recorded)",
                    line_number=item.get("line_number"),
                )
            )
        except ValueError:
            continue
    if not evidence:
        evidence = [Evidence(source=EvidenceSource.MESSAGE, value="(recorded finding)")]
    try:
        return Finding(
            detector=str(row["detector"]),
            rule_id=str(row["rule_id"]),
            severity=Severity(row["severity"]),
            confidence=Confidence(row["confidence"]),
            title=str(row["title"]) or "finding",
            message=str(row["message"]) or "recorded finding",
            evidence=tuple(evidence),
            commit_sha=row["commit_sha"],
            remediation=str(row["remediation"]) or "See the violation page.",
        )
    except ValueError:  # pragma: no cover - stored rows are validated on write
        return None


class PolicySimulationService:
    def __init__(
        self,
        store: SqliteStateStore,
        audit: AuditService,
        resolver: GovernanceResolver,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._audit = audit
        self._resolver = resolver
        self._now = now

    # -- requests --------------------------------------------------------- #
    def create(
        self,
        principal: Principal,
        account_id: int,
        *,
        target: PolicyTarget,
        document: str,
        draft_id: str | None,
        period_days: object = None,
        repository_ids: Sequence[int] | None = None,
    ) -> SimulationView:
        require(principal, Permission.POLICIES_WRITE, account_id)
        days = DEFAULT_PERIOD_DAYS if period_days is None else period_days
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_PERIOD_DAYS:
            raise InputValidationError(
                f"period_days must be between 1 and {MAX_PERIOD_DAYS}", field="period_days"
            )
        parse_document(document)  # reject an invalid draft document early
        now = self._now()
        simulation_id = new_id()
        current = self._resolver_policies_current(account_id, target)
        parameters = {
            "period_days": days,
            "repository_ids": sorted(set(repository_ids)) if repository_ids else None,
        }
        with self._store.transaction() as db:
            open_count = db.execute(
                "SELECT COUNT(*) AS n FROM policy_simulations WHERE account_id = ? "
                "AND state IN ('queued', 'running')",
                (account_id,),
            ).fetchone()["n"]
            if int(open_count) >= MAX_OPEN_SIMULATIONS:
                raise ConflictError(
                    "There are already simulations running for this organization. "
                    "Wait for them to finish."
                )
            db.execute(
                "INSERT INTO policy_simulations (simulation_id, account_id, draft_id, "
                "target_type, target_id, current_version, document, parameters, state, "
                "requested_by_id, requested_by_login, requested_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                (
                    simulation_id,
                    account_id,
                    draft_id,
                    target.type.value,
                    target.id,
                    current,
                    document,
                    json.dumps(parameters),
                    principal.user_id,
                    principal.login,
                    ts(now),
                ),
            )
        return self.get(principal, simulation_id)

    def _resolver_policies_current(self, account_id: int, target: PolicyTarget) -> int:
        rows = self._store.query(
            "SELECT MAX(version) AS latest FROM organization_policy_versions WHERE account_id = ?"
            if target.type is PolicyTargetType.ORGANIZATION
            else "SELECT MAX(version) AS latest FROM scoped_policy_versions WHERE account_id = ? "
            "AND target_type = ? AND target_id = ?",
            (account_id,)
            if target.type is PolicyTargetType.ORGANIZATION
            else (account_id, target.type.value, target.id),
        )
        return int(rows[0]["latest"] or 0) if rows else 0

    # -- reads ------------------------------------------------------------ #
    def _row(self, simulation_id: str) -> sqlite3.Row:
        if not is_hex_id(simulation_id):
            raise NotFoundError()
        rows = self._store.query(
            "SELECT * FROM policy_simulations WHERE simulation_id = ?", (simulation_id,)
        )
        if not rows:
            raise NotFoundError()
        return rows[0]

    def get(self, principal: Principal, simulation_id: str) -> SimulationView:
        row = self._row(simulation_id)
        account_id = int(row["account_id"])
        require(principal, Permission.POLICIES_READ, account_id)
        return self._view(row, principal)

    def list_simulations(
        self, principal: Principal, account_id: int, *, draft_id: str | None = None
    ) -> list[SimulationView]:
        require(principal, Permission.POLICIES_READ, account_id)
        sql = "SELECT * FROM policy_simulations WHERE account_id = ?"
        params: list[object] = [account_id]
        if draft_id is not None:
            sql += " AND draft_id = ?"
            params.append(draft_id)
        rows = self._store.query(sql + " ORDER BY requested_at DESC LIMIT 50", params)
        return [self._view(row, principal) for row in rows]

    def _view(self, row: sqlite3.Row, principal: Principal) -> SimulationView:
        account_id = int(row["account_id"])
        target = PolicyTarget(PolicyTargetType(row["target_type"]), str(row["target_id"]))
        result = None
        if row["result"]:
            try:
                parsed = SimulationResult.model_validate_json(str(row["result"]))
            except ValueError:
                parsed = None
            if parsed is not None:
                visible = visible_repository_ids(self._store, principal, account_id)
                result = parsed.model_copy(
                    update={
                        "most_affected": tuple(
                            impact
                            if impact.repository_id in visible
                            else impact.model_copy(update={"full_name": None})
                            for impact in parsed.most_affected
                        )
                    }
                )
        with self._store.transaction() as db:
            from commitguard.controlplane.policies import target_label

            label = target_label(db, account_id, target) or "(removed)"
        return SimulationView(
            id=str(row["simulation_id"]),
            organization_id=account_id,
            target=PolicyTargetView(type=target.type.value, id=target.id, label=label),
            draft_id=row["draft_id"],
            current_version=int(row["current_version"]),
            state=str(row["state"]),
            parameters=json.loads(str(row["parameters"])),
            requested_by=row["requested_by_login"],
            requested_at=req_dt(row["requested_at"]),
            started_at=dt(row["started_at"]),
            completed_at=dt(row["completed_at"]),
            result=result,
            error=row["error"],
        )

    # -- execution --------------------------------------------------------- #
    def run_pending(self, limit: int = 1) -> int:
        """Claim and run queued simulations (maintenance loop). Read-only analysis."""
        now = self._now()
        rows = self._store.query(
            "SELECT simulation_id FROM policy_simulations WHERE state = 'queued' "
            "OR (state = 'running' AND lease_expires_at < ?) ORDER BY requested_at LIMIT ?",
            (ts(now), int(limit)),
        )
        done = 0
        for row in rows:
            simulation_id = str(row["simulation_id"])
            with self._store.transaction() as db:
                claimed = db.execute(
                    "UPDATE policy_simulations SET state = 'running', started_at = ?, "
                    "lease_expires_at = ?, attempts = attempts + 1 WHERE simulation_id = ? "
                    "AND (state = 'queued' OR (state = 'running' AND lease_expires_at < ?))",
                    (
                        ts(now),
                        ts(now + SIMULATION_LEASE),
                        simulation_id,
                        ts(now),
                    ),
                ).rowcount
            if claimed != 1:
                continue
            claimed_row = self._row(simulation_id)
            requester = Actor.user(
                int(claimed_row["requested_by_id"] or 0),
                str(claimed_row["requested_by_login"] or "unknown"),
            )
            try:
                result = self._simulate(claimed_row)
                with self._store.transaction() as db:
                    db.execute(
                        "UPDATE policy_simulations SET state = 'completed', completed_at = ?, "
                        "result = ?, lease_expires_at = NULL WHERE simulation_id = ?",
                        (ts(self._now()), result.model_dump_json(), simulation_id),
                    )
                    stored = self._store.insert_audit_event(
                        db,
                        self._audit.build(
                            AuditEventType.POLICY_SIMULATED,
                            actor=requester,
                            account_id=int(claimed_row["account_id"]),
                            simulation=simulation_id,
                            scans=result.scans_analyzed,
                            new_blocks=result.new_blocks,
                            new_warnings=result.new_warnings,
                            no_longer_blocked=result.no_longer_blocked,
                        ),
                    )
                self._audit.log_stored(stored)
                done += 1
            except Exception as exc:  # noqa: BLE001 - a failed simulation changes nothing
                log.error(
                    "policy_simulation_failed",
                    simulation=simulation_id,
                    error_type=type(exc).__name__,
                )
                with self._store.transaction() as db:
                    db.execute(
                        "UPDATE policy_simulations SET state = 'failed', completed_at = ?, "
                        "error = ?, lease_expires_at = NULL WHERE simulation_id = ?",
                        (
                            ts(self._now()),
                            f"simulation failed ({type(exc).__name__})",
                            simulation_id,
                        ),
                    )
        return done

    def _draft_layer(self, target: PolicyTarget, document: str, version: int) -> PolicyLayer | None:
        floors, defaults = parse_document(document)
        rules = {
            rule: RuleRequirement(action=action, enforcement=Enforcement.DEFAULT)
            for rule, action in defaults.items()
        }
        rules.update({rule: RuleRequirement(action=action) for rule, action in floors.items()})
        if not rules:
            return None
        level = {
            PolicyTargetType.ORGANIZATION: PolicyLevel.ORGANIZATION,
            PolicyTargetType.GROUP: PolicyLevel.GROUP,
            PolicyTargetType.REPOSITORY: PolicyLevel.REPOSITORY_POLICY,
        }[target.type]
        return PolicyLayer(
            level=level,
            source_id=target.id,
            label=f"draft policy (would be v{version + 1})",
            rules=rules,
        )

    @staticmethod
    def _with_draft(
        inputs: GovernanceInputs, target: PolicyTarget, draft: PolicyLayer | None
    ) -> GovernanceInputs:
        """The same inputs with the target's layer replaced by the draft."""
        kept = tuple(
            layer
            for layer in inputs.layers
            if not (
                layer.source_id == target.id
                and layer.level
                in (
                    PolicyLevel.ORGANIZATION
                    if target.type is PolicyTargetType.ORGANIZATION
                    else PolicyLevel.GROUP
                    if target.type is PolicyTargetType.GROUP
                    else PolicyLevel.REPOSITORY_POLICY,
                )
            )
            # The security baseline is a separate organization-level layer and stays.
            or layer.source_id == "baseline"
        )
        return inputs.model_copy(update={"layers": (*kept, draft) if draft is not None else kept})

    def _scope(self, db: sqlite3.Connection, account_id: int, target: PolicyTarget) -> list[int]:
        repositories = sorted(account_repositories(db, account_id))
        if target.type is PolicyTargetType.GROUP:
            members = set(group_member_ids(db, account_id, target.id))
            return [r for r in repositories if r in members]
        if target.type is PolicyTargetType.REPOSITORY:
            wanted = int(target.id)
            return [r for r in repositories if r == wanted]
        return repositories

    def _simulate(self, row: sqlite3.Row) -> SimulationResult:
        account_id = int(row["account_id"])
        target = PolicyTarget(PolicyTargetType(row["target_type"]), str(row["target_id"]))
        parameters = json.loads(str(row["parameters"]))
        period = timedelta(days=int(parameters.get("period_days", DEFAULT_PERIOD_DAYS)))
        wanted = parameters.get("repository_ids")
        since = self._now() - period
        draft_layer = self._draft_layer(target, str(row["document"]), int(row["current_version"]))

        with self._store.transaction() as db:
            scope = self._scope(db, account_id, target)
            if wanted:
                scope = [r for r in scope if r in set(wanted)]
            names = {
                repository_id: repository.full_name
                for repository_id, repository in account_repositories(db, account_id).items()
            }
            policy_sets = {}
            for repository_id in scope:
                resolved = self._resolver.resolve_in(db, account_id, repository_id)
                policy_sets[repository_id] = (
                    resolved.inputs,
                    self._with_draft(resolved.inputs, target, draft_layer),
                )

        totals = dict.fromkeys(
            (
                "new_blocks",
                "new_warnings",
                "no_longer_blocked",
                "unchanged",
                "scans",
                "findings",
                "scans_newly_blocked",
                "scans_no_longer_blocked",
                "assumed",
            ),
            0,
        )
        impacts: list[RepositoryImpact] = []
        repositories_without_data = 0
        repositories_analyzed = 0
        truncated = False
        for repository_id in scope:
            if totals["scans"] >= MAX_SCANS or totals["findings"] >= MAX_FINDINGS:
                truncated = True  # the remaining repositories were not analyzed
                break
            scans = self._store.query(
                "SELECT j.job_id, j.repository_policies, j.group_key, j.sequence FROM scan_jobs j "
                "WHERE j.repository_id = ? AND j.state IN ('passed', 'failed') "
                "AND j.completed_at >= ? AND NOT EXISTS (SELECT 1 FROM scan_jobs n WHERE "
                "n.repository_id = j.repository_id AND n.group_key = j.group_key "
                "AND n.state IN ('passed', 'failed') AND n.sequence > j.sequence) "
                "ORDER BY j.sequence DESC LIMIT 200",
                (repository_id, ts(since)),
            )
            if not scans:
                repositories_without_data += 1
                continue
            repositories_analyzed += 1
            current_inputs, draft_inputs = policy_sets[repository_id]
            impact = {"new_blocks": 0, "new_warnings": 0, "no_longer_blocked": 0, "scans": 0}
            # Scans of one repository usually share a configuration: resolve each once.
            policy_cache: dict[str | None, tuple[Any, Any, bool]] = {}
            for scan in scans:
                key = scan["repository_policies"]
                if key not in policy_cache:
                    configs, recorded = _config_from_overrides(key)
                    policy_cache[key] = (
                        resolve_policy(current_inputs, configs).policy_set(),
                        resolve_policy(draft_inputs, configs).policy_set(),
                        recorded,
                    )
                current_policy, draft_policy, recorded = policy_cache[key]
                if not recorded:
                    totals["assumed"] += 1
                findings = self._store.query(
                    "SELECT detector, rule_id, severity, confidence, title, message, "
                    "remediation, evidence, commit_sha FROM findings WHERE job_id = ? LIMIT 2000",
                    (scan["job_id"],),
                )
                rebuilt = [f for f in (_finding(r) for r in findings) if f is not None]
                if not rebuilt:
                    continue
                totals["scans"] += 1
                impact["scans"] += 1
                totals["findings"] += len(rebuilt)
                result = DetectionResult(commit_sha=None, detectors_run=(), findings=tuple(rebuilt))
                before = PolicyEvaluator(current_policy).evaluate(result)
                after = PolicyEvaluator(draft_policy).evaluate(result)
                for old, new in zip(before.explanations, after.explanations, strict=True):
                    if old.action is new.action:
                        totals["unchanged"] += 1
                    elif new.action is Action.BLOCK:
                        totals["new_blocks"] += 1
                        impact["new_blocks"] += 1
                    elif new.action is Action.WARN and old.action is Action.ALLOW:
                        totals["new_warnings"] += 1
                        impact["new_warnings"] += 1
                    else:
                        totals["no_longer_blocked"] += 1
                        impact["no_longer_blocked"] += 1
                if before.action is not Action.BLOCK and after.action is Action.BLOCK:
                    totals["scans_newly_blocked"] += 1
                elif before.action is Action.BLOCK and after.action is not Action.BLOCK:
                    totals["scans_no_longer_blocked"] += 1
            if impact["new_blocks"] or impact["new_warnings"] or impact["no_longer_blocked"]:
                impacts.append(
                    RepositoryImpact(
                        repository_id=repository_id,
                        full_name=names.get(repository_id),
                        scans=impact["scans"],
                        new_blocks=impact["new_blocks"],
                        new_warnings=impact["new_warnings"],
                        no_longer_blocked=impact["no_longer_blocked"],
                    )
                )
        impacts.sort(
            key=lambda i: i.new_blocks + i.new_warnings + i.no_longer_blocked, reverse=True
        )
        return SimulationResult(
            repositories_analyzed=repositories_analyzed,
            repositories_without_data=repositories_without_data,
            scans_analyzed=totals["scans"],
            findings_analyzed=totals["findings"],
            new_blocks=totals["new_blocks"],
            new_warnings=totals["new_warnings"],
            no_longer_blocked=totals["no_longer_blocked"],
            unchanged=totals["unchanged"],
            scans_newly_blocked=totals["scans_newly_blocked"],
            scans_no_longer_blocked=totals["scans_no_longer_blocked"],
            scans_assumed_defaults=totals["assumed"],
            most_affected=tuple(impacts[:10]),
            truncated=truncated,
        )
