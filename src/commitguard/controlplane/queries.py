"""Read-side services for the dashboard API.

Tenant isolation
================

Every public method takes an :class:`~commitguard.controlplane.access.AccessScope`
and every statement filters on it:

* ``installation_id IN (SELECT value FROM json_each(?))`` - installations of
  accounts where the user holds the required permission and that GitHub
  reported as accessible at sign-in;
* ``EXISTS (SELECT 1 FROM session_repositories ...)`` - the repository was in
  the user's GitHub-reported repository list for this session.

A row outside the scope is indistinguishable from a row that does not exist:
detail lookups return ``None`` (the API answers 404) rather than "forbidden".

SQL safety: statements are assembled only from the constant fragments in this
module (filters present or absent, one of a fixed set of ``ORDER BY`` clauses);
every value, including search text and cursor positions, is a bound
parameter. Search uses ``LIKE ... ESCAPE '\\'`` on escaped input.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from sqlite3 import Row
from typing import Any, Literal

from commitguard.audit.models import AuditEvent, AuditEventType
from commitguard.controlplane.access import AccessScope, Permission, Principal
from commitguard.controlplane.pagination import (
    Page,
    decode_cursor,
    encode_cursor,
    like_pattern,
    sha_prefix,
)
from commitguard.controlplane.results import scan_result_label
from commitguard.controlplane.rules import remediation_steps
from commitguard.controlplane.views import (
    AcknowledgementView,
    ActorView,
    AppConnection,
    AuditEventView,
    DetectionView,
    EnforcementSignal,
    EnforcementView,
    EvidenceView,
    ExecutionHistory,
    ExecutionView,
    ExposureView,
    FindingView,
    HealthCheck,
    HealthCheckStatus,
    InstallationDetail,
    InstallationRepositoryView,
    InstallationView,
    IntegrationStatus,
    IntegrationView,
    LatestCheckSignal,
    MatchView,
    MergeGroupView,
    MergeQueueStatus,
    MergeQueueView,
    OrganizationRef,
    OverviewPeriod,
    OverviewSummary,
    OverviewView,
    PolicyEntry,
    ProtectionStatus,
    RepositoryDetail,
    RepositoryLink,
    RepositoryPermissions,
    RepositorySummary,
    RequiredCheckSignal,
    RequiredCheckStatus,
    ScanComparison,
    ScanDetail,
    ScanFailure,
    ScanResultStatus,
    ScanSummary,
    ViolationDetail,
    ViolationStatus,
    ViolationSummary,
)
from commitguard.core.decision import Action
from commitguard.core.result import Severity
from commitguard.github.permissions import (
    REQUIRED_PERMISSIONS,
    excessive_permissions,
    level_rank,
    missing_permissions,
)
from commitguard.github.storage import MergeGroupRecord, SqliteStateStore

SCOPE_JOBS = (
    "j.installation_id IN (SELECT value FROM json_each(?)) AND EXISTS (SELECT 1 FROM "
    "session_repositories sr WHERE sr.session_hash = ? AND sr.installation_id = j.installation_id "
    "AND sr.repository_id = j.repository_id)"
)
SCOPE_VIOLATIONS = (
    "v.installation_id IN (SELECT value FROM json_each(?)) AND EXISTS (SELECT 1 FROM "
    "session_repositories sr WHERE sr.session_hash = ? AND sr.installation_id = v.installation_id "
    "AND sr.repository_id = v.repository_id)"
)
SCOPE_REPOSITORIES = (
    "r.installation_id IN (SELECT value FROM json_each(?)) AND EXISTS (SELECT 1 FROM "
    "session_repositories sr WHERE sr.session_hash = ? AND sr.installation_id = r.installation_id "
    "AND sr.repository_id = r.repository_id)"
)
SCOPE_AUDIT = (
    "a.account_id IN (SELECT value FROM json_each(?)) AND (a.installation_id IS NULL OR "
    "a.installation_id IN (SELECT value FROM json_each(?))) AND (a.repository_id IS NULL OR "
    "EXISTS (SELECT 1 FROM session_repositories sr WHERE sr.session_hash = ? AND "
    "sr.installation_id = a.installation_id AND sr.repository_id = a.repository_id))"
)
SCOPE_INSTALLATIONS = "i.installation_id IN (SELECT value FROM json_each(?))"

_SCAN_COLUMNS = (
    "j.job_id, j.scan_id, j.installation_id, j.repository_id, j.owner, j.name, j.event, "
    "j.pull_request_number, j.ref, j.base_sha, j.head_sha, j.check_name, j.state, "
    "j.result_action, j.commits_scanned, j.violations, j.warnings, j.findings_count, "
    "j.created_at, j.started_at, j.completed_at, j.requested_by, j.sequence, j.group_key, "
    "j.conclusion, j.failure_kind, j.message, j.tool_version, j.rules_version, j.policy_version, "
    "j.policy_source, j.organization_policy_version, j.effective_policies, "
    "j.detector_failures, j.notices, j.scan_key, j.execution, j.trigger_kind, "
    "(SELECT account_id FROM installations i WHERE "
    "i.installation_id = j.installation_id) AS account_id"
)
_VIOLATION_COLUMNS = (
    "v.violation_id, v.installation_id, v.repository_id, v.rule_id, v.detector, v.title, "
    "v.severity, v.severity_rank, v.action, v.commit_sha, v.author, v.status, "
    "v.first_detected_at, v.last_detected_at, v.detections, v.acknowledged_at, "
    "v.acknowledged_by_login, v.acknowledgement_note, v.resolved_at, v.resolution, "
    "v.first_job_id, v.last_job_id, "
    "(SELECT owner || '/' || name FROM known_repositories k WHERE "
    "k.installation_id = v.installation_id AND k.repository_id = v.repository_id) AS full_name, "
    "(SELECT account_id FROM installations i WHERE i.installation_id = v.installation_id) "
    "AS account_id"
)

SCAN_RESULTS: Mapping[str, ScanResultStatus] = {s.value: s for s in ScanResultStatus}
_RESULT_CLAUSES: Mapping[ScanResultStatus, str] = {
    ScanResultStatus.QUEUED: "j.state = 'queued'",
    ScanResultStatus.RUNNING: "j.state = 'running'",
    ScanResultStatus.PASS: "j.state = 'passed' AND COALESCE(j.result_action, '') != 'warn'",
    ScanResultStatus.WARNING: "j.state = 'passed' AND j.result_action = 'warn'",
    ScanResultStatus.BLOCKED: "j.state = 'failed'",
    ScanResultStatus.ERROR: "j.state = 'error'",
    ScanResultStatus.CANCELLED: "j.state = 'cancelled' AND COALESCE(j.failure_kind, '') != 'stale'",
    ScanResultStatus.STALE: "j.state = 'cancelled' AND j.failure_kind = 'stale'",
}
SCAN_SORTS = ("newest", "oldest")
VIOLATION_SORTS = ("newest", "oldest", "severity", "repository")
REPOSITORY_SORTS = ("name", "risk", "recent")
AUDIT_SORTS = ("newest", "oldest")
PERIODS: Mapping[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def _dt(value: float | None) -> datetime | None:
    return None if value is None else datetime.fromtimestamp(value, UTC)


def _req_dt(value: float) -> datetime:
    return datetime.fromtimestamp(value, UTC)


class _Where:
    """Conditions built from constant fragments; values are always parameters."""

    def __init__(self, scope_clause: str, *scope_params: Any) -> None:
        self.clauses = [scope_clause]
        self.params: list[Any] = list(scope_params)

    def add(self, clause: str, *params: Any) -> None:
        self.clauses.append(clause)
        self.params.extend(params)

    @property
    def sql(self) -> str:
        return " AND ".join(f"({c})" for c in self.clauses)


@dataclass(frozen=True, slots=True)
class ScanFilters:
    organization_id: int | None = None
    repository_id: int | None = None
    result: ScanResultStatus | None = None
    event: Literal["pull_request", "push"] | None = None
    rule_id: str | None = None
    severity: Severity | None = None
    start: datetime | None = None
    end: datetime | None = None
    q: str | None = None
    sort: str = "newest"


@dataclass(frozen=True, slots=True)
class ViolationFilters:
    organization_id: int | None = None
    repository_id: int | None = None
    status: ViolationStatus | None = None
    severity: Severity | None = None
    rule_id: str | None = None
    action: Action | None = None
    start: datetime | None = None
    end: datetime | None = None
    q: str | None = None
    sort: str = "newest"


@dataclass(frozen=True, slots=True)
class RepositoryFilters:
    organization_id: int | None = None
    protection: ProtectionStatus | None = None
    q: str | None = None
    sort: str = "name"


@dataclass(frozen=True, slots=True)
class AuditFilters:
    organization_id: int | None = None
    repository_id: int | None = None
    event_type: AuditEventType | None = None
    actor: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    sort: str = "newest"


# --------------------------------------------------------------------------- #
# Row conversion
# --------------------------------------------------------------------------- #
def scan_summary(row: Row) -> ScanSummary:
    started = _dt(row["started_at"])
    completed = _dt(row["completed_at"])
    duration = (
        int((completed - started).total_seconds() * 1000)
        if started is not None and completed is not None and completed >= started
        else None
    )
    return ScanSummary(
        id=row["job_id"],
        scan_id=row["scan_id"],
        repository=RepositoryLink(
            id=row["repository_id"],
            installation_id=row["installation_id"],
            full_name=f"{row['owner']}/{row['name']}",
        ),
        organization_id=row["account_id"],
        event=row["event"],
        pull_request_number=row["pull_request_number"],
        ref=row["ref"],
        base_sha=row["base_sha"],
        head_sha=row["head_sha"],
        check_name=row["check_name"],
        result=ScanResultStatus(
            scan_result_label(row["state"], row["result_action"], row["failure_kind"])
        ),
        commits_scanned=row["commits_scanned"],
        violations=row["violations"],
        warnings=row["warnings"],
        findings=row["findings_count"],
        created_at=_req_dt(row["created_at"]),
        started_at=started,
        completed_at=completed,
        duration_ms=duration,
        requested_by=row["requested_by"],
        trigger=row["trigger_kind"] or row["event"],
        execution=row["execution"] or 1,
        failure_source=_failure_source(row["event"]),
    )


def _failure_source(event: str) -> Literal["pull_request", "push", "merge_queue"]:
    if event == "merge_group":
        return "merge_queue"
    return "push" if event == "push" else "pull_request"


def _duration_ms(started: datetime | None, completed: datetime | None) -> int | None:
    if started is None or completed is None or completed < started:
        return None
    return int((completed - started).total_seconds() * 1000)


def merge_group_view(record: MergeGroupRecord, job: Row | None) -> MergeGroupView:
    return MergeGroupView(
        head_sha=record.head_sha,
        base_sha=record.base_sha,
        base_ref=record.base_ref,
        pull_requests=record.pull_requests,
        state=record.state.value,
        destroyed_reason=record.destroyed_reason,
        result=ScanResultStatus(
            scan_result_label(job["state"], job["result_action"], job["failure_kind"])
        )
        if job is not None
        else None,
        scan=job["job_id"] if job is not None else None,
        created_at=record.created_at,
        updated_at=record.updated_at,
        validated_at=_dt(job["completed_at"]) if job is not None else None,
    )


def _evidence(document: str) -> tuple[EvidenceView, ...]:
    items = json.loads(document)
    return tuple(
        EvidenceView(
            source=item["source"],
            source_label=item["source_label"],
            value=item["value"],
            line_number=item.get("line_number"),
            matched=tuple(MatchView(**m) for m in item.get("matched", [])),
            notes=tuple(item.get("notes", [])),
        )
        for item in items
    )


def finding_view(row: Row) -> FindingView:
    return FindingView(
        id=row["finding_id"],
        violation_id=row["violation_id"],
        rule_id=row["rule_id"],
        detector=row["detector"],
        title=row["title"],
        message=row["message"],
        severity=Severity(row["severity"]),
        confidence=row["confidence"],
        action=Action(row["action"]),
        reason=row["reason"],
        commit_sha=row["commit_sha"],
        author=row["author"],
        committer=row["committer"],
        evidence=_evidence(row["evidence"]),
        remediation=row["remediation"],
    )


def _violation_status(row: Row) -> ViolationStatus:
    if row["status"] == "resolved":
        return ViolationStatus.RESOLVED
    if row["acknowledged_at"] is not None:
        return ViolationStatus.ACKNOWLEDGED
    return ViolationStatus.OPEN


def violation_summary(row: Row) -> ViolationSummary:
    return ViolationSummary(
        id=row["violation_id"],
        rule_id=row["rule_id"],
        title=row["title"],
        severity=Severity(row["severity"]),
        action=Action(row["action"]),
        repository=RepositoryLink(
            id=row["repository_id"],
            installation_id=row["installation_id"],
            full_name=row["full_name"] or f"repository {row['repository_id']}",
        ),
        organization_id=row["account_id"],
        commit_sha=row["commit_sha"],
        author=row["author"],
        status=_violation_status(row),
        first_detected_at=_req_dt(row["first_detected_at"]),
        last_detected_at=_req_dt(row["last_detected_at"]),
        detections=row["detections"],
    )


def _policies(document: str | None) -> tuple[PolicyEntry, ...]:
    return tuple(PolicyEntry(**entry) for entry in json.loads(document or "[]"))


def audit_summary(event: AuditEvent) -> str:
    """A one-line, server-side description of an audit event."""
    data = event.data
    t = AuditEventType

    def value(key: str, default: str = "") -> str:
        item = data.get(key)
        return default if item is None else str(item)

    texts: dict[AuditEventType, Callable[[], str]] = {
        t.INSTALLATION_CREATED: lambda: "GitHub App installed",
        t.INSTALLATION_REMOVED: lambda: "GitHub App uninstalled",
        t.INSTALLATION_SUSPENDED: lambda: "GitHub App installation suspended",
        t.INSTALLATION_UNSUSPENDED: lambda: "GitHub App installation unsuspended",
        t.INSTALLATION_PERMISSIONS_UPDATED: lambda: "GitHub App permissions updated",
        t.REPOSITORIES_ADDED: lambda: f"{value('repositories', '0')} repositories connected",
        t.REPOSITORIES_REMOVED: lambda: f"{value('repositories', '0')} repositories disconnected",
        t.REPOSITORIES_SYNCED: lambda: (
            f"Repositories synchronized ({value('added', '0')} added, "
            f"{value('removed', '0')} removed)"
        ),
        t.WEBHOOK_REJECTED: lambda: f"Webhook rejected: {value('reason')}",
        t.SCAN_QUEUED: lambda: "Scan queued",
        t.SCAN_REQUESTED: lambda: "Re-scan requested",
        t.REPOSITORY_SCANNED: lambda: f"Scanned {value('commits_scanned', '0')} commit(s)",
        t.SCAN_PASSED: lambda: "Scan completed: passed",
        t.SCAN_FAILED: lambda: f"Scan completed: blocked ({value('violations', '0')} violation(s))",
        t.POLICY_VIOLATION: lambda: f"Policy violation: {value('rules')}",
        t.POLICY_MODIFICATION: lambda: f"Security policy modification detected: {value('changes')}",
        t.CONFIGURATION_ERROR: lambda: f"Configuration error: {value('reason')}",
        t.SCAN_ERROR: lambda: f"Scan could not be completed: {value('reason')}",
        t.SCAN_CANCELLED: lambda: f"Scan cancelled: {value('reason')}",
        t.AUTHORIZATION_DENIED: lambda: f"Access denied: {value('reason')}",
        t.PULL_REQUEST_MERGED: lambda: f"Pull request #{value('pull_request')} merged",
        t.USER_SIGNED_IN: lambda: "Signed in",
        t.USER_SIGNED_OUT: lambda: "Signed out",
        t.SESSION_REVOKED: lambda: "Session revoked",
        t.MEMBER_ROLE_GRANTED: lambda: f"Granted {value('new_role')} to user {value('member')}",
        t.MEMBER_ROLE_CHANGED: lambda: (
            f"Changed role of user {value('member')}: {value('old_role')} → {value('new_role')}"
        ),
        t.MEMBER_REMOVED: lambda: f"Removed user {value('member')} ({value('old_role')})",
        t.ORGANIZATION_POLICY_CHANGED: lambda: (
            f"Changed organization policy v{value('old_version')} → v{value('new_version')}: "
            f"{value('changes')}"
        ),
        t.VIOLATION_OPENED: lambda: f"Violation detected: {value('rule')}",
        t.VIOLATION_REOPENED: lambda: f"Violation reopened: {value('rule')}",
        t.VIOLATION_RESOLVED: lambda: f"Violation resolved: {value('reason')}",
        t.VIOLATION_ACKNOWLEDGED: lambda: f"Violation acknowledged: {value('rule')}",
        t.VIOLATION_ACKNOWLEDGEMENT_REMOVED: lambda: f"Acknowledgement removed: {value('rule')}",
        t.REPOSITORY_MONITORING_DISABLED: lambda: f"Monitoring paused: {value('reason')}",
        t.REPOSITORY_MONITORING_ENABLED: lambda: "Monitoring resumed",
        t.ENFORCEMENT_STATUS_CHECKED: lambda: (
            f"Enforcement checked: Actions {value('actions')}, "
            f"required check {value('branch_protection')}"
        ),
    }
    return texts.get(event.type, lambda: event.type.value.replace("_", " ").capitalize())()


def audit_view(event: AuditEvent, full_names: Mapping[tuple[int, int], str]) -> AuditEventView:
    repository = None
    if event.repository_id is not None and event.installation_id is not None:
        name = full_names.get((event.installation_id, event.repository_id)) or event.repository
        repository = RepositoryLink(
            id=event.repository_id,
            installation_id=event.installation_id,
            full_name=name or f"repository {event.repository_id}",
        )
    scan = event.data.get("job") if isinstance(event.data.get("job"), str) else event.job_id
    return AuditEventView(
        id=event.event_id,
        type=event.type.value,
        occurred_at=event.occurred_at,
        actor=ActorView(type=event.actor_type.value, id=event.actor_id, login=event.actor_login),
        organization_id=event.account_id,
        installation_id=event.installation_id,
        repository=repository,
        head_sha=event.head_sha,
        action=event.action,
        summary=audit_summary(event),
        data=dict(event.data),
        scan=scan if isinstance(scan, str) else None,
    )


# --------------------------------------------------------------------------- #
# Protection
# --------------------------------------------------------------------------- #
def protection_for(
    *,
    app: AppConnection,
    monitoring_enabled: bool,
    latest_failure_kind: str | None,
    branch_protection: str | None,
    detail: str | None,
) -> tuple[ProtectionStatus, str]:
    """Explicit rules; a repository is never 'protected' only because the App is installed."""
    if app is AppConnection.SUSPENDED:
        return (
            ProtectionStatus.AT_RISK,
            "The GitHub App installation is suspended: CommitGuard checks no longer run.",
        )
    if app is AppConnection.DISCONNECTED:
        return (
            ProtectionStatus.AT_RISK,
            "The GitHub App no longer has access to this repository: CommitGuard checks no "
            "longer run.",
        )
    if not monitoring_enabled:
        return ProtectionStatus.UNPROTECTED, "CommitGuard monitoring is paused for this repository."
    if latest_failure_kind == "configuration":
        return (
            ProtectionStatus.CONFIGURATION_ERROR,
            "The latest scan failed because the CommitGuard configuration is invalid.",
        )
    if branch_protection == RequiredCheckStatus.REQUIRED.value:
        return ProtectionStatus.PROTECTED, detail or "A CommitGuard check is required."
    if branch_protection == RequiredCheckStatus.NOT_REQUIRED.value:
        return (
            ProtectionStatus.UNPROTECTED,
            (detail or "No CommitGuard check is required.")
            + " Failing checks do not block merges.",
        )
    return (
        ProtectionStatus.UNKNOWN,
        "Scanned by the GitHub App, but branch protection has not been verified.",
    )


@dataclass(frozen=True, slots=True)
class _RepoRow:
    row: Row
    summary: RepositorySummary


class DashboardQueries:
    def __init__(
        self,
        store: SqliteStateStore,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._now = now

    # ------------------------------------------------------------------ #
    # Scans
    # ------------------------------------------------------------------ #
    def list_scans(
        self, scope: AccessScope, filters: ScanFilters, *, cursor: str | None, limit: int
    ) -> Page[ScanSummary]:
        if not scope.installation_ids:
            return Page([], None, limit)
        where = _Where(SCOPE_JOBS, scope.installations_json, scope.session_hash)
        self._scan_filters(where, filters)
        position = decode_cursor(cursor, (int,))
        newest = filters.sort != "oldest"
        if position is not None:
            where.add("j.sequence < ?" if newest else "j.sequence > ?", position[0])
        order = "j.sequence DESC" if newest else "j.sequence ASC"
        rows = self._store.query(
            " ".join(
                (
                    "SELECT",
                    _SCAN_COLUMNS,
                    "FROM scan_jobs j WHERE",
                    where.sql,
                    "ORDER BY",
                    order,
                    "LIMIT ?",
                )
            ),
            (*where.params, limit + 1),
        )
        items = [scan_summary(r) for r in rows[:limit]]
        next_cursor = encode_cursor([rows[limit - 1]["sequence"]]) if len(rows) > limit else None
        return Page(items, next_cursor, limit)

    @staticmethod
    def _scan_filters(where: _Where, filters: ScanFilters) -> None:
        if filters.organization_id is not None:
            where.add(
                "j.installation_id IN (SELECT installation_id FROM installations "
                "WHERE account_id = ?)",
                filters.organization_id,
            )
        if filters.repository_id is not None:
            where.add("j.repository_id = ?", filters.repository_id)
        if filters.result is not None:
            where.add(_RESULT_CLAUSES[filters.result])
        if filters.event is not None:
            where.add("j.event = ?", filters.event)
        if filters.rule_id is not None:
            where.add(
                "EXISTS (SELECT 1 FROM findings f WHERE f.job_id = j.job_id AND f.rule_id = ?)",
                filters.rule_id,
            )
        if filters.severity is not None:
            where.add(
                "EXISTS (SELECT 1 FROM findings f WHERE f.job_id = j.job_id AND f.severity = ?)",
                filters.severity.value,
            )
        if filters.start is not None:
            where.add("j.created_at >= ?", filters.start.timestamp())
        if filters.end is not None:
            where.add("j.created_at < ?", filters.end.timestamp())
        if filters.q is not None:
            prefix = sha_prefix(filters.q)
            if prefix is not None:
                where.add(
                    "(j.head_sha LIKE ? OR (j.owner || '/' || j.name) LIKE ? ESCAPE '\\')",
                    prefix + "%",
                    like_pattern(filters.q),
                )
            else:
                where.add("(j.owner || '/' || j.name) LIKE ? ESCAPE '\\'", like_pattern(filters.q))

    def _scan_row(self, scope: AccessScope, scan_id: str) -> Row | None:
        if not scope.installation_ids or not _is_hex_id(scan_id):
            return None
        rows = self._store.query(
            " ".join(
                ("SELECT", _SCAN_COLUMNS, "FROM scan_jobs j WHERE j.job_id = ? AND", SCOPE_JOBS)
            ),
            (scan_id, scope.installations_json, scope.session_hash),
        )
        return rows[0] if rows else None

    def get_scan(
        self, scope: AccessScope, scan_id: str, *, principal: Principal
    ) -> ScanDetail | None:
        row = self._scan_row(scope, scan_id)
        if row is None:
            return None
        findings = self._store.query(
            "SELECT * FROM findings WHERE job_id = ? ORDER BY severity_rank DESC, finding_id",
            (scan_id,),
        )
        summary = scan_summary(row)
        can_rescan, blocked_reason = self.rescan_eligibility(row, principal)
        failure = None
        if row["state"] in ("error", "cancelled"):
            failure = ScanFailure(kind=row["failure_kind"], message=row["message"] or "")
        executions = self._store.query(
            "SELECT COUNT(*) AS n, (SELECT job_id FROM scan_jobs o WHERE o.installation_id = ? "
            "AND o.repository_id = ? AND o.scan_key = ? ORDER BY o.sequence DESC LIMIT 1) "
            "AS latest FROM scan_jobs WHERE installation_id = ? AND repository_id = ? "
            "AND scan_key = ?",
            (
                row["installation_id"],
                row["repository_id"],
                row["scan_key"],
                row["installation_id"],
                row["repository_id"],
                row["scan_key"],
            ),
        )[0]
        merge_group = None
        if row["event"] == "merge_group":
            record = self._store.get_merge_group(
                row["installation_id"], row["repository_id"], row["head_sha"]
            )
            if record is not None:
                merge_group = merge_group_view(record, row)
        return ScanDetail(
            executions=int(executions["n"] or 1),
            latest_execution=executions["latest"] or row["job_id"],
            merge_group=merge_group,
            scan=summary,
            conclusion=row["conclusion"],
            tool_version=row["tool_version"],
            rules_version=row["rules_version"],
            policy_version=row["policy_version"],
            policy_source=row["policy_source"],
            organization_policy_version=row["organization_policy_version"],
            effective_policies=_policies(row["effective_policies"]),
            detector_failures=row["detector_failures"],
            notices=tuple(json.loads(row["notices"] or "[]")),
            failure=failure,
            findings=tuple(finding_view(f) for f in findings),
            can_rescan=can_rescan,
            rescan_blocked_reason=blocked_reason,
        )

    def scan_executions(self, scope: AccessScope, scan_id: str) -> ExecutionHistory | None:
        """Every execution of the logical scan ``scan_id`` belongs to, newest first."""
        row = self._scan_row(scope, scan_id)
        if row is None:
            return None
        rows = self._store.query(
            " ".join(
                (
                    "SELECT",
                    _SCAN_COLUMNS,
                    "FROM scan_jobs j WHERE j.installation_id = ? AND j.repository_id = ? "
                    "AND j.scan_key = ? ORDER BY j.sequence DESC LIMIT 100",
                )
            ),
            (row["installation_id"], row["repository_id"], row["scan_key"]),
        )
        items = []
        for index, r in enumerate(rows):
            started, completed = _dt(r["started_at"]), _dt(r["completed_at"])
            failure = (
                ScanFailure(kind=r["failure_kind"], message=r["message"] or "")
                if r["state"] in ("error", "cancelled")
                else None
            )
            items.append(
                ExecutionView(
                    id=r["job_id"],
                    execution=r["execution"] or 1,
                    trigger=r["trigger_kind"] or r["event"],
                    current=index == 0,
                    result=ScanResultStatus(
                        scan_result_label(r["state"], r["result_action"], r["failure_kind"])
                    ),
                    head_sha=r["head_sha"],
                    base_sha=r["base_sha"],
                    organization_policy_version=r["organization_policy_version"],
                    policy_version=r["policy_version"],
                    rules_version=r["rules_version"],
                    tool_version=r["tool_version"],
                    conclusion=r["conclusion"],
                    requested_by=r["requested_by"],
                    failure=failure,
                    created_at=_req_dt(r["created_at"]),
                    started_at=started,
                    completed_at=completed,
                    duration_ms=_duration_ms(started, completed),
                )
            )
        evaluated = [i for i in items if i.policy_version is not None]
        return ExecutionHistory(
            scan_id=scan_id,
            items=tuple(items),
            policy_changed=len(
                {(i.policy_version, i.organization_policy_version) for i in evaluated}
            )
            > 1,
            rules_changed=len({i.rules_version for i in evaluated}) > 1,
        )

    def rescan_eligibility(self, row: Row, principal: Principal) -> tuple[bool, str | None]:
        account_id = row["account_id"]
        if account_id is None or not principal.can(Permission.SCANS_TRIGGER, int(account_id)):
            return False, "Your role cannot request scans."
        if row["state"] in ("queued", "running"):
            return False, "This scan has not finished yet."
        latest = self._store.latest_group_sequence(
            row["installation_id"], row["repository_id"], row["group_key"]
        )
        if latest > row["sequence"]:
            return False, "A newer scan exists for this pull request or branch."
        installation = self._store.get_installation(row["installation_id"])
        if installation is None or installation.state.value != "active":
            return False, "The GitHub App installation is not active."
        if not self._store.repository_listed(row["installation_id"], row["repository_id"]):
            return False, "The GitHub App no longer has access to this repository."
        if not self._store.monitoring_enabled(row["installation_id"], row["repository_id"]):
            return False, "Monitoring is paused for this repository."
        return True, None

    def scan_row_for_command(self, scope: AccessScope, scan_id: str) -> Row | None:
        return self._scan_row(scope, scan_id)

    def compare_scan(self, scope: AccessScope, scan_id: str) -> ScanComparison | None:
        row = self._scan_row(scope, scan_id)
        if row is None:
            return None
        previous = self._store.query(
            "SELECT job_id FROM scan_jobs WHERE installation_id = ? AND repository_id = ? "
            "AND group_key = ? AND sequence < ? AND state IN ('passed', 'failed') "
            "ORDER BY sequence DESC LIMIT 1",
            (row["installation_id"], row["repository_id"], row["group_key"], row["sequence"]),
        )
        current = self._findings_by_fingerprint(scan_id)
        before = self._findings_by_fingerprint(previous[0]["job_id"]) if previous else {}
        new = sorted(set(current) - set(before))
        resolved = sorted(set(before) - set(current))
        unchanged = sorted(set(current) & set(before))
        return ScanComparison(
            scan_id=scan_id,
            previous_scan_id=previous[0]["job_id"] if previous else None,
            new=tuple(new),
            resolved=tuple(resolved),
            unchanged=tuple(unchanged),
            new_findings=tuple(finding_view(current[f]) for f in new),
            resolved_findings=tuple(finding_view(before[f]) for f in resolved),
        )

    def _findings_by_fingerprint(self, job_id: str) -> dict[str, Row]:
        rows = self._store.query(
            "SELECT * FROM findings WHERE job_id = ? ORDER BY finding_id LIMIT 5000", (job_id,)
        )
        return {r["fingerprint"]: r for r in rows}

    # ------------------------------------------------------------------ #
    # Violations
    # ------------------------------------------------------------------ #
    def list_violations(
        self, scope: AccessScope, filters: ViolationFilters, *, cursor: str | None, limit: int
    ) -> Page[ViolationSummary]:
        if not scope.installation_ids:
            return Page([], None, limit)
        where = _Where(SCOPE_VIOLATIONS, scope.installations_json, scope.session_hash)
        if filters.organization_id is not None:
            where.add(
                "v.installation_id IN (SELECT installation_id FROM installations "
                "WHERE account_id = ?)",
                filters.organization_id,
            )
        if filters.repository_id is not None:
            where.add("v.repository_id = ?", filters.repository_id)
        if filters.status is ViolationStatus.RESOLVED:
            where.add("v.status = 'resolved'")
        elif filters.status is ViolationStatus.ACKNOWLEDGED:
            where.add("v.status = 'open' AND v.acknowledged_at IS NOT NULL")
        elif filters.status is ViolationStatus.OPEN:
            where.add("v.status = 'open' AND v.acknowledged_at IS NULL")
        if filters.severity is not None:
            where.add("v.severity = ?", filters.severity.value)
        if filters.rule_id is not None:
            where.add("v.rule_id = ?", filters.rule_id)
        if filters.action is not None:
            where.add("v.action = ?", filters.action.value)
        if filters.start is not None:
            where.add("v.last_detected_at >= ?", filters.start.timestamp())
        if filters.end is not None:
            where.add("v.last_detected_at < ?", filters.end.timestamp())
        if filters.q is not None:
            prefix = sha_prefix(filters.q)
            pattern = like_pattern(filters.q)
            where.add(
                "(v.rule_id LIKE ? ESCAPE '\\' OR v.author LIKE ? ESCAPE '\\' OR "
                "COALESCE(v.commit_sha, '') LIKE ? OR EXISTS (SELECT 1 FROM known_repositories k "
                "WHERE k.installation_id = v.installation_id AND k.repository_id = v.repository_id "
                "AND (k.owner || '/' || k.name) LIKE ? ESCAPE '\\'))",
                pattern,
                pattern,
                (prefix + "%") if prefix else "\x00",
                pattern,
            )
        sort = filters.sort if filters.sort in VIOLATION_SORTS else "newest"
        if sort == "severity":
            position = decode_cursor(cursor, (int, float, str))
            if position is not None:
                where.add(
                    "(v.severity_rank < ? OR (v.severity_rank = ? AND (v.last_detected_at < ? OR "
                    "(v.last_detected_at = ? AND v.violation_id < ?))))",
                    position[0],
                    position[0],
                    position[1],
                    position[1],
                    position[2],
                )
            order = "v.severity_rank DESC, v.last_detected_at DESC, v.violation_id DESC"
        elif sort == "repository":
            position = decode_cursor(cursor, (str, float, str))
            name_expr = (
                "COALESCE((SELECT owner || '/' || name FROM known_repositories k WHERE "
                "k.installation_id = v.installation_id AND k.repository_id = v.repository_id), '')"
            )
            if position is not None:
                where.add(
                    " ".join(
                        (
                            "(",
                            name_expr,
                            "> ? OR (",
                            name_expr,
                            "= ? AND (v.last_detected_at < ? "
                            "OR (v.last_detected_at = ? AND v.violation_id < ?))))",
                        )
                    ),
                    position[0],
                    position[0],
                    position[1],
                    position[1],
                    position[2],
                )
            order = " ".join((name_expr, "ASC, v.last_detected_at DESC, v.violation_id DESC"))
        else:
            newest = sort == "newest"
            position = decode_cursor(cursor, (float, str))
            if position is not None:
                where.add(
                    "(v.last_detected_at < ? OR (v.last_detected_at = ? AND v.violation_id < ?))"
                    if newest
                    else (
                        "(v.last_detected_at > ? OR (v.last_detected_at = ? "
                        "AND v.violation_id > ?))"
                    ),
                    position[0],
                    position[0],
                    position[1],
                )
            order = (
                "v.last_detected_at DESC, v.violation_id DESC"
                if newest
                else "v.last_detected_at ASC, v.violation_id ASC"
            )
        rows = self._store.query(
            " ".join(
                (
                    "SELECT",
                    _VIOLATION_COLUMNS,
                    "FROM violations v WHERE",
                    where.sql,
                    "ORDER BY",
                    order,
                    "LIMIT ?",
                )
            ),
            (*where.params, limit + 1),
        )
        items = [violation_summary(r) for r in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            if sort == "severity":
                next_cursor = encode_cursor(
                    [last["severity_rank"], last["last_detected_at"], last["violation_id"]]
                )
            elif sort == "repository":
                next_cursor = encode_cursor(
                    [last["full_name"] or "", last["last_detected_at"], last["violation_id"]]
                )
            else:
                next_cursor = encode_cursor([last["last_detected_at"], last["violation_id"]])
        return Page(items, next_cursor, limit)

    def violation_row(self, scope: AccessScope, violation_id: str) -> Row | None:
        if not scope.installation_ids or not _is_hex_id(violation_id):
            return None
        rows = self._store.query(
            " ".join(
                (
                    "SELECT",
                    _VIOLATION_COLUMNS,
                    "FROM violations v WHERE v.violation_id = ? AND",
                    SCOPE_VIOLATIONS,
                )
            ),
            (violation_id, scope.installations_json, scope.session_hash),
        )
        return rows[0] if rows else None

    def get_violation(
        self, scope: AccessScope, violation_id: str, *, principal: Principal
    ) -> ViolationDetail | None:
        row = self.violation_row(scope, violation_id)
        if row is None:
            return None
        latest = self._store.query(
            "SELECT * FROM findings WHERE violation_id = ? ORDER BY finding_id DESC LIMIT 1",
            (violation_id,),
        )
        exposures = self._store.query(
            "SELECT kind, label, active, opened_at, closed_at, closed_reason FROM "
            "violation_exposures WHERE violation_id = ? ORDER BY active DESC, opened_at DESC "
            "LIMIT 100",
            (violation_id,),
        )
        detections = self._store.query(
            "SELECT f.job_id, f.action, f.created_at, j.state, j.result_action, j.head_sha "
            "FROM findings f JOIN scan_jobs j ON j.job_id = f.job_id WHERE f.violation_id = ? "
            "ORDER BY f.finding_id DESC LIMIT 50",
            (violation_id,),
        )
        finding = latest[0] if latest else None
        acknowledgement = None
        if row["acknowledged_at"] is not None:
            acknowledgement = AcknowledgementView(
                by=row["acknowledged_by_login"],
                at=_req_dt(row["acknowledged_at"]),
                note=row["acknowledgement_note"],
            )
        account_id = row["account_id"]
        return ViolationDetail(
            violation=violation_summary(row),
            detector=row["detector"],
            message=finding["message"] if finding else row["title"],
            committer=finding["committer"] if finding else None,
            policy_reason=finding["reason"] if finding else "",
            evidence=_evidence(finding["evidence"]) if finding else (),
            remediation=finding["remediation"] if finding else "",
            recommended_steps=remediation_steps(row["rule_id"]),
            resolved_at=_dt(row["resolved_at"]),
            resolution=row["resolution"],
            acknowledgement=acknowledgement,
            exposures=tuple(
                ExposureView(
                    kind=e["kind"],
                    label=e["label"],
                    active=bool(e["active"]),
                    opened_at=_req_dt(e["opened_at"]),
                    closed_at=_dt(e["closed_at"]),
                    closed_reason=e["closed_reason"],
                )
                for e in exposures
            ),
            detections=tuple(
                DetectionView(
                    scan=d["job_id"],
                    result=ScanResultStatus(scan_result_label(d["state"], d["result_action"])),
                    detected_at=_req_dt(d["created_at"]),
                    action=Action(d["action"]),
                    head_sha=d["head_sha"],
                )
                for d in detections
            ),
            first_scan_id=row["first_job_id"],
            last_scan_id=row["last_job_id"],
            can_manage=account_id is not None
            and principal.can(Permission.VIOLATIONS_MANAGE, int(account_id))
            and row["status"] == "open",
        )

    # ------------------------------------------------------------------ #
    # Repositories
    # ------------------------------------------------------------------ #
    def _repository_rows(
        self,
        scope: AccessScope,
        *,
        organization_id: int | None = None,
        repository_id: int | None = None,
        q: str | None = None,
    ) -> list[_RepoRow]:
        if not scope.installation_ids:
            return []
        where = _Where(SCOPE_REPOSITORIES, scope.installations_json, scope.session_hash)
        if organization_id is not None:
            where.add("i.account_id = ?", organization_id)
        if repository_id is not None:
            where.add("r.repository_id = ?", repository_id)
        if q is not None:
            where.add("(r.owner || '/' || r.name) LIKE ? ESCAPE '\\'", like_pattern(q))
        sql = " ".join(
            (
                "SELECT r.installation_id, r.repository_id, r.owner, r.name, r.default_branch, "
                "r.removed_at, i.account_id, i.account_login, i.account_type, i.state AS "
                "installation_state, EXISTS (SELECT 1 FROM installation_repositories ir WHERE "
                "ir.installation_id = r.installation_id AND ir.repository_id = r.repository_id) "
                "AS listed, COALESCE((SELECT monitoring_enabled FROM repository_settings s WHERE "
                "s.installation_id = r.installation_id AND s.repository_id = r.repository_id), 1) "
                "AS monitoring_enabled, (SELECT MAX(sequence) FROM scan_jobs j WHERE "
                "j.installation_id = r.installation_id AND j.repository_id = r.repository_id) "
                "AS latest_sequence, (SELECT failure_kind FROM scan_jobs j WHERE "
                "j.installation_id = r.installation_id AND j.repository_id = r.repository_id AND "
                "j.state IN ('passed', 'failed', 'error') ORDER BY sequence DESC LIMIT 1) AS "
                "latest_failure_kind, (SELECT COUNT(*) FROM violations v WHERE "
                "v.installation_id = r.installation_id AND v.repository_id = r.repository_id AND "
                "v.status = 'open' AND v.action = 'block') AS open_violations, (SELECT COUNT(*) "
                "FROM violations v WHERE v.installation_id = r.installation_id AND "
                "v.repository_id = r.repository_id AND v.status = 'open' AND v.action = 'warn') "
                "AS open_warnings, (SELECT COUNT(*) FROM violations v WHERE "
                "v.installation_id = r.installation_id AND v.repository_id = r.repository_id AND "
                "v.status = 'open' AND v.severity = 'critical') AS critical_open, "
                "e.branch_protection, e.branch_protection_detail, e.required_checks, e.branch, "
                "e.actions, e.actions_detail, e.checked_at, e.merge_queue, e.merge_queue_detail "
                "FROM known_repositories r JOIN installations i ON i.installation_id = "
                "r.installation_id LEFT JOIN enforcement_status e ON e.installation_id = "
                "r.installation_id AND e.repository_id = r.repository_id WHERE",
                where.sql,
                "LIMIT 20000",
            )
        )
        rows = self._store.query(sql, where.params)
        sequences = [r["latest_sequence"] for r in rows if r["latest_sequence"] is not None]
        latest: dict[int, Row] = {}
        for chunk_start in range(0, len(sequences), 500):
            chunk = sequences[chunk_start : chunk_start + 500]
            for scan in self._store.query(
                " ".join(
                    (
                        "SELECT",
                        _SCAN_COLUMNS,
                        "FROM scan_jobs j WHERE j.sequence IN (SELECT value FROM json_each(?))",
                    )
                ),
                (json.dumps(chunk),),
            ):
                latest[int(scan["sequence"])] = scan
        results = []
        for row in rows:
            scan_row = latest.get(row["latest_sequence"]) if row["latest_sequence"] else None
            results.append(_RepoRow(row, self._repository_summary(row, scan_row)))
        return results

    @staticmethod
    def _app_connection(row: Row) -> AppConnection:
        if row["installation_state"] == "suspended":
            return AppConnection.SUSPENDED
        if row["installation_state"] != "active" or not row["listed"]:
            return AppConnection.DISCONNECTED
        return AppConnection.CONNECTED

    def _repository_summary(self, row: Row, scan_row: Row | None) -> RepositorySummary:
        app = self._app_connection(row)
        monitoring = bool(row["monitoring_enabled"])
        protection, reason = protection_for(
            app=app,
            monitoring_enabled=monitoring,
            latest_failure_kind=row["latest_failure_kind"],
            branch_protection=row["branch_protection"],
            detail=row["branch_protection_detail"],
        )
        owner, name = row["owner"], row["name"]
        return RepositorySummary(
            id=row["repository_id"],
            installation_id=row["installation_id"],
            organization=OrganizationRef(
                id=row["account_id"], login=row["account_login"], type=row["account_type"]
            ),
            owner=owner,
            name=name,
            full_name=f"{owner}/{name}",
            # owner and name are validated GitHub identifiers ([A-Za-z0-9._-]).
            github_url=f"https://github.com/{owner}/{name}",
            default_branch=row["default_branch"],
            protection=protection,
            protection_reason=reason,
            app_connection=app,
            monitoring_enabled=monitoring,
            last_scan=scan_summary(scan_row) if scan_row is not None else None,
            open_violations=row["open_violations"],
            open_warnings=row["open_warnings"],
            critical_open=row["critical_open"],
        )

    def list_repositories(
        self, scope: AccessScope, filters: RepositoryFilters, *, offset: int, limit: int
    ) -> Page[RepositorySummary]:
        rows = self._repository_rows(scope, organization_id=filters.organization_id, q=filters.q)
        summaries = [r.summary for r in rows]
        if filters.protection is not None:
            summaries = [s for s in summaries if s.protection is filters.protection]
        summaries = sort_repositories(summaries, filters.sort)
        page = summaries[offset : offset + limit]
        next_cursor = encode_cursor([offset + limit]) if len(summaries) > offset + limit else None
        return Page(page, next_cursor, limit)

    def repository_ref(self, scope: AccessScope, repository_id: int) -> Row | None:
        """The (installation, repository) row a caller can see, preferring a connected one."""
        rows = self._repository_rows(scope, repository_id=repository_id)
        if not rows:
            return None
        rows.sort(
            key=lambda r: (
                r.summary.app_connection is AppConnection.CONNECTED,
                r.row["latest_sequence"] or 0,
            ),
            reverse=True,
        )
        return rows[0].row

    def get_repository(
        self, scope: AccessScope, repository_id: int, *, principal: Principal
    ) -> RepositoryDetail | None:
        rows = self._repository_rows(scope, repository_id=repository_id)
        if not rows:
            return None
        rows.sort(
            key=lambda r: (
                r.summary.app_connection is AppConnection.CONNECTED,
                r.row["latest_sequence"] or 0,
            ),
            reverse=True,
        )
        chosen = rows[0]
        row, summary = chosen.row, chosen.summary
        installation_id = row["installation_id"]
        account_id = int(row["account_id"])
        recent = self._store.query(
            " ".join(
                (
                    "SELECT",
                    _SCAN_COLUMNS,
                    "FROM scan_jobs j WHERE j.installation_id = ? AND "
                    "j.repository_id = ? ORDER BY j.sequence DESC LIMIT 10",
                )
            ),
            (installation_id, repository_id),
        )
        completed = self._store.query(
            "SELECT job_id, effective_policies, organization_policy_version, state, "
            "result_action, head_sha, completed_at FROM scan_jobs WHERE installation_id = ? AND "
            "repository_id = ? AND state IN ('passed', 'failed') ORDER BY sequence DESC LIMIT 1",
            (installation_id, repository_id),
        )
        latest_completed = completed[0] if completed else None
        violations = self._store.query(
            " ".join(
                (
                    "SELECT",
                    _VIOLATION_COLUMNS,
                    "FROM violations v WHERE v.installation_id = ? AND "
                    "v.repository_id = ? AND v.status = 'open' ORDER BY v.severity_rank DESC, "
                    "v.last_detected_at DESC LIMIT 10",
                )
            ),
            (installation_id, repository_id),
        )
        can_audit = principal.can(Permission.AUDIT_READ, account_id)
        audit: tuple[AuditEventView, ...] = ()
        if can_audit:
            events = self._store.query(
                "SELECT document FROM audit_events WHERE installation_id = ? AND repository_id = ? "
                "ORDER BY occurred_at DESC LIMIT 10",
                (installation_id, repository_id),
            )
            names = {(installation_id, repository_id): summary.full_name}
            audit = tuple(
                audit_view(AuditEvent.model_validate_json(e["document"]), names) for e in events
            )
        checked_at = _dt(row["checked_at"])
        enforcement = EnforcementView(
            github_app=EnforcementSignal(
                status=summary.app_connection.value,
                detail={
                    AppConnection.CONNECTED: "The GitHub App can access this repository.",
                    AppConnection.SUSPENDED: "The GitHub App installation is suspended.",
                    AppConnection.DISCONNECTED: "The GitHub App no longer has access.",
                }[summary.app_connection],
            ),
            github_actions=EnforcementSignal(
                status=row["actions"] or "unknown",
                detail=row["actions_detail"] or "Not checked yet.",
                checked_at=checked_at,
            ),
            required_check=RequiredCheckSignal(
                status=RequiredCheckStatus(row["branch_protection"] or "unknown"),
                branch=row["branch"],
                required_checks=tuple(json.loads(row["required_checks"] or "[]")),
                detail=row["branch_protection_detail"] or "Not checked yet.",
                checked_at=checked_at,
            ),
            latest_check=LatestCheckSignal(
                result=ScanResultStatus(
                    scan_result_label(latest_completed["state"], latest_completed["result_action"])
                )
                if latest_completed
                else None,
                scan=latest_completed["job_id"] if latest_completed else None,
                head_sha=latest_completed["head_sha"] if latest_completed else None,
                completed_at=_dt(latest_completed["completed_at"]) if latest_completed else None,
            ),
            local_hooks=EnforcementSignal(
                status="not_verifiable",
                detail="A server cannot see whether developers installed the Git hooks.",
            ),
            merge_queue=EnforcementSignal(
                status=row["merge_queue"] or MergeQueueStatus.UNKNOWN.value,
                detail=row["merge_queue_detail"] or "Not checked yet.",
                checked_at=checked_at,
            ),
            monitoring_enabled=summary.monitoring_enabled,
        )
        return RepositoryDetail(
            repository=summary,
            enforcement=enforcement,
            organization_policy_version=latest_completed["organization_policy_version"]
            if latest_completed
            else None,
            effective_policies=_policies(latest_completed["effective_policies"])
            if latest_completed
            else (),
            effective_policy_scan=latest_completed["job_id"] if latest_completed else None,
            recent_scans=tuple(scan_summary(r) for r in recent),
            open_violations=tuple(violation_summary(v) for v in violations),
            audit=audit,
            permissions=RepositoryPermissions(
                manage=principal.can(Permission.REPOSITORIES_MANAGE, account_id),
                trigger_scans=principal.can(Permission.SCANS_TRIGGER, account_id),
                read_audit=can_audit,
            ),
        )

    def merge_queue(self, scope: AccessScope, repository_id: int) -> MergeQueueView | None:
        rows = self._repository_rows(scope, repository_id=repository_id)
        if not rows:
            return None
        rows.sort(
            key=lambda r: (
                r.summary.app_connection is AppConnection.CONNECTED,
                r.row["latest_sequence"] or 0,
            ),
            reverse=True,
        )
        row = rows[0].row
        installation_id = int(row["installation_id"])
        records = self._store.list_merge_groups(installation_id, repository_id, limit=10)
        jobs: dict[str, Row] = {}
        ids = [r.job_id for r in records if r.job_id]
        if ids:
            for job in self._store.query(
                " ".join(
                    (
                        "SELECT",
                        _SCAN_COLUMNS,
                        "FROM scan_jobs j WHERE j.job_id IN (SELECT value FROM json_each(?))",
                    )
                ),
                (json.dumps(ids),),
            ):
                jobs[job["job_id"]] = job
        views = [merge_group_view(r, jobs.get(r.job_id or "")) for r in records]
        current = next((v for v in views if v.state == "checks_requested"), None)
        installation = self._store.get_installation(installation_id)
        permission: Literal["granted", "missing"] = (
            "granted"
            if installation is not None
            and level_rank(installation.permissions.get("merge_queues")) >= level_rank("read")
            else "missing"
        )
        status = MergeQueueStatus(row["merge_queue"] or MergeQueueStatus.UNKNOWN.value)
        return MergeQueueView(
            repository_id=repository_id,
            status=status,
            detail=row["merge_queue_detail"]
            or "Merge queue settings have not been checked. Refresh the enforcement status.",
            checked_at=_dt(row["checked_at"]),
            permission=permission,
            current=current,
            recent=tuple(views),
        )

    # ------------------------------------------------------------------ #
    # Audit
    # ------------------------------------------------------------------ #
    def list_audit(
        self, scope: AccessScope, filters: AuditFilters, *, cursor: str | None, limit: int
    ) -> Page[AuditEventView]:
        if not scope.account_ids:
            return Page([], None, limit)
        where = _Where(
            SCOPE_AUDIT, scope.accounts_json, scope.installations_json, scope.session_hash
        )
        if filters.organization_id is not None:
            where.add("a.account_id = ?", filters.organization_id)
        if filters.repository_id is not None:
            where.add("a.repository_id = ?", filters.repository_id)
        if filters.event_type is not None:
            where.add("a.type = ?", filters.event_type.value)
        if filters.actor is not None:
            where.add("a.actor_login LIKE ? ESCAPE '\\'", like_pattern(filters.actor))
        if filters.start is not None:
            where.add("a.occurred_at >= ?", filters.start.timestamp())
        if filters.end is not None:
            where.add("a.occurred_at < ?", filters.end.timestamp())
        newest = filters.sort != "oldest"
        # Ties on the timestamp are broken by insertion order (rowid), so events
        # recorded in one transaction keep their order.
        position = decode_cursor(cursor, (float, int))
        if position is not None:
            where.add(
                "(a.occurred_at < ? OR (a.occurred_at = ? AND a.rowid < ?))"
                if newest
                else "(a.occurred_at > ? OR (a.occurred_at = ? AND a.rowid > ?))",
                position[0],
                position[0],
                position[1],
            )
        order = "a.occurred_at DESC, a.rowid DESC" if newest else "a.occurred_at ASC, a.rowid ASC"
        rows = self._store.query(
            " ".join(
                (
                    "SELECT a.rowid AS position, a.occurred_at, a.document FROM audit_events a "
                    "WHERE",
                    where.sql,
                    "ORDER BY",
                    order,
                    "LIMIT ?",
                )
            ),
            (*where.params, limit + 1),
        )
        events = [AuditEvent.model_validate_json(r["document"]) for r in rows[:limit]]
        names = self._full_names(events)
        items = [audit_view(e, names) for e in events]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = encode_cursor([last["occurred_at"], last["position"]])
        return Page(items, next_cursor, limit)

    def get_audit_event(self, scope: AccessScope, event_id: str) -> AuditEventView | None:
        if not scope.account_ids or not _is_hex_id(event_id):
            return None
        rows = self._store.query(
            " ".join(
                ("SELECT a.document FROM audit_events a WHERE a.event_id = ? AND", SCOPE_AUDIT)
            ),
            (event_id, scope.accounts_json, scope.installations_json, scope.session_hash),
        )
        if not rows:
            return None
        event = AuditEvent.model_validate_json(rows[0]["document"])
        return audit_view(event, self._full_names([event]))

    def _full_names(self, events: Sequence[AuditEvent]) -> dict[tuple[int, int], str]:
        keys = sorted(
            {
                (e.installation_id, e.repository_id)
                for e in events
                if e.installation_id is not None and e.repository_id is not None
            }
        )
        names: dict[tuple[int, int], str] = {}
        for installation_id, repository_id in keys:
            rows = self._store.query(
                "SELECT owner, name FROM known_repositories WHERE installation_id = ? "
                "AND repository_id = ?",
                (installation_id, repository_id),
            )
            if rows:
                names[(installation_id, repository_id)] = f"{rows[0]['owner']}/{rows[0]['name']}"
        return names

    # ------------------------------------------------------------------ #
    # GitHub installations
    # ------------------------------------------------------------------ #
    def _installation_rows(
        self, scope: AccessScope, installation_id: int | None = None
    ) -> list[Row]:
        if not scope.installation_ids:
            return []
        where = _Where(SCOPE_INSTALLATIONS, scope.installations_json)
        if installation_id is not None:
            where.add("i.installation_id = ?", installation_id)
        return self._store.query(
            " ".join(
                (
                    "SELECT i.*, (SELECT COUNT(*) FROM installation_repositories ir WHERE "
                    "ir.installation_id = i.installation_id) AS repository_count, (SELECT "
                    "MAX(occurred_at) FROM audit_events a WHERE a.installation_id = "
                    "i.installation_id) AS last_event_at FROM installations i WHERE",
                    where.sql,
                    "ORDER BY i.account_login COLLATE NOCASE, i.installation_id",
                )
            ),
            where.params,
        )

    @staticmethod
    def _installation_view(row: Row, principal: Principal) -> InstallationView:
        permissions = json.loads(row["permissions"] or "{}")
        state = row["state"]
        status = {
            "active": AppConnection.CONNECTED,
            "suspended": AppConnection.SUSPENDED,
        }.get(state, AppConnection.DISCONNECTED)
        login = row["account_login"]
        if row["account_type"] == "Organization":
            url = f"https://github.com/organizations/{login}/settings/installations/{row['installation_id']}"
        else:
            url = f"https://github.com/settings/installations/{row['installation_id']}"
        return InstallationView(
            id=row["installation_id"],
            account=OrganizationRef(id=row["account_id"], login=login, type=row["account_type"]),
            status=status,
            repository_selection=row["repository_selection"],
            repositories=row["repository_count"],
            permissions=permissions,
            missing_permissions=tuple(
                f"{k}: {v}"
                for k, v in missing_permissions(permissions, REQUIRED_PERMISSIONS).items()
            ),
            excessive_permissions=tuple(
                f"{k}: {v}"
                for k, v in excessive_permissions(permissions, REQUIRED_PERMISSIONS).items()
            ),
            installed_at=_req_dt(row["created_at"]),
            updated_at=_req_dt(row["updated_at"]),
            last_event_at=_dt(row["last_event_at"]),
            github_settings_url=url,
            can_manage=principal.can(Permission.GITHUB_MANAGE, int(row["account_id"])),
        )

    def list_installations(
        self, scope: AccessScope, principal: Principal
    ) -> list[InstallationView]:
        return [self._installation_view(r, principal) for r in self._installation_rows(scope)]

    def get_installation(
        self, scope: AccessScope, installation_id: int, principal: Principal
    ) -> InstallationDetail | None:
        rows = self._installation_rows(scope, installation_id)
        if not rows:
            return None
        view = self._installation_view(rows[0], principal)
        events: tuple[AuditEventView, ...] = ()
        if principal.can(Permission.AUDIT_READ, view.account.id):
            audit_scope = principal.scope(Permission.AUDIT_READ, account_id=view.account.id)
            page = self.list_audit(
                audit_scope,
                AuditFilters(organization_id=view.account.id),
                cursor=None,
                limit=10,
            )
            events = tuple(e for e in page.items if e.installation_id in (installation_id, None))
        return InstallationDetail(installation=view, recent_events=events)

    def installation_repositories(
        self, scope: AccessScope, installation_id: int, *, offset: int, limit: int
    ) -> Page[InstallationRepositoryView] | None:
        if not self._installation_rows(scope, installation_id):
            return None
        rows = self._store.query(
            "SELECT r.repository_id, r.owner, r.name, r.first_seen_at, r.removed_at, EXISTS "
            "(SELECT 1 FROM installation_repositories ir WHERE ir.installation_id = "
            "r.installation_id AND ir.repository_id = r.repository_id) AS listed, "
            "COALESCE((SELECT monitoring_enabled FROM repository_settings s WHERE "
            "s.installation_id = r.installation_id AND s.repository_id = r.repository_id), 1) AS "
            "monitoring_enabled FROM known_repositories r WHERE r.installation_id = ? AND EXISTS "
            "(SELECT 1 FROM session_repositories sr WHERE sr.session_hash = ? AND "
            "sr.installation_id = r.installation_id AND sr.repository_id = r.repository_id) "
            "ORDER BY listed DESC, r.owner COLLATE NOCASE, r.name COLLATE NOCASE LIMIT ? OFFSET ?",
            (installation_id, scope.session_hash, limit + 1, offset),
        )
        items = [
            InstallationRepositoryView(
                id=r["repository_id"],
                full_name=f"{r['owner']}/{r['name']}",
                connected=bool(r["listed"]),
                monitoring_enabled=bool(r["monitoring_enabled"]),
                added_at=_dt(r["first_seen_at"]),
                removed_at=_dt(r["removed_at"]),
            )
            for r in rows[:limit]
        ]
        next_cursor = encode_cursor([offset + limit]) if len(rows) > limit else None
        return Page(items, next_cursor, limit)

    # ------------------------------------------------------------------ #
    # Overview
    # ------------------------------------------------------------------ #
    def overview(
        self,
        principal: Principal,
        *,
        period: str,
        organization_id: int | None,
    ) -> OverviewView:
        now = self._now()
        start = now - PERIODS[period]
        repo_scope = principal.scope(Permission.REPOSITORIES_READ, account_id=organization_id)
        scan_scope = principal.scope(Permission.SCANS_READ, account_id=organization_id)
        violation_scope = principal.scope(Permission.VIOLATIONS_READ, account_id=organization_id)

        repositories = [r.summary for r in self._repository_rows(repo_scope)]
        monitored = [r for r in repositories if r.app_connection is AppConnection.CONNECTED]
        by_protection = {
            status: sum(1 for r in monitored if r.protection is status)
            for status in ProtectionStatus
        }
        scan_counts = {"total": 0, "passed": 0, "blocked": 0, "error": 0}
        if scan_scope.installation_ids:
            row = self._store.query(
                " ".join(
                    (
                        "SELECT COUNT(*) AS total, SUM(j.state = 'passed') AS passed, "
                        "SUM(j.state = 'failed') AS blocked, SUM(j.state = 'error') AS error "
                        "FROM scan_jobs j WHERE",
                        SCOPE_JOBS,
                        "AND j.created_at >= ?",
                    )
                ),
                (scan_scope.installations_json, scan_scope.session_hash, start.timestamp()),
            )[0]
            scan_counts = {k: int(row[k] or 0) for k in scan_counts}
        violation_counts = {"block": 0, "warn": 0, "critical": 0, "high": 0}
        if violation_scope.installation_ids:
            row = self._store.query(
                " ".join(
                    (
                        "SELECT SUM(v.action = 'block') AS block, SUM(v.action = 'warn') AS warn, "
                        "SUM(v.severity = 'critical') AS critical, "
                        "SUM(v.severity = 'high') AS high "
                        "FROM violations v WHERE",
                        SCOPE_VIOLATIONS,
                        "AND v.status = 'open'",
                    )
                ),
                (violation_scope.installations_json, violation_scope.session_hash),
            )[0]
            violation_counts = {k: int(row[k] or 0) for k in violation_counts}

        installations = self.list_installations(repo_scope, principal)
        integration = _integration(installations)
        recent_scans = self.list_scans(scan_scope, ScanFilters(), cursor=None, limit=8).items
        recent_violations = self.list_violations(
            violation_scope, ViolationFilters(), cursor=None, limit=8
        ).items
        health_list = sort_repositories(repositories, "risk")[:8]
        summary = OverviewSummary(
            repositories_monitored=len(monitored),
            repositories_protected=by_protection[ProtectionStatus.PROTECTED],
            # Not "monitored": the App lost access, which is exactly what puts them at risk.
            repositories_at_risk=sum(
                1 for r in repositories if r.protection is ProtectionStatus.AT_RISK
            ),
            repositories_unprotected=by_protection[ProtectionStatus.UNPROTECTED],
            repositories_unknown=by_protection[ProtectionStatus.UNKNOWN],
            repositories_configuration_error=by_protection[ProtectionStatus.CONFIGURATION_ERROR],
            scans=scan_counts["total"],
            scans_passed=scan_counts["passed"],
            scans_blocked=scan_counts["blocked"],
            scans_error=scan_counts["error"],
            open_violations=violation_counts["block"],
            open_warnings=violation_counts["warn"],
            critical_open=violation_counts["critical"],
            high_open=violation_counts["high"],
        )
        return OverviewView(
            period=OverviewPeriod(key=period, start=start, end=now),  # type: ignore[arg-type]
            summary=summary,
            integration=integration,
            health=_health_checks(summary, integration, len(monitored)),
            recent_scans=tuple(recent_scans),
            recent_violations=tuple(recent_violations),
            repository_health=tuple(health_list),
        )


def _is_hex_id(value: str) -> bool:
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)


_RISK_ORDER = {
    ProtectionStatus.AT_RISK: 0,
    ProtectionStatus.CONFIGURATION_ERROR: 1,
    ProtectionStatus.UNPROTECTED: 2,
    ProtectionStatus.UNKNOWN: 3,
    ProtectionStatus.PROTECTED: 4,
}


def sort_repositories(items: list[RepositorySummary], sort: str) -> list[RepositorySummary]:
    if sort == "risk":
        return sorted(
            items,
            key=lambda r: (
                -r.critical_open,
                -(1 if r.last_scan and r.last_scan.result is ScanResultStatus.BLOCKED else 0),
                -r.open_violations,
                _RISK_ORDER[r.protection],
                r.full_name.lower(),
            ),
        )
    if sort == "recent":
        return sorted(
            items,
            key=lambda r: (
                -(r.last_scan.created_at.timestamp() if r.last_scan else 0),
                r.full_name.lower(),
            ),
        )
    return sorted(items, key=lambda r: (r.full_name.lower(), r.installation_id))


def _integration(installations: Sequence[InstallationView]) -> IntegrationView:
    connected = sum(1 for i in installations if i.status is AppConnection.CONNECTED)
    suspended = sum(1 for i in installations if i.status is AppConnection.SUSPENDED)
    disconnected = sum(1 for i in installations if i.status is AppConnection.DISCONNECTED)
    missing = any(
        i.missing_permissions for i in installations if i.status is AppConnection.CONNECTED
    )
    if connected and not suspended and not missing:
        return IntegrationView(
            status=IntegrationStatus.CONNECTED,
            installations_connected=connected,
            installations_suspended=suspended,
            installations_disconnected=disconnected,
            detail="The GitHub App is installed and has the permissions CommitGuard needs.",
        )
    if connected or suspended:
        detail = (
            "An installation is suspended."
            if suspended
            else "An installation is missing permissions."
        )
        return IntegrationView(
            status=IntegrationStatus.ACTION_REQUIRED,
            installations_connected=connected,
            installations_suspended=suspended,
            installations_disconnected=disconnected,
            detail=detail,
        )
    return IntegrationView(
        status=IntegrationStatus.DISCONNECTED,
        installations_connected=0,
        installations_suspended=0,
        installations_disconnected=disconnected,
        detail="No active GitHub App installation is available to you.",
    )


def _health_checks(
    summary: OverviewSummary, integration: IntegrationView, monitored: int
) -> tuple[HealthCheck, ...]:
    """Explicit checks instead of a score (see docs/dashboard.md, "Security health")."""
    checks = [
        HealthCheck(
            id="github_integration",
            label="GitHub integration",
            status=HealthCheckStatus.OK
            if integration.status is IntegrationStatus.CONNECTED
            else HealthCheckStatus.ATTENTION,
            detail=integration.detail,
        ),
    ]
    if monitored == 0:
        checks.append(
            HealthCheck(
                id="required_check",
                label="Merge protection",
                status=HealthCheckStatus.UNKNOWN,
                detail="No repositories are monitored.",
            )
        )
    else:
        if (
            summary.repositories_at_risk
            or summary.repositories_unprotected
            or summary.repositories_configuration_error
        ):
            status = HealthCheckStatus.ATTENTION
        elif summary.repositories_unknown:
            status = HealthCheckStatus.UNKNOWN
        else:
            status = HealthCheckStatus.OK
        checks.append(
            HealthCheck(
                id="required_check",
                label="Merge protection",
                status=status,
                detail=(
                    f"{summary.repositories_protected} of {monitored} monitored repositories are "
                    "verified to require a CommitGuard check; "
                    f"{summary.repositories_unknown} not verified"
                    + (
                        f"; {summary.repositories_at_risk} at risk (GitHub App access lost)."
                        if summary.repositories_at_risk
                        else "."
                    )
                ),
            )
        )
    checks.append(
        HealthCheck(
            id="critical_violations",
            label="Critical violations",
            status=HealthCheckStatus.OK
            if summary.critical_open == 0
            else HealthCheckStatus.ATTENTION,
            detail=f"{summary.critical_open} open critical violation(s).",
        )
    )
    checks.append(
        HealthCheck(
            id="open_violations",
            label="Open violations",
            status=HealthCheckStatus.OK
            if summary.open_violations == 0
            else HealthCheckStatus.ATTENTION,
            detail=f"{summary.open_violations} blocking violation(s) are currently present.",
        )
    )
    checks.append(
        HealthCheck(
            id="scan_errors",
            label="Scan reliability",
            status=HealthCheckStatus.OK
            if summary.scans_error == 0
            else HealthCheckStatus.ATTENTION,
            detail=f"{summary.scans_error} scan(s) could not be completed in this period.",
        )
    )
    checks.append(
        HealthCheck(
            id="configuration",
            label="Configuration",
            status=HealthCheckStatus.OK
            if summary.repositories_configuration_error == 0
            else HealthCheckStatus.ATTENTION,
            detail=f"{summary.repositories_configuration_error} repositories have an invalid "
            "CommitGuard configuration.",
        )
    )
    return tuple(checks)
