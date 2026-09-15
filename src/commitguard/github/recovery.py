"""Operational recovery, run by the maintenance loop.

What fails, and what happens next:

=================================  =========================================================
Failure                            Recovery
=================================  =========================================================
Event processing crashed           The event record is ``failed`` (or ``processing`` for
(database error, process killed)   longer than 10 minutes and then marked ``failed``);
                                   GitHub's redelivery of the same delivery ID is processed
                                   again instead of being dropped as a duplicate.
Scan could not complete: GitHub    The execution is ``error`` and its check failed closed
API, network or fetch timeout      when it could be published. Up to
                                   :data:`MAX_AUTOMATIC_RETRIES` new executions (trigger
                                   ``retry``) are scheduled after 5 and 20 minutes, only
                                   while the scan is still the newest for its pull request,
                                   branch or merge group, the installation is active and
                                   monitoring is on. A retry never publishes success for
                                   commits it did not scan.
Scan crashed on every attempt      The execution becomes ``error`` after three claims, is
                                   audited, and its check is completed as a failure.
Notification delivery failed       Handled by :mod:`commitguard.notifications.retry`
                                   (bounded retries with backoff).
=================================  =========================================================

Authorization and configuration errors are not retried automatically: they
need a human (fix permissions or configuration, then re-run the check).
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from commitguard.audit.models import AuditEventType
from commitguard.github.storage import JobState, ScanTrigger, SqliteStateStore
from commitguard.observability.logging import correlation, get_logger
from commitguard.observability.metrics import SCAN_RETRIES, Metrics
from commitguard.services.audit import AuditService

log = get_logger(__name__)

MAX_AUTOMATIC_RETRIES = 2
RETRY_BACKOFF = (timedelta(minutes=5), timedelta(minutes=20))
RETRY_WINDOW = timedelta(hours=24)
RETRYABLE_FAILURES = ("infrastructure", "timeout")
RECOVERY_BATCH = 50


class RecoveryService:
    def __init__(
        self,
        store: SqliteStateStore,
        audit: AuditService,
        metrics: Metrics,
        *,
        enqueue: Callable[[str], bool],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._audit = audit
        self._metrics = metrics
        self._enqueue = enqueue
        self._now = now

    def run_once(self) -> dict[str, int]:
        now = self._now()
        stuck = self._store.fail_stuck_deliveries(now)
        if stuck:
            log.warning("webhook_deliveries_abandoned", count=stuck)
        return {"stuck_deliveries": stuck, "scan_retries": self.retry_failed_scans()}

    def retry_failed_scans(self) -> int:
        now = self._now()
        rows = self._store.query(
            "SELECT j.job_id FROM scan_jobs j WHERE j.state = 'error' "
            "AND j.failure_kind IN (?, ?) AND j.completed_at >= ? "
            # still the newest execution of the newest scan of its group
            "AND NOT EXISTS (SELECT 1 FROM scan_jobs n WHERE n.installation_id = j.installation_id "
            "AND n.repository_id = j.repository_id AND n.group_key = j.group_key "
            "AND n.sequence > j.sequence) "
            "AND EXISTS (SELECT 1 FROM installations i WHERE i.installation_id = j.installation_id "
            "AND i.state = 'active') "
            "ORDER BY j.sequence LIMIT ?",
            (*RETRYABLE_FAILURES, (now - RETRY_WINDOW).timestamp(), RECOVERY_BATCH),
        )
        scheduled = 0
        for row in rows:
            job = self._store.get_job(str(row["job_id"]))
            if job is None or job.state is not JobState.ERROR or job.completed_at is None:
                continue
            retries = sum(
                1
                for e in self._store.list_executions(
                    job.installation_id, job.repository.id, job.scan_key
                )
                if e.trigger is ScanTrigger.RETRY
            )
            if retries >= MAX_AUTOMATIC_RETRIES:
                continue
            if now - job.completed_at < RETRY_BACKOFF[retries]:
                continue
            if not self._store.monitoring_enabled(job.installation_id, job.repository.id):
                continue
            execution, created = self._store.create_execution(
                job, trigger=ScanTrigger.RETRY, now=now
            )
            if not created:
                continue
            with correlation(job_id=execution.job_id, repository=job.repository.full_name):
                self._audit.record(
                    AuditEventType.SCAN_RETRY_SCHEDULED,
                    installation_id=job.installation_id,
                    repository_id=job.repository.id,
                    repository=job.repository.full_name,
                    head_sha=job.head_sha,
                    job=execution.job_id,
                    previous_scan=job.job_id,
                    execution=execution.execution,
                    failure_kind=job.failure_kind,
                    retry=retries + 1,
                )
            self._metrics.increment(SCAN_RETRIES)
            self._enqueue(execution.job_id)
            scheduled += 1
        return scheduled
