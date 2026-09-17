"""Organization security posture, repository matrix, drift, trends, reports and search.

Posture is a small set of explicit states, never a score
=========================================================

Repository posture - the **first** rule that matches decides:

====  ==================================================================  ===============
#     Condition                                                           Posture
====  ==================================================================  ===============
1     the GitHub App installation is suspended or lost access             ``at_risk``
2     CommitGuard monitoring is paused                                    ``unprotected``
3     GitHub does not require a CommitGuard check (branch protection)     ``unprotected``
4     the effective policy could not be resolved (propagation ``error``)  ``at_risk``
5     the latest scan failed on invalid CommitGuard configuration         ``at_risk``
6     open critical violations                                            ``at_risk``
7     monitor mode (violations are reported, not blocked)                 ``at_risk``
8     an active exception lowers a high or critical rule                  ``at_risk``
9     branch protection has not been verified                             ``unknown``
10    otherwise                                                           ``secure``
====  ==================================================================  ===============

Organization posture - again the first matching rule decides:

* ``unknown`` - the organization has no repositories;
* ``at_risk`` - any installation suspended, removed or failing to synchronise,
  any repository ``at_risk``, or some (not all) repositories ``unprotected``;
* ``unprotected`` - every repository is proven ``unprotected`` (monitoring paused
  or no CommitGuard check required by GitHub);
* ``unknown`` - otherwise, if the protection of any repository is not verified;
* ``secure`` - every repository is ``secure`` and installations are healthy.

**Compliance** is shown only as a fraction with its definition: "*N of M
required repositories satisfy all mandatory controls*", where required
repositories are onboarded, not archived and connected, and satisfying means
posture ``secure``. It is a policy compliance report - not a SOC 2, ISO or any
other certification, and the reports say so.

Policy drift compares what a repository's own configuration asked for with what
organization governance requires, using the latest scan's recorded provenance
(the repository configuration is only known at scan time):

* ``compliant`` - no conflicts; ``customized`` - the repository changes
  defaults the organization allows it to change; ``drift`` - the repository asks
  for less than a mandatory requirement (the requirement still applies; each
  difference is listed); ``unknown`` - no scan recorded provenance yet.

Staleness: computed values carry ``computed_at``. Trends that cannot be derived
from scan and violation history (protection, exceptions, drift) come from daily
snapshots written by :meth:`SecurityPostureService.snapshot_metrics`, and say so.
"""

import csv
import io
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from commitguard.audit.models import Actor, AuditEventType
from commitguard.controlplane.access import Permission, Principal
from commitguard.controlplane.errors import ConflictError, InputValidationError, NotFoundError
from commitguard.controlplane.pagination import like_pattern
from commitguard.controlplane.queries import DashboardQueries
from commitguard.controlplane.rules import CATALOG
from commitguard.controlplane.views import AppConnection, ProtectionStatus, RepositorySummary
from commitguard.core.result import Severity
from commitguard.github.storage import SqliteStateStore
from commitguard.governance.common import (
    dt,
    is_hex_id,
    req_dt,
    require,
    text,
    ts,
    visible_repository_ids,
)
from commitguard.governance.exceptions import rule_severity
from commitguard.governance.inventory import governance_states
from commitguard.governance.settings import load_settings
from commitguard.policies.governance import EffectivePolicy, RepositoryMode
from commitguard.services.audit import AuditService

Posture = Literal["secure", "at_risk", "unprotected", "unknown"]
Drift = Literal["compliant", "customized", "drift", "unknown"]
SNAPSHOT_MAX_AGE = timedelta(minutes=5)
MATRIX_SORTS = ("name", "posture", "violations", "last_scan")
POSTURE_ORDER = {"at_risk": 0, "unprotected": 1, "unknown": 2, "secure": 3}
REPORT_KINDS = (
    "compliance",
    "violations",
    "coverage",
    "exceptions",
    "policy_changes",
    "installations",
)
#: A synchronisation still running after this long is reported as failed (crashed).
SYNC_STALLED_AFTER = timedelta(minutes=15)
#: Rows per report; a larger result is cut and the summary says ``truncated``.
REPORT_ROW_LIMIT = 10_000
NOT_A_CERTIFICATION = (
    "Policy compliance report generated by CommitGuard. It describes CommitGuard policy "
    "enforcement at the time shown; it is not a SOC 2, ISO 27001 or any other certification "
    "or attestation."
)


