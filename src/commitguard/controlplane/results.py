"""Recording scan results and tracking whether each violation is still present.

Finding versus violation
========================

* A **finding** row is one detector finding in one scan: immutable history.
  Every finding of a completed scan is stored, including findings a disabled
  policy allowed.
* A **violation** is a finding whose policy action was ``block`` or ``warn``,
  tracked across scans by the finding fingerprint (detector, rule, commit SHA
  and evidence) within one repository. The same attribution on the same commit
  is one violation however often it is scanned.

Violation status
================

A violation is *exposed* wherever CommitGuard saw it:

* **pull request exposure** - active while the newest completed scan of that
  pull request still contains the finding. Pull request scans always cover the
  complete ``base..head`` range, so absence means the commit left the pull
  request (rewritten, removed, or the base now contains a fixed history). A
  closed pull request ends the exposure; a merged one moves it to the base
  branch.
* **merge group exposure** - active while the merge queue's temporary merge
  group commit that contained the finding exists; it ends when GitHub destroys
  the merge group (merged into the base branch: the exposure moves to that
  branch; invalidated or dequeued: it ends).
* **branch exposure** - push scans are incremental (only new commits), so
  absence proves nothing. The exposure ends when a later push scan of that
  branch shows the commit is no longer reachable from the branch head (history
  was rewritten), or the branch is deleted. When reachability cannot be
  determined the exposure stays active: CommitGuard does not guess in the
  permissive direction.

``open``          at least one active exposure: the violation is currently present
                  in a pull request or branch CommitGuard monitors.
``acknowledged``  open, and a security manager has recorded that they reviewed it.
                  Enforcement is unchanged: GitHub checks still fail.
``resolved``      no active exposure remains. Set only by CommitGuard from scan and
                  GitHub events, never by a user. History is kept.

A violation that appears again after being resolved is reopened and its
acknowledgement cleared, so a new occurrence is reviewed again.

Stale results: if a newer scan of the same pull request or branch has already
completed, an older scan's findings are stored as history but do not change
exposures, so a slow scan cannot reopen or close anything behind a newer one.

Notifications: newly opened (or reopened) violations that a scan *blocked* with
severity ``high`` or ``critical`` produce one notification per rule per pull
request, branch or merge group - not one per commit - written to the
notification outbox in the same transaction as the violations. A blocked merge
group additionally produces a ``merge_queue_failure`` notification.
"""

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from commitguard.audit.models import AuditEvent, AuditEventType
from commitguard.core.decision import Action
from commitguard.core.result import Severity
from commitguard.git.repository import Repository
from commitguard.github.pull_requests import branch_group_key, group_key, merge_group_key
from commitguard.github.storage import JobState, ScanJob, SqliteStateStore
from commitguard.notifications.deduplication import domain_key
from commitguard.notifications.models import NotificationEvent, NotificationType
from commitguard.notifications.outbox import account_for_installation, emit
from commitguard.observability.logging import get_logger
from commitguard.security.sanitization import sanitize_for_terminal
from commitguard.security.secrets import redact
from commitguard.services.audit import AuditService
from commitguard.services.reports import EvaluatedFinding
from commitguard.services.scan import ScanResult

log = get_logger(__name__)

MAX_IDENTITY_CHARS = 256
MAX_TEXT_CHARS = 1000
MAX_EVIDENCE_VALUE_CHARS = 512
MAX_IDENTITY_LOOKUPS = 500
TRACKED_ACTIONS = (Action.BLOCK, Action.WARN)
NOTIFY_SEVERITIES = {
    Severity.CRITICAL: NotificationType.CRITICAL_VIOLATION,
    Severity.HIGH: NotificationType.HIGH_VIOLATION,
}
MAX_NOTIFIED_VIOLATIONS = 20


def clean_text(value: str, limit: int = MAX_TEXT_CHARS) -> str:
    """Untrusted text as stored for display: secrets redacted, control characters visible."""
    return sanitize_for_terminal(redact(value), max_length=limit)


def _ts(value: datetime) -> float:
    return value.timestamp()