class _View(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DriftDifference(_View):
    policy_id: str
    requested: str
    requested_by: str
    required: str
    required_by: str
    effective: str


class RepositoryPosture(_View):
    repository_id: int
    full_name: str
    github_url: str
    installation_id: int
    groups: tuple[dict[str, str], ...]
    connection: str
    archived: bool
    onboarding: str
    mode: RepositoryMode
    protection: ProtectionStatus
    protection_reason: str
    posture: Posture
    posture_reasons: tuple[str, ...]
    organization_policy_version: int | None
    policy_state: str
    last_scan_result: str | None
    last_scan_at: datetime | None
    open_violations: int
    open_warnings: int
    critical_open: int
    active_exceptions: int
    expiring_exceptions: int
    drift: Drift
    drift_differences: tuple[DriftDifference, ...]


class MatrixPage(_View):
    items: tuple[RepositoryPosture, ...]
    total: int
    next_cursor: str | None
    computed_at: datetime


class InstallationHealth(_View):
    installation_id: int
    account_login: str
    state: str  # active | suspended | deleted
    sync: Literal["healthy", "syncing", "degraded", "failed", "never"]
    sync_detail: str
    last_success_at: datetime | None
    repositories: int


class OrganizationPostureView(_View):
    organization_id: int
    login: str
    type: str
    github_url: str
    posture: Posture
    posture_reasons: tuple[str, ...]
    members: int
    repositories: int
    required_repositories: int
    compliant_repositories: int
    compliance: str  # "94 of 100 required repositories satisfy all mandatory controls"
    by_posture: dict[str, int]
    by_protection: dict[str, int]
    monitor_mode: int
    critical_open: int
    high_open: int
    active_exceptions: int
    expiring_exceptions: int
    expired_exceptions_30d: int
    drift: dict[str, int]
    installations: tuple[InstallationHealth, ...]
    policy: dict[str, Any]
    recent_activity: tuple[dict[str, Any], ...]
    computed_at: datetime


class TrendPoint(_View):
    day: str
    values: dict[str, int]


class TrendsView(_View):
    organization_id: int
    days: int
    history: tuple[TrendPoint, ...]  # from scans and violations
    snapshots: tuple[TrendPoint, ...]  # from daily metric snapshots
    snapshot_note: str
    computed_at: datetime


class SearchResult(_View):
    kind: str
    id: str
    title: str
    detail: str
    link: str


def repository_posture(
    *,
    summary: RepositorySummary,
    mode: RepositoryMode,
    policy_state: str,
    high_exceptions: int,
) -> tuple[Posture, tuple[str, ...]]:
    """The explicit posture rules (module docstring). Returns the posture and why."""
    if summary.app_connection is not AppConnection.CONNECTED:
        return "at_risk", (summary.protection_reason,)
    if not summary.monitoring_enabled:
        return "unprotected", ("CommitGuard monitoring is paused.",)
    if summary.protection is ProtectionStatus.UNPROTECTED:
        return "unprotected", (summary.protection_reason,)
    reasons: list[str] = []
    if policy_state == "error":
        reasons.append("The effective policy could not be resolved.")
    if summary.protection is ProtectionStatus.CONFIGURATION_ERROR:
        reasons.append(summary.protection_reason)
    if summary.critical_open:
        reasons.append(f"{summary.critical_open} open critical violation(s).")
    if mode is RepositoryMode.MONITOR:
        reasons.append("Monitor mode: violations are reported but not blocked.")
    if high_exceptions:
        reasons.append(f"{high_exceptions} active exception(s) lower high or critical rules.")
    if reasons:
        return "at_risk", tuple(reasons)
    if summary.protection is ProtectionStatus.UNKNOWN:
        return "unknown", (summary.protection_reason,)
    return "secure", ("Protected, enforcing, no open critical violations.",)


def drift_from(effective: EffectivePolicy | None) -> tuple[Drift, tuple[DriftDifference, ...]]:
    if effective is None:
        return "unknown", ()
    conflicts = tuple(
        DriftDifference(
            policy_id=c.policy_id,
            requested="disabled" if not c.requested_enabled else c.requested_action.value,
            requested_by=c.requested_label,
            required=c.required_action.value,
            required_by=c.required_label,
            effective=c.effective_action.value,
        )
        for c in effective.conflicts
    )
    if conflicts:
        return "drift", conflicts
    customized = any(r.source.value == "repository_configuration" for r in effective.rules)
    return ("customized" if customized else "compliant"), ()


class SecurityPostureService:
    def __init__(
        self,
        store: SqliteStateStore,
        audit: AuditService,
        queries: DashboardQueries,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._audit = audit
        self._queries = queries
        self._now = now

    # -- data ------------------------------------------------------------- #
    def _postures(self, principal: Principal, account_id: int) -> list[RepositoryPosture]:
        """Posture of every repository of the organization the caller can see."""
        scope = principal.scope(Permission.REPOSITORIES_READ, account_id=account_id)
        rows = self._queries.repository_summaries(scope, organization_id=account_id)
        # One row per repository ID: prefer the connected installation's row.
        chosen: dict[int, tuple[sqlite3.Row, RepositorySummary]] = {}
        for row, summary in rows:
            current = chosen.get(summary.id)
            if current is None or (
                current[1].app_connection is not AppConnection.CONNECTED
                and summary.app_connection is AppConnection.CONNECTED
            ):
                chosen[summary.id] = (row, summary)
        states = governance_states(self._store, account_id)
        default_mode = load_settings(self._store, account_id).settings.default_onboarding_mode
        extras = self._repository_extras(account_id, [s.id for _, s in chosen.values()])
        result = []
        for _row, summary in chosen.values():
            extra = extras.get(summary.id, {})
            state = states.get(summary.id)
            mode = state.mode if state else default_mode
            policy_state = str(extra.get("policy_state", "pending"))
            posture, reasons = repository_posture(
                summary=summary,
                mode=mode,
                policy_state=policy_state,
                high_exceptions=int(extra.get("high_exceptions", 0)),
            )
            drift, differences = drift_from(extra.get("effective"))
            result.append(
                RepositoryPosture(
                    repository_id=summary.id,
                    full_name=summary.full_name,
                    github_url=summary.github_url,
                    installation_id=summary.installation_id,
                    groups=tuple(extra.get("groups", ())),
                    connection=summary.app_connection.value,
                    archived=bool(extra.get("archived", False)),
                    onboarding=state.onboarding if state else "discovered",
                    mode=mode,
                    protection=summary.protection,
                    protection_reason=summary.protection_reason,
                    posture=posture,
                    posture_reasons=reasons,
                    organization_policy_version=extra.get("organization_policy_version"),
                    policy_state=policy_state,
                    last_scan_result=summary.last_scan.result.value if summary.last_scan else None,
                    last_scan_at=summary.last_scan.created_at if summary.last_scan else None,
                    open_violations=summary.open_violations,
                    open_warnings=summary.open_warnings,
                    critical_open=summary.critical_open,
                    active_exceptions=int(extra.get("active_exceptions", 0)),
                    expiring_exceptions=int(extra.get("expiring_exceptions", 0)),
                    drift=drift,
                    drift_differences=differences,
                )
            )
        return result

    def _repository_extras(
        self, account_id: int, repository_ids: Sequence[int]
    ) -> dict[int, dict[str, Any]]:
        """Groups, exceptions, propagation and last-scan provenance, in a few queries."""
        now = self._now()
        extras: dict[int, dict[str, Any]] = {i: {"groups": []} for i in repository_ids}
        ids_json = json.dumps(list(repository_ids))
        group_members: dict[str, list[int]] = {}
        for row in self._store.query(
            "SELECT m.repository_id, g.group_id, g.name FROM repository_group_members m JOIN "
            "repository_groups g ON g.group_id = m.group_id WHERE m.account_id = ? "
            "AND g.archived_at IS NULL ORDER BY g.name_key",
            (account_id,),
        ):
            repository_id = int(row["repository_id"])
            group_members.setdefault(str(row["group_id"]), []).append(repository_id)
            if repository_id in extras:
                extras[repository_id]["groups"].append(
                    {"id": str(row["group_id"]), "name": str(row["name"])}
                )
        for row in self._store.query(
            "SELECT rule_id, scope_type, scope_id, expires_at, permanent FROM policy_exceptions "
            "WHERE account_id = ? AND status = 'active' AND (permanent = 1 OR expires_at > ?)",
            (account_id, ts(now)),
        ):
            if row["scope_type"] == "organization":
                targets: Sequence[int] = repository_ids
            elif row["scope_type"] == "group":
                targets = group_members.get(str(row["scope_id"]), [])
            else:
                targets = [int(row["scope_id"])] if str(row["scope_id"]).isdigit() else []
            high = rule_severity(str(row["rule_id"])).rank >= Severity.HIGH.rank
            expiring = (
                not row["permanent"]
                and row["expires_at"] is not None
                and float(row["expires_at"]) - ts(now) <= timedelta(days=7).total_seconds()
            )
            for repository_id in targets:
                if repository_id not in extras:
                    continue
                entry = extras[repository_id]
                entry["active_exceptions"] = entry.get("active_exceptions", 0) + 1
                if high:
                    entry["high_exceptions"] = entry.get("high_exceptions", 0) + 1
                if expiring:
                    entry["expiring_exceptions"] = entry.get("expiring_exceptions", 0) + 1
        for row in self._store.query(
            "SELECT repository_id, state, valid_until FROM repository_effective_policies "
            "WHERE account_id = ? AND repository_id IN (SELECT value FROM json_each(?))",
            (account_id, ids_json),
        ):
            state = str(row["state"])
            if (
                state == "up_to_date"
                and row["valid_until"]
                and float(row["valid_until"]) <= ts(now)
            ):
                state = "stale"
            extras[int(row["repository_id"])]["policy_state"] = state
        for row in self._store.query(
            "SELECT k.repository_id, MAX(k.archived) AS archived FROM known_repositories k "
            "JOIN installations i ON i.installation_id = k.installation_id WHERE "
            "i.account_id = ? GROUP BY k.repository_id",
            (account_id,),
        ):
            if int(row["repository_id"]) in extras:
                extras[int(row["repository_id"])]["archived"] = bool(row["archived"])
        for row in self._store.query(
            "SELECT j.repository_id, j.governance, j.organization_policy_version FROM scan_jobs j "
            "JOIN installations i ON i.installation_id = j.installation_id WHERE "
            "i.account_id = ? AND j.governance IS NOT NULL AND j.sequence = (SELECT "
            "MAX(n.sequence) "
            "FROM scan_jobs n WHERE n.installation_id = j.installation_id AND n.repository_id = "
            "j.repository_id AND n.governance IS NOT NULL)",
            (account_id,),
        ):
            repository_id = int(row["repository_id"])
            if repository_id not in extras:
                continue
            extras[repository_id]["organization_policy_version"] = row[
                "organization_policy_version"
            ]
            try:
                record = json.loads(str(row["governance"]))
                effective = record.get("effective")
                extras[repository_id]["effective"] = (
                    EffectivePolicy.model_validate(effective) if effective else None
                )
            except (ValueError, TypeError):
                continue
        return extras

    # -- organization ----------------------------------------------------- #
    def _organization_ref(self, principal: Principal, account_id: int) -> tuple[str, str]:
        membership = principal.memberships.get(account_id)
        if membership is None:
            raise NotFoundError()
        return membership.account_login, membership.account_type

    def installations(self, account_id: int) -> tuple[InstallationHealth, ...]:
        now = self._now()
        rows = self._store.query(
            "SELECT i.installation_id, i.account_login, i.state, s.state AS sync_state, "
            "s.started_at, s.last_success_at, s.error, s.repositories, (SELECT COUNT(*) FROM "
            "installation_repositories r WHERE r.installation_id = i.installation_id) AS listed "
            "FROM installations i LEFT JOIN installation_sync_status s ON s.installation_id = "
            "i.installation_id WHERE i.account_id = ? AND i.state != 'deleted' "
            "ORDER BY i.installation_id",
            (account_id,),
        )
        result = []
        for row in rows:
            last_success = dt(row["last_success_at"])
            sync: Literal["healthy", "syncing", "degraded", "failed", "never"]
            if row["sync_state"] == "failed":
                sync, detail = "failed", str(row["error"] or "The last synchronisation failed.")
            elif row["sync_state"] == "syncing" and (
                row["started_at"] is None or now - req_dt(row["started_at"]) > SYNC_STALLED_AFTER
            ):
                sync, detail = "failed", "The last synchronisation did not finish."
            elif row["sync_state"] == "syncing":
                sync, detail = "syncing", "Synchronising repositories with GitHub."
            elif row["state"] != "active":
                sync, detail = "degraded", f"The installation is {row['state']}."
            elif last_success is None:
                sync, detail = (
                    "never",
                    "Repositories are known from GitHub events; no full synchronisation yet.",
                )
            elif now - last_success > timedelta(days=7):
                sync, detail = "degraded", "Not synchronised with GitHub in the last 7 days."
            else:
                sync, detail = "healthy", "Synchronised with GitHub."
            result.append(
                InstallationHealth(
                    installation_id=int(row["installation_id"]),
                    account_login=str(row["account_login"]),
                    state=str(row["state"]),
                    sync=sync,
                    sync_detail=detail,
                    last_success_at=last_success,
                    repositories=int(row["listed"]),
                )
            )
        return tuple(result)

    def overview(self, principal: Principal, account_id: int) -> OrganizationPostureView:
        require(principal, Permission.SECURITY_READ, account_id)
        login, account_type = self._organization_ref(principal, account_id)
        now = self._now()
        postures = self._postures(principal, account_id)
        states = governance_states(self._store, account_id)
        installations = self.installations(account_id)
        required = [
            p
            for p in postures
            if p.connection == "connected"
            and not p.archived
            and (
                states[p.repository_id].onboarding == "onboarded"
                if p.repository_id in states
                else True
            )
        ]
        compliant = sum(1 for p in required if p.posture == "secure")
        by_posture = {k: sum(1 for p in postures if p.posture == k) for k in POSTURE_ORDER}
        by_protection = {
            status.value: sum(1 for p in postures if p.protection is status)
            for status in ProtectionStatus
        }
        reasons: list[str] = []
        unhealthy = [i for i in installations if i.state != "active" or i.sync == "failed"]
        if not postures:
            posture: Posture = "unknown"
            reasons.append("No repositories are connected to CommitGuard yet.")
        elif (
            unhealthy
            or by_posture["at_risk"]
            or (by_posture["unprotected"] and by_posture["unprotected"] < len(postures))
        ):
            posture = "at_risk"
            for installation in unhealthy:
                reasons.append(
                    f"Installation {installation.account_login} ({installation.installation_id}): "
                    f"{installation.sync_detail}"
                )
            if by_posture["at_risk"]:
                reasons.append(f"{by_posture['at_risk']} repository(ies) at risk.")
            if by_posture["unprotected"]:
                reasons.append(f"{by_posture['unprotected']} repository(ies) unprotected.")
        elif by_posture["unprotected"] == len(postures):
            posture = "unprotected"
            reasons.append(
                "No repository is protected: CommitGuard checks do not block merges anywhere."
            )
        elif by_posture["unknown"]:
            posture = "unknown"
            reasons.append(
                f"Branch protection of {by_posture['unknown']} repository(ies) is not verified."
            )
        else:
            posture = "secure"
            reasons.append("Every repository is protected and enforcing; installations healthy.")
        members = int(
            self._store.query(
                "SELECT COUNT(*) AS n FROM memberships WHERE account_id = ?", (account_id,)
            )[0]["n"]
        )
        exception_counts = self._store.query(
            "SELECT SUM(status = 'active' AND (permanent = 1 OR expires_at > ?)) AS active, "
            "SUM(status = 'active' AND permanent = 0 AND expires_at > ? AND expires_at <= ?) "
            "AS expiring, SUM(status = 'expired' AND expired_at >= ?) AS expired, "
            "SUM(status = 'requested') AS requested FROM policy_exceptions WHERE account_id = ?",
            (
                ts(now),
                ts(now),
                ts(now + timedelta(days=7)),
                ts(now - timedelta(days=30)),
                account_id,
            ),
        )[0]
        return OrganizationPostureView(
            organization_id=account_id,
            login=login,
            type=account_type,
            github_url=f"https://github.com/{login}",
            posture=posture,
            posture_reasons=tuple(reasons),
            members=members,
            repositories=len(postures),
            required_repositories=len(required),
            compliant_repositories=compliant,
            compliance=(
                f"{compliant} of {len(required)} required repositories satisfy all mandatory "
                "controls"
            ),
            by_posture=by_posture,
            by_protection=by_protection,
            monitor_mode=sum(1 for p in postures if p.mode is RepositoryMode.MONITOR),
            critical_open=sum(p.critical_open for p in postures),
            high_open=self._high_open(account_id, [p.repository_id for p in postures]),
            active_exceptions=int(exception_counts["active"] or 0),
            expiring_exceptions=int(exception_counts["expiring"] or 0),
            expired_exceptions_30d=int(exception_counts["expired"] or 0),
            drift={
                k: sum(1 for p in postures if p.drift == k)
                for k in ("compliant", "customized", "drift", "unknown")
            },
            installations=installations,
            policy=self._policy_status(account_id, int(exception_counts["requested"] or 0)),
            recent_activity=self._recent_activity(principal, account_id),
            computed_at=now,
        )

    def _high_open(self, account_id: int, repository_ids: Sequence[int]) -> int:
        """Open high violations of the repositories the caller can see."""
        return int(
            self._store.query(
                "SELECT COUNT(*) AS n FROM violations v JOIN installations i ON "
                "i.installation_id = v.installation_id WHERE i.account_id = ? "
                "AND v.status = 'open' AND v.severity = 'high' "
                "AND v.repository_id IN (SELECT value FROM json_each(?))",
                (account_id, json.dumps(sorted(set(repository_ids)))),
            )[0]["n"]
        )

    def _policy_status(self, account_id: int, requested_exceptions: int) -> dict[str, Any]:
        latest = self._store.query(
            "SELECT version, created_at, created_by_login FROM organization_policy_versions "
            "WHERE account_id = ? ORDER BY version DESC LIMIT 1",
            (account_id,),
        )
        pending = self._store.query(
            "SELECT COUNT(*) AS n FROM policy_drafts WHERE account_id = ? "
            "AND state = 'pending_approval'",
            (account_id,),
        )[0]["n"]
        rollouts = self._store.query(
            "SELECT COUNT(*) AS n FROM policy_rollouts WHERE account_id = ? "
            "AND state IN ('pilot', 'rollout', 'paused')",
            (account_id,),
        )[0]["n"]
        propagation = {
            str(r["state"]): int(r["n"])
            for r in self._store.query(
                "SELECT state, COUNT(*) AS n FROM repository_effective_policies "
                "WHERE account_id = ? GROUP BY state",
                (account_id,),
            )
        }
        baseline = load_settings(self._store, account_id).settings.security_baseline
        return {
            "organization_version": int(latest[0]["version"]) if latest else 0,
            "updated_at": req_dt(latest[0]["created_at"]).isoformat() if latest else None,
            "updated_by": latest[0]["created_by_login"] if latest else None,
            "approvals_pending": int(pending),
            "exceptions_requested": requested_exceptions,
            "rollouts_in_progress": int(rollouts),
            "propagation": propagation,
            "baseline": {k: v.value for k, v in baseline.items()},
        }

    def _recent_activity(self, principal: Principal, account_id: int) -> tuple[dict[str, Any], ...]:
        if not principal.can(Permission.AUDIT_READ, account_id):
            return ()
        types = (
            "organization_policy_changed",
            "organization_policy_rolled_back",
            "policy_published",
            "policy_emergency_published",
            "exception_approved",
            "exception_revoked",
            "exception_expired",
            "installation_suspended",
            "installation_removed",
            "installation_unsuspended",
            "policy_rollout_paused",
            "organization_settings_changed",
            "repository_mode_changed",
        )
        rows = self._store.query(
            "SELECT event_id, occurred_at, type, actor_login, repository_id FROM audit_events "
            "WHERE account_id = ? AND type IN (SELECT value FROM json_each(?)) "
            "ORDER BY occurred_at DESC LIMIT 12",
            (account_id, json.dumps(types)),
        )
        return tuple(
            {
                "id": str(r["event_id"]),
                "type": str(r["type"]),
                "occurred_at": req_dt(r["occurred_at"]).isoformat(),
                "actor": r["actor_login"],
            }
            for r in rows
        )

    # -- matrix ----------------------------------------------------------- #
    def matrix(
        self,
        principal: Principal,
        account_id: int,
        *,
        filters: Mapping[str, str | None],
        offset: int,
        limit: int,
    ) -> MatrixPage:
        require(principal, Permission.SECURITY_READ, account_id)
        now = self._now()
        items = self._postures(principal, account_id)
        q = filters.get("q")
        if q:
            needle = q.casefold()
            items = [p for p in items if needle in p.full_name.casefold()]
        group = filters.get("group")
        if group:
            if not is_hex_id(group):
                raise InputValidationError("group must be a group ID", field="group")
            items = [p for p in items if any(g["id"] == group for g in p.groups)]
        checks: dict[str, tuple[set[str], Callable[[RepositoryPosture], str]]] = {
            "posture": ({"secure", "at_risk", "unprotected", "unknown"}, lambda p: p.posture),
            "protection": ({s.value for s in ProtectionStatus}, lambda p: p.protection.value),
            "mode": ({"monitor", "enforce"}, lambda p: p.mode.value),
            "onboarding": ({"discovered", "onboarded", "excluded"}, lambda p: p.onboarding),
            "policy_state": (
                {"up_to_date", "stale", "syncing", "error", "pending"},
                lambda p: p.policy_state,
            ),
            "drift": ({"compliant", "customized", "drift", "unknown"}, lambda p: p.drift),
        }
        for name, (allowed, getter) in checks.items():
            value = filters.get(name)
            if value:
                if value not in allowed:
                    raise InputValidationError(f"unknown {name}", field=name)
                items = [p for p in items if getter(p) == value]
        exceptions = filters.get("exceptions")
        if exceptions == "active":
            items = [p for p in items if p.active_exceptions]
        elif exceptions == "expiring":
            items = [p for p in items if p.expiring_exceptions]
        elif exceptions == "none":
            items = [p for p in items if not p.active_exceptions]
        elif exceptions:
            raise InputValidationError("exceptions must be active, expiring or none")
        severity = filters.get("severity")
        if severity == "critical":
            items = [p for p in items if p.critical_open]
        elif severity == "any":
            items = [p for p in items if p.open_violations or p.open_warnings]
        elif severity:
            raise InputValidationError("severity must be critical or any", field="severity")
        last_scan = filters.get("last_scan")
        if last_scan == "never":
            items = [p for p in items if p.last_scan_at is None]
        elif last_scan and last_scan.isdigit() and 0 < int(last_scan) <= 365:
            cutoff = now - timedelta(days=int(last_scan))
            items = [p for p in items if p.last_scan_at is None or p.last_scan_at < cutoff]
        elif last_scan:
            raise InputValidationError(
                "last_scan must be never or a number of days", field="last_scan"
            )
        sort = filters.get("sort") or "posture"
        if sort not in MATRIX_SORTS:
            raise InputValidationError("unknown sort", field="sort")
        epoch = datetime.fromtimestamp(0, UTC)
        keys: dict[str, Callable[[RepositoryPosture], Any]] = {
            "name": lambda p: p.full_name.casefold(),
            "posture": lambda p: (POSTURE_ORDER[p.posture], -p.critical_open, p.full_name),
            "violations": lambda p: (-p.critical_open, -p.open_violations, p.full_name),
            "last_scan": lambda p: (p.last_scan_at or epoch, p.full_name),
        }
        items.sort(key=keys[sort])
        page = items[offset : offset + limit]
        from commitguard.controlplane.pagination import encode_cursor

        return MatrixPage(
            items=tuple(page),
            total=len(items),
            next_cursor=encode_cursor([offset + limit]) if len(items) > offset + limit else None,
            computed_at=now,
        )

    # -- trends and snapshots --------------------------------------------- #
    def snapshot_metrics(self) -> int:
        """Write today's metrics snapshot per organization (background job)."""
        now = self._now()
        day = now.date().isoformat()
        written = 0
        for row in self._store.query(
            "SELECT DISTINCT account_id FROM installations WHERE state != 'deleted'"
        ):
            account_id = int(row["account_id"])
            existing = self._store.query(
                "SELECT computed_at FROM security_metric_snapshots WHERE account_id = ? AND day = "
                "?",
                (account_id, day),
            )
            if existing and now - req_dt(existing[0]["computed_at"]) < timedelta(hours=1):
                continue
            document = self._system_metrics(account_id)
            with self._store.transaction() as db:
                db.execute(
                    "INSERT INTO security_metric_snapshots (account_id, day, computed_at, "
                    "document) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT (account_id, day) DO UPDATE SET "
                    "computed_at = excluded.computed_at, document = excluded.document",
                    (account_id, day, ts(now), json.dumps(document, sort_keys=True)),
                )
            written += 1
        return written

    def _system_metrics(self, account_id: int) -> dict[str, int]:
        """Counts that do not depend on a session's visibility (organization totals)."""
        now = self._now()
        protection = {
            str(r["branch_protection"]): int(r["n"])
            for r in self._store.query(
                "SELECT e.branch_protection, COUNT(*) AS n FROM enforcement_status e JOIN "
                "installations i ON i.installation_id = e.installation_id WHERE i.account_id = ? "
                "AND i.state = 'active' GROUP BY e.branch_protection",
                (account_id,),
            )
        }
        violations = self._store.query(
            "SELECT SUM(v.severity = 'critical') AS critical, COUNT(*) AS open FROM violations v "
            "JOIN installations i ON i.installation_id = v.installation_id WHERE i.account_id = ? "
            "AND v.status = 'open'",
            (account_id,),
        )[0]
        exceptions = self._store.query(
            "SELECT COUNT(*) AS n FROM policy_exceptions WHERE account_id = ? AND status = "
            "'active' "
            "AND (permanent = 1 OR expires_at > ?)",
            (account_id, ts(now)),
        )[0]["n"]
        drift = 0
        for row in self._store.query(
            "SELECT j.governance FROM scan_jobs j JOIN installations i ON i.installation_id = "
            "j.installation_id WHERE i.account_id = ? AND j.governance IS NOT NULL AND "
            "j.sequence = (SELECT MAX(n.sequence) FROM scan_jobs n WHERE n.installation_id = "
            "j.installation_id AND n.repository_id = j.repository_id AND n.governance IS NOT NULL)",
            (account_id,),
        ):
            try:
                effective = json.loads(str(row["governance"])).get("effective") or {}
            except ValueError:
                continue
            if any(r.get("conflict") for r in effective.get("rules", [])):
                drift += 1
        return {
            "protected_repositories": protection.get("required", 0),
            "unprotected_repositories": protection.get("not_required", 0),
            "open_violations": int(violations["open"] or 0),
            "critical_open": int(violations["critical"] or 0),
            "active_exceptions": int(exceptions),
            "drift_repositories": drift,
        }

    def trends(self, principal: Principal, account_id: int, *, days: int) -> TrendsView:
        require(principal, Permission.SECURITY_READ, account_id)
        if not 1 <= days <= 365:
            raise InputValidationError("days must be between 1 and 365", field="days")
        now = self._now()
        start = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
        history: dict[str, dict[str, int]] = {}
        for row in self._store.query(
            "SELECT date(j.completed_at, 'unixepoch') AS day, COUNT(*) AS scans, "
            "SUM(j.state = 'failed') AS blocked, SUM(j.state = 'error') AS errors FROM scan_jobs j "
            "JOIN installations i ON i.installation_id = j.installation_id WHERE i.account_id = ? "
            "AND j.completed_at >= ? GROUP BY day",
            (account_id, ts(start)),
        ):
            history.setdefault(str(row["day"]), {}).update(
                scans=int(row["scans"]),
                blocked_scans=int(row["blocked"] or 0),
                scan_errors=int(row["errors"] or 0),
            )
        for row in self._store.query(
            "SELECT date(v.first_detected_at, 'unixepoch') AS day, COUNT(*) AS violations, "
            "SUM(v.severity = 'critical') AS critical FROM violations v JOIN installations i ON "
            "i.installation_id = v.installation_id WHERE i.account_id = ? "
            "AND v.first_detected_at >= ? GROUP BY day",
            (account_id, ts(start)),
        ):
            history.setdefault(str(row["day"]), {}).update(
                new_violations=int(row["violations"]),
                new_critical=int(row["critical"] or 0),
            )
        snapshots = [
            TrendPoint(day=str(r["day"]), values=json.loads(str(r["document"])))
            for r in self._store.query(
                "SELECT day, document FROM security_metric_snapshots WHERE account_id = ? "
                "AND day >= ? ORDER BY day",
                (account_id, start.date().isoformat()),
            )
        ]
        return TrendsView(
            organization_id=account_id,
            days=days,
            history=tuple(
                TrendPoint(day=day, values=values) for day, values in sorted(history.items())
            ),
            snapshots=tuple(snapshots),
            snapshot_note=(
                "Protection, exceptions and drift over time come from daily snapshots taken by "
                "CommitGuard; days before the first snapshot have no data."
            ),
            computed_at=now,
        )

    # -- reports ---------------------------------------------------------- #
    def report(
        self, principal: Principal, account_id: int, *, kind: str, fmt: str
    ) -> tuple[str, bytes, str]:
        """(content type, body, filename). A point-in-time snapshot, audited."""
        require(principal, Permission.SECURITY_READ, account_id)
        if kind not in REPORT_KINDS:
            raise InputValidationError(
                f"report must be one of {', '.join(REPORT_KINDS)}", field="report"
            )
        if fmt not in ("json", "csv"):
            raise InputValidationError("format must be json or csv", field="format")
        if kind in ("policy_changes",) and not principal.can(Permission.AUDIT_READ, account_id):
            raise NotFoundError()
        now = self._now()
        login, _ = self._organization_ref(principal, account_id)
        rows, summary = self._report_rows(principal, account_id, kind)
        document = {
            "report": kind,
            "organization": login,
            "organization_id": account_id,
            "generated_at": now.isoformat(),
            "generated_by": principal.login,
            "notice": NOT_A_CERTIFICATION,
            "summary": summary,
            "rows": rows,
        }
        with self._store.transaction() as db:
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.REPORT_EXPORTED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=account_id,
                    report=kind,
                    format=fmt,
                    rows=len(rows),
                ),
            )
        self._audit.log_stored(stored)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        filename = f"commitguard-{login}-{kind}-{stamp}.{fmt}"
        if fmt == "json":
            body = json.dumps(document, indent=2, sort_keys=True, default=str).encode("utf-8")
            return "application/json", body, filename
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([f"# {NOT_A_CERTIFICATION}"])
        writer.writerow([f"# organization={login} generated_at={now.isoformat()} report={kind}"])
        columns = sorted({key for row in rows for key in row}) if rows else ["empty"]
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_csv_cell(row.get(column)) for column in columns])
        return "text/csv; charset=utf-8", buffer.getvalue().encode("utf-8"), filename

    def _report_rows(
        self, principal: Principal, account_id: int, kind: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if kind in ("compliance", "coverage"):
            overview = self.overview(principal, account_id)
            postures = self._postures(principal, account_id)
            rows = [
                {
                    "repository": p.full_name,
                    "posture": p.posture,
                    "reasons": "; ".join(p.posture_reasons),
                    "protection": p.protection.value,
                    "connection": p.connection,
                    "mode": p.mode.value,
                    "onboarding": p.onboarding,
                    "policy_state": p.policy_state,
                    "organization_policy_version": p.organization_policy_version,
                    "open_violations": p.open_violations,
                    "critical_open": p.critical_open,
                    "active_exceptions": p.active_exceptions,
                    "drift": p.drift,
                    "last_scan_at": p.last_scan_at.isoformat() if p.last_scan_at else None,
                }
                for p in sorted(postures, key=lambda p: p.full_name.casefold())
            ]
            summary = {
                "posture": overview.posture,
                "repositories": overview.repositories,
                "protected": overview.by_protection.get("protected", 0),
                "at_risk": overview.by_posture.get("at_risk", 0),
                "unprotected": overview.by_posture.get("unprotected", 0),
                "critical_findings": overview.critical_open,
                "active_exceptions": overview.active_exceptions,
                "compliance": overview.compliance,
            }
            return rows, summary
        if kind == "violations":
            scope = principal.scope(Permission.VIOLATIONS_READ, account_id=account_id)
            rows = [
                {
                    "rule": r["rule_id"],
                    "severity": r["severity"],
                    "action": r["action"],
                    "status": r["status"],
                    "repository": f"{r['owner']}/{r['name']}",
                    "open": int(r["n"]),
                }
                for r in self._store.query(
                    "SELECT v.rule_id, v.severity, v.action, v.status, k.owner, k.name, COUNT(*) "
                    "AS n FROM violations v JOIN known_repositories k ON k.installation_id = "
                    "v.installation_id AND k.repository_id = v.repository_id WHERE "
                    "v.installation_id IN (SELECT value FROM json_each(?)) AND EXISTS (SELECT 1 "
                    "FROM session_repositories sr WHERE sr.session_hash = ? AND sr.installation_id "
                    "= v.installation_id AND sr.repository_id = v.repository_id) GROUP BY "
                    "v.rule_id, v.severity, v.action, v.status, k.owner, k.name LIMIT ?",
                    (scope.installations_json, scope.session_hash, REPORT_ROW_LIMIT),
                )
            ]
            return rows, {"groups": len(rows), "truncated": len(rows) >= REPORT_ROW_LIMIT}
        if kind == "exceptions":
            rows = [
                {
                    "id": r["exception_id"],
                    "rule": r["rule_id"],
                    "scope": r["scope_type"],
                    "scope_id": r["scope_id"],
                    "action": r["action"],
                    "status": r["status"],
                    "reason": r["reason"],
                    "requested_by": r["requested_by_login"],
                    "approved_by": r["decided_by_login"],
                    "expires_at": req_dt(r["expires_at"]).isoformat() if r["expires_at"] else None,
                    "permanent": bool(r["permanent"]),
                }
                for r in self._store.query(
                    "SELECT * FROM policy_exceptions WHERE account_id = ? ORDER BY requested_at "
                    "DESC LIMIT ?",
                    (account_id, REPORT_ROW_LIMIT),
                )
                if r["scope_type"] != "repository"
                or int(r["scope_id"]) in self._visible(principal, account_id)
            ]
            return rows, {"exceptions": len(rows), "truncated": len(rows) >= REPORT_ROW_LIMIT}
        if kind == "policy_changes":
            rows = [
                {
                    "occurred_at": req_dt(r["occurred_at"]).isoformat(),
                    "type": r["type"],
                    "actor": r["actor_login"],
                    "details": json.loads(str(r["document"])).get("data", {}),
                }
                for r in self._store.query(
                    "SELECT occurred_at, type, actor_login, document FROM audit_events WHERE "
                    "account_id = ? AND type IN ('organization_policy_changed', "
                    "'organization_policy_rolled_back', 'policy_published', 'policy_rolled_back', "
                    "'policy_emergency_published', 'policy_approved', 'policy_rejected', "
                    "'organization_settings_changed', 'organization_rules_changed') "
                    "ORDER BY occurred_at DESC LIMIT ?",
                    (account_id, REPORT_ROW_LIMIT),
                )
            ]
            return rows, {"changes": len(rows), "truncated": len(rows) >= REPORT_ROW_LIMIT}
        rows = [i.model_dump(mode="json") for i in self.installations(account_id)]
        return rows, {"installations": len(rows)}

    def _visible(self, principal: Principal, account_id: int) -> set[int]:
        return visible_repository_ids(self._store, principal, account_id)

    # -- search ----------------------------------------------------------- #
    def search(self, principal: Principal, account_id: int, q: str) -> list[SearchResult]:
        require(principal, Permission.ORGANIZATION_READ, account_id)
        needle = q.strip()
        if not 2 <= len(needle) <= 100:
            raise InputValidationError("q must be 2-100 characters", field="q")
        pattern = like_pattern(needle)
        results: list[SearchResult] = []
        visible = self._visible(principal, account_id)
        if principal.can(Permission.REPOSITORIES_READ, account_id):
            for r in self._store.query(
                "SELECT DISTINCT k.repository_id, k.owner, k.name FROM known_repositories k JOIN "
                "installations i ON i.installation_id = k.installation_id WHERE i.account_id = ? "
                "AND (k.owner || '/' || k.name) LIKE ? ESCAPE '\\' ORDER BY k.name LIMIT 30",
                (account_id, pattern),
            ):
                if int(r["repository_id"]) in visible and len(results) < 10:
                    results.append(
                        SearchResult(
                            kind="repository",
                            id=str(r["repository_id"]),
                            title=f"{r['owner']}/{r['name']}",
                            detail="Repository",
                            link=f"/repositories/{r['repository_id']}",
                        )
                    )
            for r in self._store.query(
                "SELECT group_id, name, description FROM repository_groups WHERE account_id = ? "
                "AND archived_at IS NULL AND name LIKE ? ESCAPE '\\' LIMIT 10",
                (account_id, pattern),
            ):
                results.append(
                    SearchResult(
                        kind="group",
                        id=str(r["group_id"]),
                        title=str(r["name"]),
                        detail=str(r["description"] or "Repository group"),
                        link=f"/organization/groups/{r['group_id']}",
                    )
                )
        if principal.can(Permission.POLICIES_READ, account_id):
            for r in self._store.query(
                "SELECT draft_id, title, state FROM policy_drafts WHERE account_id = ? "
                "AND title LIKE ? ESCAPE '\\' ORDER BY updated_at DESC LIMIT 10",
                (account_id, pattern),
            ):
                results.append(
                    SearchResult(
                        kind="policy",
                        id=str(r["draft_id"]),
                        title=str(r["title"]),
                        detail=f"Policy draft ({r['state']})",
                        link=f"/organization/policies/drafts/{r['draft_id']}",
                    )
                )
        if principal.can(Permission.RULES_READ, account_id):
            lowered = needle.casefold()
            for entry in CATALOG:
                if lowered in entry.rule_id or lowered in entry.name.casefold():
                    results.append(
                        SearchResult(
                            kind="rule",
                            id=entry.rule_id,
                            title=entry.name,
                            detail=f"Rule {entry.rule_id} ({entry.severity.value})",
                            link=f"/rules/{entry.rule_id}",
                        )
                    )
        if principal.can(Permission.EXCEPTIONS_READ, account_id):
            for r in self._store.query(
                "SELECT exception_id, rule_id, scope_type, scope_id, status, reason FROM "
                "policy_exceptions WHERE account_id = ? AND (rule_id LIKE ? ESCAPE '\\' OR reason "
                "LIKE ? ESCAPE '\\') ORDER BY requested_at DESC LIMIT 20",
                (account_id, pattern, pattern),
            ):
                if r["scope_type"] == "repository" and int(r["scope_id"]) not in visible:
                    continue
                results.append(
                    SearchResult(
                        kind="exception",
                        id=str(r["exception_id"]),
                        title=f"Exception: {r['rule_id']} ({r['status']})",
                        detail=str(r["reason"])[:120],
                        link=f"/organization/exceptions/{r['exception_id']}",
                    )
                )
        if principal.can(Permission.VIOLATIONS_READ, account_id):
            scope = principal.scope(Permission.VIOLATIONS_READ, account_id=account_id)
            for r in self._store.query(
                "SELECT v.violation_id, v.rule_id, v.title, v.commit_sha, v.status FROM violations "
                "v WHERE v.installation_id IN (SELECT value FROM json_each(?)) AND EXISTS (SELECT "
                "1 FROM session_repositories sr WHERE sr.session_hash = ? AND sr.installation_id = "
                "v.installation_id AND sr.repository_id = v.repository_id) AND (v.rule_id LIKE ? "
                "ESCAPE '\\' OR v.title LIKE ? ESCAPE '\\' OR v.commit_sha LIKE ?) "
                "ORDER BY v.last_detected_at DESC LIMIT 10",
                (
                    scope.installations_json,
                    scope.session_hash,
                    pattern,
                    pattern,
                    f"{needle.lower()}%" if needle.isalnum() else "\x00",
                ),
            ):
                results.append(
                    SearchResult(
                        kind="finding",
                        id=str(r["violation_id"]),
                        title=str(r["title"]),
                        detail=f"{r['rule_id']} · {r['status']} · {str(r['commit_sha'])[:12]}",
                        link=f"/violations/{r['violation_id']}",
                    )
                )
        return results[:50]

    # -- acknowledgement -------------------------------------------------- #
    def acknowledge_event(
        self, principal: Principal, account_id: int, event_id: str, note: object
    ) -> dict[str, Any]:
        """Record that someone saw a critical security event. It does not resolve anything."""
        require(principal, Permission.VIOLATIONS_MANAGE, account_id)
        if not is_hex_id(event_id):
            raise NotFoundError()
        rows = self._store.query(
            "SELECT event_id, severity, type, title FROM notification_events WHERE event_id = ? "
            "AND account_id = ?",
            (event_id, account_id),
        )
        if not rows:
            raise NotFoundError()
        if rows[0]["severity"] not in ("critical", "high"):
            raise ConflictError("Only critical and high security events are acknowledged.")
        note_text = text(note, "note", limit=500)
        now = self._now()
        with self._store.transaction() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO notification_acknowledgements (event_id, account_id, "
                "acknowledged_by_id, acknowledged_by_login, acknowledged_at, note) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, account_id, principal.user_id, principal.login, ts(now), note_text),
            )
            if cursor.rowcount != 1:
                raise ConflictError("This event was already acknowledged.")
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.SECURITY_EVENT_ACKNOWLEDGED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=account_id,
                    notification_event=event_id,
                    notification_type=str(rows[0]["type"]),
                    note=note_text,
                ),
            )
        self._audit.log_stored(stored)
        return {
            "event_id": event_id,
            "acknowledged_by": principal.login,
            "acknowledged_at": now.isoformat(),
            "meaning": "An administrator has seen this event. It does not resolve any violation.",
        }

    def security_events(self, principal: Principal, account_id: int) -> list[dict[str, Any]]:
        """Recent critical and high organization events with their acknowledgement state."""
        require(principal, Permission.SECURITY_READ, account_id)
        rows = self._store.query(
            "SELECT e.event_id, e.type, e.severity, e.title, e.body, e.repository_id, "
            "e.last_occurred_at, e.occurrences, a.acknowledged_by_login, a.acknowledged_at FROM "
            "notification_events e LEFT JOIN notification_acknowledgements a ON a.event_id = "
            "e.event_id WHERE e.account_id = ? AND e.severity IN ('critical', 'high') "
            "ORDER BY e.last_occurred_at DESC LIMIT 50",
            (account_id,),
        )
        visible = self._visible(principal, account_id)
        return [
            {
                "id": str(r["event_id"]),
                "type": str(r["type"]),
                "severity": str(r["severity"]),
                "title": str(r["title"]),
                "body": str(r["body"]),
                "occurrences": int(r["occurrences"]),
                "last_occurred_at": req_dt(r["last_occurred_at"]).isoformat(),
                "acknowledged_by": r["acknowledged_by_login"],
                "acknowledged_at": req_dt(r["acknowledged_at"]).isoformat()
                if r["acknowledged_at"]
                else None,
            }
            for r in rows
            if r["repository_id"] is None or int(r["repository_id"]) in visible
        ]


def _csv_cell(value: object) -> str:
    """CSV cells that a spreadsheet will not execute (formula injection)."""
    if value is None:
        return ""
    cell = json.dumps(value, sort_keys=True) if isinstance(value, dict | list) else str(value)
    if cell[:1] in ("=", "+", "-", "@", "\t", "\r"):
        cell = "'" + cell
    return cell