@dataclass(frozen=True, slots=True)
class _Group:
    key: str
    kind: str  # "pull_request" | "branch" | "merge_group"
    label: str


def job_group(job: ScanJob) -> _Group:
    if job.event == "merge_group":
        base = (job.context.ref or "").removeprefix("refs/heads/")
        return _Group(job.group_key, "merge_group", clean_text(f"merge queue ({base})", 256))
    if job.pull_request_number is not None:
        return _Group(
            group_key(job.pull_request_number), "pull_request", f"#{job.pull_request_number}"
        )
    ref = job.context.ref or ""
    return _Group(branch_group_key(ref), "branch", clean_text(ref, 256))


def _closed_reason(number: int) -> str:
    return f"pull request #{number} was closed without merging"


def _evidence_document(item: EvaluatedFinding) -> str:
    return json.dumps(
        [
            {
                "source": evidence.source.value,
                "source_label": evidence.source.label,
                "value": clean_text(evidence.value, MAX_EVIDENCE_VALUE_CHARS),
                "line_number": evidence.line_number,
                "matched": [
                    {
                        "kind": reason.kind.value,
                        "value": clean_text(reason.value, 256),
                        "rule": clean_text(reason.rule, 256),
                    }
                    for reason in evidence.matched
                ],
                "notes": [clean_text(note, 256) for note in evidence.notes],
            }
            for evidence in item.finding.evidence
        ],
        ensure_ascii=True,
    )


class ScanResultRecorder:
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

    # ------------------------------------------------------------------ #
    # Scan completion
    # ------------------------------------------------------------------ #
    def record_completed(
        self,
        job: ScanJob,
        result: ScanResult,
        *,
        state: JobState,
        conclusion: str,
        repository: Repository | None,
        organization_policy_version: int | None,
        governance_record: str | None = None,
        governance_fingerprint: str | None = None,
    ) -> None:
        """Store the result, its findings and the violation lifecycle in one transaction."""
        now = self._now()
        # Errors and cancellations never reach this method: they change no violation state.
        findings = [f for commit in result.report.commits for f in commit.findings]
        identities = self._identities(repository, {f.finding.commit_sha for f in findings})
        group = job_group(job)
        current = {f.fingerprint for f in findings if f.action in TRACKED_ACTIONS}
        # Reachability checks run Git, so they happen before the database transaction.
        unreachable = self._unreachable_branch_exposures(job, group, current, repository)

        stats = result.statistics
        metadata = result.metadata
        notices = [*result.plan.notices]
        # Monitor mode reports block as warn; alerts still go out for what would have blocked.
        monitored = (
            {r.policy_id for r in result.effective.rules if r.monitor_mode}
            if result.effective is not None
            else set()
        )
        events: list[AuditEvent] = []
        with self._store.transaction() as db:
            db.execute(
                "UPDATE scan_jobs SET state = ?, scan_id = ?, result_action = ?, conclusion = ?, "
                "commits_scanned = ?, violations = ?, warnings = ?, lease_expires_at = NULL, "
                "base_sha = ?, completed_at = ?, tool_version = ?, rules_version = ?, "
                "policy_version = ?, policy_source = ?, organization_policy_version = ?, "
                "effective_policies = ?, findings_count = ?, detector_failures = ?, notices = ?, "
                "governance = ?, governance_fingerprint = ?, repository_policies = ?, "
                "updated_at = ? WHERE job_id = ?",
                (
                    state.value,
                    metadata.scan_id,
                    result.action.value,
                    conclusion,
                    stats.commits_scanned,
                    stats.violations,
                    stats.warnings,
                    metadata.base_sha,
                    _ts(now),
                    metadata.tool_version,
                    metadata.rules_version,
                    metadata.policy_version,
                    clean_text(metadata.policy_source, 500),
                    organization_policy_version,
                    json.dumps(
                        [
                            {"id": p.id, "enabled": p.enabled, "action": p.action.value}
                            for p in result.policies
                        ]
                    ),
                    stats.findings,
                    stats.detector_failures,
                    json.dumps([clean_text(n, 1000) for n in notices[:20]]),
                    governance_record,
                    governance_fingerprint,
                    json.dumps(result.repository_overrides, sort_keys=True)
                    if result.effective is not None
                    else None,
                    _ts(now),
                    job.job_id,
                ),
            )
            stale = self._newer_scan_completed(db, job)
            touched: set[str] = set()
            new_blocked: dict[tuple[NotificationType, str], list[str]] = {}
            for item in findings:
                violation_id = None
                if item.action in TRACKED_ACTIONS:
                    violation_id, event = self._upsert_violation(
                        db, job, item, identities, now, stale=stale
                    )
                    touched.add(violation_id)
                    if event is not None:
                        events.append(event)
                        notification_type = NOTIFY_SEVERITIES.get(item.finding.severity)
                        would_block = item.action is Action.BLOCK or (
                            item.action is Action.WARN and item.finding.rule_id in monitored
                        )
                        if not stale and would_block and notification_type is not None:
                            key = (notification_type, item.finding.rule_id)
                            new_blocked.setdefault(key, []).append(violation_id)
                    if not stale:
                        self._activate_exposure(db, job, group, violation_id, now)
                author, committer = identities.get(item.finding.commit_sha or "", (None, None))
                self._insert_finding(db, job, item, violation_id, author, committer, now)
            if not stale:
                closed = self._close_absent_exposures(db, job, group, current, unreachable, now)
                touched.update(closed)
            events.extend(self._refresh_statuses(db, touched, now))
            events = [self._store.insert_audit_event(db, e) for e in events]
            account_id = account_for_installation(db, job.installation_id)
            if account_id is not None:
                aggregate = bool(new_blocked) and _aggregates_violations(db, account_id)
                for (notification_type, rule_id), ids in sorted(new_blocked.items()):
                    notification = self._violation_notification(
                        job,
                        group,
                        account_id,
                        notification_type,
                        rule_id,
                        ids,
                        monitored=rule_id in monitored,
                        aggregated=aggregate,
                    )
                    emit(db, notification, now)
                    if aggregate:
                        emit(db, _violation_digest(db, notification, now), now)
                if job.event == "merge_group" and state is JobState.FAILED and not stale:
                    emit(
                        db,
                        merge_queue_failure(
                            job,
                            account_id,
                            f"CommitGuard blocked the merge group: {stats.violations} "
                            "violation(s). The merge queue cannot merge it.",
                        ),
                        now,
                    )
        for event in events:
            self._audit.log_stored(event)

    @staticmethod
    def _violation_notification(
        job: ScanJob,
        group: _Group,
        account_id: int,
        notification_type: NotificationType,
        rule_id: str,
        violation_ids: list[str],
        *,
        monitored: bool = False,
        aggregated: bool = False,
    ) -> NotificationEvent:
        severity = (
            Severity.CRITICAL
            if notification_type is NotificationType.CRITICAL_VIOLATION
            else Severity.HIGH
        )
        where = {
            "pull_request": f"pull request {group.label}",
            "merge_group": group.label,
        }.get(group.kind, f"branch {group.label}")
        count = len(violation_ids)
        return NotificationEvent(
            type=notification_type,
            account_id=account_id,
            severity=severity,
            installation_id=job.installation_id,
            repository_id=job.repository.id,
            resource_type="violation",
            resource_id=violation_ids[0],
            dedup_key=domain_key(
                notification_type,
                job.installation_id,
                job.repository.id,
                group.key,
                rule_id,
            ),
            title=(
                f"Would block (monitor mode): {rule_id} in {job.repository.full_name}"
                if monitored
                else f"Blocked: {rule_id} in {job.repository.full_name}"
            ),
            body=(
                (
                    f"CommitGuard found {count} commit(s) in {where} of "
                    f"{job.repository.full_name} ({rule_id}, {severity.value}) at "
                    f"{job.head_sha[:12]} that policy blocks. The repository is in monitor mode, "
                    "so the GitHub check reports a warning instead of failing."
                )
                if monitored
                else (
                    f"CommitGuard blocked {count} commit(s) in {where} of "
                    f"{job.repository.full_name} ({rule_id}, {severity.value}) at "
                    f"{job.head_sha[:12]}. The GitHub check failed; remediation is shown on the "
                    "violation page."
                )
            ),
            metadata={
                "rule": rule_id,
                "violations": count,
                "violation_ids": ",".join(violation_ids[:MAX_NOTIFIED_VIOLATIONS]),
                "scan": job.job_id,
                "head_sha": job.head_sha,
                "source": group.kind,
                "location": where,
                "monitor_mode": monitored,
                # Organization digest enabled: e-mail and webhooks go to the digest instead.
                "aggregated": aggregated,
            },
        )

    # ------------------------------------------------------------------ #
    # Merge queue
    # ------------------------------------------------------------------ #
    def merge_group_destroyed(
        self,
        installation_id: int,
        repository_id: int,
        head_sha: str,
        *,
        reason: str | None,
        base_ref: str,
    ) -> None:
        """End merge group exposures; a merged group's violations move to the base branch."""
        now = self._now()
        key = merge_group_key(head_sha)
        merged = reason == "merged"
        text = (
            f"merge group {head_sha[:12]} was merged into {clean_text(base_ref, 200)}"
            if merged
            else f"merge group {head_sha[:12]} was {reason or 'removed'}"
        )
        events: list[AuditEvent] = []
        with self._store.transaction() as db:
            rows = db.execute(
                "SELECT violation_id FROM violation_exposures WHERE installation_id = ? "
                "AND repository_id = ? AND group_key = ? AND active = 1",
                (installation_id, repository_id, key),
            ).fetchall()
            ids = {row["violation_id"] for row in rows}
            db.execute(
                "UPDATE violation_exposures SET active = 0, closed_at = ?, closed_reason = ? "
                "WHERE installation_id = ? AND repository_id = ? AND group_key = ? AND active = 1",
                (_ts(now), text, installation_id, repository_id, key),
            )
            if merged:
                branch = _Group(branch_group_key(base_ref), "branch", clean_text(base_ref, 256))
                for violation_id in ids:
                    self._activate_exposure_row(
                        db, installation_id, repository_id, branch, violation_id, None, now
                    )
            events.extend(self._refresh_statuses(db, ids, now))
            events = [self._store.insert_audit_event(db, e) for e in events]
        for event in events:
            self._audit.log_stored(event)

    # ------------------------------------------------------------------ #
    # GitHub lifecycle events
    # ------------------------------------------------------------------ #
    def pull_request_closed(
        self,
        installation_id: int,
        repository_id: int,
        number: int,
        *,
        merged: bool,
        base_ref: str | None,
    ) -> None:
        now = self._now()
        key = group_key(number)
        reason = (
            f"pull request #{number} was merged into {clean_text(base_ref or 'its base', 200)}"
            if merged
            else _closed_reason(number)
        )
        events: list[AuditEvent] = []
        with self._store.transaction() as db:
            rows = db.execute(
                "SELECT violation_id FROM violation_exposures WHERE installation_id = ? "
                "AND repository_id = ? AND group_key = ? AND active = 1",
                (installation_id, repository_id, key),
            ).fetchall()
            ids = {row["violation_id"] for row in rows}
            db.execute(
                "UPDATE violation_exposures SET active = 0, closed_at = ?, closed_reason = ? "
                "WHERE installation_id = ? AND repository_id = ? AND group_key = ? AND active = 1",
                (_ts(now), reason, installation_id, repository_id, key),
            )
            if merged and base_ref:
                branch = _Group(branch_group_key(base_ref), "branch", clean_text(base_ref, 256))
                for violation_id in ids:
                    self._activate_exposure_row(
                        db, installation_id, repository_id, branch, violation_id, None, now
                    )
            events.extend(self._refresh_statuses(db, ids, now))
            events = [self._store.insert_audit_event(db, e) for e in events]
        for event in events:
            self._audit.log_stored(event)

    def pull_request_reopened(self, installation_id: int, repository_id: int, number: int) -> None:
        """Restore exposures ended by closing a pull request whose commits did not change."""
        now = self._now()
        key = group_key(number)
        events: list[AuditEvent] = []
        with self._store.transaction() as db:
            rows = db.execute(
                "SELECT violation_id FROM violation_exposures WHERE installation_id = ? "
                "AND repository_id = ? AND group_key = ? AND active = 0 AND closed_reason = ?",
                (installation_id, repository_id, key, _closed_reason(number)),
            ).fetchall()
            ids = {row["violation_id"] for row in rows}
            db.execute(
                "UPDATE violation_exposures SET active = 1, closed_at = NULL, "
                "closed_reason = NULL, opened_at = ? WHERE installation_id = ? "
                "AND repository_id = ? AND group_key = ? AND active = 0 AND closed_reason = ?",
                (_ts(now), installation_id, repository_id, key, _closed_reason(number)),
            )
            events.extend(self._refresh_statuses(db, ids, now))
            events = [self._store.insert_audit_event(db, e) for e in events]
        for event in events:
            self._audit.log_stored(event)

    def branch_deleted(self, installation_id: int, repository_id: int, ref: str) -> None:
        now = self._now()
        key = branch_group_key(ref)
        reason = f"branch {clean_text(ref, 200)} was deleted"
        events: list[AuditEvent] = []
        with self._store.transaction() as db:
            rows = db.execute(
                "SELECT violation_id FROM violation_exposures WHERE installation_id = ? "
                "AND repository_id = ? AND group_key = ? AND active = 1",
                (installation_id, repository_id, key),
            ).fetchall()
            ids = {row["violation_id"] for row in rows}
            db.execute(
                "UPDATE violation_exposures SET active = 0, closed_at = ?, closed_reason = ? "
                "WHERE installation_id = ? AND repository_id = ? AND group_key = ? AND active = 1",
                (_ts(now), reason, installation_id, repository_id, key),
            )
            events.extend(self._refresh_statuses(db, ids, now))
            events = [self._store.insert_audit_event(db, e) for e in events]
        for event in events:
            self._audit.log_stored(event)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _identities(
        repository: Repository | None, shas: Iterable[str | None]
    ) -> dict[str, tuple[str, str]]:
        wanted = sorted(s for s in shas if s)[:MAX_IDENTITY_LOOKUPS]
        if repository is None or not wanted:
            return {}
        identities: dict[str, tuple[str, str]] = {}
        try:
            for commit in repository.iter_commits(wanted):
                if commit.sha:
                    identities[commit.sha] = (
                        clean_text(str(commit.author), MAX_IDENTITY_CHARS),
                        clean_text(str(commit.committer), MAX_IDENTITY_CHARS),
                    )
        except Exception as exc:  # noqa: BLE001 - identities are display data only
            log.warning("finding_identities_unavailable", error_type=type(exc).__name__)
        return identities

    def _unreachable_branch_exposures(
        self,
        job: ScanJob,
        group: _Group,
        current: set[str],
        repository: Repository | None,
    ) -> set[str]:
        """Active branch exposures whose commit is provably no longer on the branch."""
        if group.kind != "branch" or repository is None:
            return set()
        rows = self._store.query(
            "SELECT e.violation_id, v.fingerprint, v.commit_sha FROM violation_exposures e "
            "JOIN violations v ON v.violation_id = e.violation_id WHERE e.installation_id = ? "
            "AND e.repository_id = ? AND e.group_key = ? AND e.active = 1 LIMIT 1000",
            (job.installation_id, job.repository.id, group.key),
        )
        gone: set[str] = set()
        for row in rows:
            if row["fingerprint"] in current or not row["commit_sha"]:
                continue
            try:
                reachable = repository.is_ancestor(row["commit_sha"], job.head_sha)
            except Exception as exc:  # noqa: BLE001 - unknown keeps the exposure open
                log.warning("reachability_check_failed", error_type=type(exc).__name__)
                reachable = None
            if reachable is False:
                gone.add(row["violation_id"])
        return gone

    @staticmethod
    def _newer_scan_completed(db: sqlite3.Connection, job: ScanJob) -> bool:
        row = db.execute(
            "SELECT 1 FROM scan_jobs WHERE installation_id = ? AND repository_id = ? "
            "AND group_key = ? AND sequence > ? AND state IN ('passed', 'failed') LIMIT 1",
            (job.installation_id, job.repository.id, job.group_key, job.sequence),
        ).fetchone()
        return row is not None

    def _upsert_violation(
        self,
        db: sqlite3.Connection,
        job: ScanJob,
        item: EvaluatedFinding,
        identities: dict[str, tuple[str, str]],
        now: datetime,
        *,
        stale: bool,
    ) -> tuple[str, AuditEvent | None]:
        finding = item.finding
        author = identities.get(finding.commit_sha or "", (None, None))[0]
        row = db.execute(
            "SELECT violation_id, status FROM violations WHERE installation_id = ? "
            "AND repository_id = ? AND fingerprint = ?",
            (job.installation_id, job.repository.id, item.fingerprint),
        ).fetchone()
        common: dict[str, Any] = {
            "installation_id": job.installation_id,
            "repository_id": job.repository.id,
            "repository": job.repository.full_name,
            "head_sha": job.head_sha,
            "action": item.action,
        }
        if row is None:
            violation_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO violations (violation_id, installation_id, repository_id, "
                "fingerprint, "
                "rule_id, detector, severity, severity_rank, action, title, commit_sha, author, "
                "status, first_detected_at, last_detected_at, first_job_id, last_job_id, "
                "detections, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, "
                "?, ?, 1, ?)",
                (
                    violation_id,
                    job.installation_id,
                    job.repository.id,
                    item.fingerprint,
                    finding.rule_id,
                    finding.detector,
                    finding.severity.value,
                    finding.severity.rank,
                    item.action.value,
                    clean_text(finding.title, 200),
                    finding.commit_sha,
                    author,
                    _ts(now),
                    _ts(now),
                    job.job_id,
                    job.job_id,
                    _ts(now),
                ),
            )
            return violation_id, self._audit.build(
                AuditEventType.VIOLATION_OPENED,
                **common,
                violation=violation_id,
                rule=finding.rule_id,
                severity=finding.severity.value,
            )
        violation_id = row["violation_id"]
        reopen = row["status"] == "resolved" and not stale
        db.execute(
            "UPDATE violations SET last_detected_at = ?, last_job_id = ?, "
            "detections = detections + 1, action = ?, severity = ?, severity_rank = ?, "
            "title = ?, author = COALESCE(?, author), updated_at = ? WHERE violation_id = ?",
            (
                _ts(now),
                job.job_id,
                item.action.value,
                finding.severity.value,
                finding.severity.rank,
                clean_text(finding.title, 200),
                author,
                _ts(now),
                violation_id,
            ),
        )
        if not reopen:
            return violation_id, None
        db.execute(
            "UPDATE violations SET status = 'open', resolved_at = NULL, resolution = NULL, "
            "acknowledged_at = NULL, acknowledged_by_id = NULL, acknowledged_by_login = NULL, "
            "acknowledgement_note = NULL WHERE violation_id = ?",
            (violation_id,),
        )
        return violation_id, self._audit.build(
            AuditEventType.VIOLATION_REOPENED,
            **common,
            violation=violation_id,
            rule=finding.rule_id,
        )

    def _activate_exposure(
        self,
        db: sqlite3.Connection,
        job: ScanJob,
        group: _Group,
        violation_id: str,
        now: datetime,
    ) -> None:
        self._activate_exposure_row(
            db, job.installation_id, job.repository.id, group, violation_id, job.job_id, now
        )

    @staticmethod
    def _activate_exposure_row(
        db: sqlite3.Connection,
        installation_id: int,
        repository_id: int,
        group: _Group,
        violation_id: str,
        job_id: str | None,
        now: datetime,
    ) -> None:
        db.execute(
            "INSERT INTO violation_exposures (violation_id, installation_id, repository_id, "
            "group_key, kind, label, active, first_job_id, last_job_id, opened_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?) ON CONFLICT (violation_id, group_key) DO UPDATE "
            "SET active = 1, last_job_id = COALESCE(excluded.last_job_id, last_job_id), "
            "opened_at = CASE WHEN active = 1 THEN opened_at ELSE excluded.opened_at END, "
            "closed_at = NULL, closed_reason = NULL",
            (
                violation_id,
                installation_id,
                repository_id,
                group.key,
                group.kind,
                group.label,
                job_id,
                job_id,
                _ts(now),
            ),
        )

    @staticmethod
    def _close_absent_exposures(
        db: sqlite3.Connection,
        job: ScanJob,
        group: _Group,
        current: set[str],
        unreachable: set[str],
        now: datetime,
    ) -> set[str]:
        rows = db.execute(
            "SELECT e.violation_id, v.fingerprint FROM violation_exposures e JOIN violations v "
            "ON v.violation_id = e.violation_id WHERE e.installation_id = ? "
            "AND e.repository_id = ? AND e.group_key = ? AND e.active = 1",
            (job.installation_id, job.repository.id, group.key),
        ).fetchall()
        closed: set[str] = set()
        for row in rows:
            if row["fingerprint"] in current:
                continue
            if group.kind == "pull_request":
                reason = (
                    f"no longer part of pull request {group.label} (scan of {job.head_sha[:12]})"
                )
            elif row["violation_id"] in unreachable:
                reason = (
                    f"commit no longer reachable from {group.label} at {job.head_sha[:12]} "
                    "(history rewritten)"
                )
            else:
                continue
            db.execute(
                "UPDATE violation_exposures SET active = 0, closed_at = ?, closed_reason = ?, "
                "last_job_id = ? WHERE violation_id = ? AND group_key = ?",
                (_ts(now), reason, job.job_id, row["violation_id"], group.key),
            )
            closed.add(row["violation_id"])
        return closed

    def _refresh_statuses(
        self, db: sqlite3.Connection, violation_ids: Iterable[str], now: datetime
    ) -> list[AuditEvent]:
        events: list[AuditEvent] = []
        for violation_id in sorted(violation_ids):
            row = db.execute(
                "SELECT v.status, v.installation_id, v.repository_id, v.rule_id, v.action, "
                "(SELECT COUNT(*) FROM violation_exposures e WHERE e.violation_id = v.violation_id "
                "AND e.active = 1) AS active, (SELECT closed_reason FROM violation_exposures e "
                "WHERE e.violation_id = v.violation_id AND e.active = 0 ORDER BY closed_at DESC "
                "LIMIT 1) AS reason FROM violations v WHERE v.violation_id = ?",
                (violation_id,),
            ).fetchone()
            if row is None:
                continue
            if row["active"] == 0 and row["status"] == "open":
                resolution = row["reason"] or "not present in a newer scan"
                db.execute(
                    "UPDATE violations SET status = 'resolved', resolved_at = ?, resolution = ?, "
                    "updated_at = ? WHERE violation_id = ?",
                    (_ts(now), resolution, _ts(now), violation_id),
                )
                events.append(
                    self._audit.build(
                        AuditEventType.VIOLATION_RESOLVED,
                        installation_id=row["installation_id"],
                        repository_id=row["repository_id"],
                        action=Action(row["action"]),
                        violation=violation_id,
                        rule=row["rule_id"],
                        reason=resolution,
                    )
                )
            elif row["active"] > 0 and row["status"] == "resolved":
                db.execute(
                    "UPDATE violations SET status = 'open', resolved_at = NULL, resolution = NULL, "
                    "acknowledged_at = NULL, acknowledged_by_id = NULL, "
                    "acknowledged_by_login = NULL, acknowledgement_note = NULL, updated_at = ? "
                    "WHERE violation_id = ?",
                    (_ts(now), violation_id),
                )
                events.append(
                    self._audit.build(
                        AuditEventType.VIOLATION_REOPENED,
                        installation_id=row["installation_id"],
                        repository_id=row["repository_id"],
                        action=Action(row["action"]),
                        violation=violation_id,
                        rule=row["rule_id"],
                    )
                )
        return events

    @staticmethod
    def _insert_finding(
        db: sqlite3.Connection,
        job: ScanJob,
        item: EvaluatedFinding,
        violation_id: str | None,
        author: str | None,
        committer: str | None,
        now: datetime,
    ) -> None:
        finding = item.finding
        db.execute(
            "INSERT INTO findings (job_id, installation_id, repository_id, violation_id, "
            "fingerprint, commit_sha, rule_id, detector, severity, severity_rank, confidence, "
            "action, policy_id, reason, title, message, remediation, evidence, author, committer, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job.job_id,
                job.installation_id,
                job.repository.id,
                violation_id,
                item.fingerprint,
                finding.commit_sha,
                finding.rule_id,
                finding.detector,
                finding.severity.value,
                finding.severity.rank,
                finding.confidence.value,
                item.action.value,
                item.policy_id,
                clean_text(item.reason, 500),
                clean_text(finding.title, 200),
                clean_text(finding.message, MAX_TEXT_CHARS),
                clean_text(finding.remediation, MAX_TEXT_CHARS),
                _evidence_document(item),
                author,
                committer,
                _ts(now),
            ),
        )


def _aggregates_violations(db: sqlite3.Connection, account_id: int) -> bool:
    row = db.execute(
        "SELECT document FROM organization_settings WHERE account_id = ?", (account_id,)
    ).fetchone()
    if row is None:
        return False
    try:
        return bool(json.loads(str(row["document"])).get("aggregate_violation_alerts", False))
    except ValueError:
        return False


def _violation_digest(
    db: sqlite3.Connection, event: NotificationEvent, now: datetime
) -> NotificationEvent:
    """One organization-level alert per rule and hour, however many repositories."""
    rule = str(event.metadata.get("rule", ""))
    window = NotificationType.VIOLATION_DIGEST
    since = now.timestamp() - (now.timestamp() % 3600)
    repositories = db.execute(
        "SELECT COUNT(DISTINCT repository_id) AS n FROM notification_events WHERE account_id = ? "
        "AND type IN ('critical_violation', 'high_violation') AND last_occurred_at >= ? "
        "AND json_extract(metadata, '$.rule') = ?",
        (event.account_id, since, rule),
    ).fetchone()["n"]
    count = max(int(repositories or 0), 1)
    return NotificationEvent(
        type=window,
        account_id=event.account_id,
        severity=event.severity,
        resource_type="organization",
        resource_id=str(event.account_id),
        dedup_key=domain_key(window, event.account_id, rule),
        title=f"CommitGuard detected blocked violations of {rule} in {count} repositories",
        body=(
            f"Violations of {rule} ({event.severity.value}) were detected in {count} "
            "repository(ies) of your organization in the last hour. E-mail and webhooks are "
            "aggregated; the dashboard lists every repository and violation."
        ),
        metadata={"rule": rule, "repositories": count},
    )


def merge_queue_failure(job: ScanJob, account_id: int, body: str) -> NotificationEvent:
    return NotificationEvent(
        type=NotificationType.MERGE_QUEUE_FAILURE,
        account_id=account_id,
        severity=Severity.HIGH,
        installation_id=job.installation_id,
        repository_id=job.repository.id,
        resource_type="scan",
        resource_id=job.job_id,
        dedup_key=domain_key(
            NotificationType.MERGE_QUEUE_FAILURE,
            job.installation_id,
            job.repository.id,
            job.head_sha,
        ),
        title=f"Merge queue blocked in {job.repository.full_name}",
        body=f"{body} Merge group commit {job.head_sha[:12]}.",
        metadata={"scan": job.job_id, "head_sha": job.head_sha, "source": "merge_queue"},
    )


def scan_result_label(
    state: str, result_action: str | None, failure_kind: str | None = None
) -> str:
    """The dashboard's scan result for a stored job state (see ``views.ScanResultStatus``)."""
    if state == JobState.CANCELLED.value and failure_kind == "stale":
        return "stale"
    if state == JobState.PASSED.value:
        return "warning" if result_action == Action.WARN.value else "pass"
    return {
        JobState.QUEUED.value: "queued",
        JobState.RUNNING.value: "running",
        JobState.FAILED.value: "blocked",
        JobState.ERROR.value: "error",
        JobState.CANCELLED.value: "cancelled",
    }.get(state, "error")
