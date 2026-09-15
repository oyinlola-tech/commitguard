"""The CommitGuard GitHub App service.

::

    GitHub --HTTPS webhook--> WSGI endpoint (POST /webhooks/github)
             rate limit -> size/content-type -> signature -> headers -> JSON
             -> normalise -> event record (delivery ID: new / duplicate / retry)
             -> installation events: apply (state + audit + notification, one transaction)
             -> push / pull_request / merge_group: store job, enqueue
             -> check_run / check_suite "rerequested": new execution of the stored scan
             <- 2xx within milliseconds (no scanning on the request path)

    worker threads:      queue -> ScanWorker -> Check Run
    notification thread: outbox -> inbox / deliveries -> e-mail, webhooks (retries)
    maintenance:         re-enqueue abandoned jobs, recovery (failed deliveries,
                         infrastructure retries), retention purge

Event records: every verified delivery is stored with a processing status
(``processing`` -> ``processed`` / ``ignored`` / ``failed``). GitHub reuses the
delivery ID when a delivery is redelivered: a processed delivery is answered as
a duplicate, while a failed or abandoned one is processed again, so an outage
while handling an event does not lose it.

The WSGI application has no framework dependency. ``commitguard github serve``
runs it with a small threaded development server; production deployments put
a WSGI server behind a TLS-terminating reverse proxy (docs/deployment.md).
The service itself never serves plain HTTP to the internet.
"""

import json
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from wsgiref.types import StartResponse, WSGIEnvironment

from commitguard.audit.models import GITHUB_ACTOR, AuditEventType
from commitguard.config.sources import MandatoryPolicy, load_mandatory_policy
from commitguard.controlplane.policies import OrganizationPolicyService
from commitguard.controlplane.results import ScanResultRecorder
from commitguard.github.auth import AppCredentials, InstallationTokenProvider
from commitguard.github.checks import APP_CHECK_NAME, APP_PUSH_CHECK_NAME
from commitguard.github.client import API_URL, GitHubClient, Transport
from commitguard.github.errors import WebhookValidationError
from commitguard.github.events import (
    CheckRunRerequestedEvent,
    CheckSuiteRerequestedEvent,
    GitHubWebhookEvent,
    IgnoredEvent,
    InstallationEvent,
    InstallationRepositoriesEvent,
    MergeGroupAction,
    MergeGroupEvent,
    PullRequestEvent,
    PushEvent,
    normalize_webhook,
)
from commitguard.github.installations import InstallationService
from commitguard.github.pull_requests import (
    PullRequestDisposition,
    branch_group_key,
    disposition,
    group_key,
    merge_group_key,
)
from commitguard.github.queue import DEFAULT_QUEUE_SIZE, EventQueue, InProcessEventQueue
from commitguard.github.recovery import RecoveryService
from commitguard.github.repositories import GitHubRemoteLocator, MirrorManager, RemoteLocator
from commitguard.github.settings import AppSettings, load_settings
from commitguard.github.storage import (
    DATABASE_FILENAME,
    DeliveryStatus,
    EventProcessingStatus,
    MergeGroupState,
    NewScanJob,
    ScanJob,
    ScanTrigger,
    SqliteStateStore,
)
from commitguard.github.webhooks import MAX_WEBHOOK_BYTES, WebhookDelivery, parse_delivery
from commitguard.github.worker import ScanWorker
from commitguard.notifications.service import RUN_INTERVAL_SECONDS, NotificationService
from commitguard.notifications.settings import NotificationSettings
from commitguard.observability.logging import configure_json_logging, correlation, get_logger
from commitguard.observability.metrics import (
    CHECK_RERUNS,
    GITHUB_EVENTS_FAILED,
    GITHUB_EVENTS_RECEIVED,
    GITHUB_EVENTS_REPLAYED,
    SCANS_QUEUED,
    WEBHOOKS_DUPLICATE,
    WEBHOOKS_RECEIVED,
    WEBHOOKS_REJECTED,
    InMemoryMetrics,
)
from commitguard.security.hashing import fingerprint
from commitguard.security.rate_limit import RequestRateLimiter
from commitguard.security.secrets import Secret
from commitguard.services.audit import AuditService

log = get_logger(__name__)

WEBHOOK_PATH = "/webhooks/github"
DEFAULT_RATE_LIMIT_PER_MINUTE = 600
RECOVERY_INTERVAL_SECONDS = 60.0
RECOVER_QUEUED_AFTER = timedelta(minutes=5)
RETENTION_INTERVAL_SECONDS = 3600.0
APP_CHECK_NAMES = frozenset({APP_CHECK_NAME, APP_PUSH_CHECK_NAME})


@dataclass(frozen=True, slots=True)
class WebhookResult:
    status: int
    body: Mapping[str, str | int] = field(default_factory=dict)


class GitHubAppService:
    def __init__(
        self,
        *,
        credentials: AppCredentials,
        webhook_secret: Secret,
        store: SqliteStateStore,
        client: GitHubClient,
        mirrors: MirrorManager,
        mandatory_policy: MandatoryPolicy | None = None,
        workers: int = 2,
        retention: timedelta = timedelta(days=30),
        max_commits: int = 10_000,
        queue: EventQueue | None = None,
        metrics: InMemoryMetrics | None = None,
        rate_limit_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE,
        notification_settings: NotificationSettings | None = None,
        notifications: NotificationService | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.client = client
        self.metrics = metrics or InMemoryMetrics()
        self.queue = queue or InProcessEventQueue()
        self.audit = AuditService([store], now=now)
        self.tokens = InstallationTokenProvider(credentials, client)
        self.mirrors = mirrors
        self.installations = InstallationService(
            store, self.tokens, client, mirrors, self.audit, now=now
        )
        self.policies = OrganizationPolicyService(
            store, self.audit, service_policy=mandatory_policy, metrics=self.metrics, now=now
        )
        self.notifications = notifications or NotificationService(
            store,
            self.audit,
            self.metrics,
            notification_settings or NotificationSettings(),
            now=now,
        )
        self.recorder = ScanResultRecorder(store, self.audit, now=now)
        self.worker = ScanWorker(
            store=store,
            installations=self.installations,
            client=client,
            mirrors=mirrors,
            audit=self.audit,
            metrics=self.metrics,
            policy_resolver=self.policies.mandatory_for_installation,
            recorder=self.recorder,
            max_commits=max_commits,
            now=now,
        )
        self.recovery = RecoveryService(
            store, self.audit, self.metrics, enqueue=self.queue.put, now=now
        )
        self._credentials = credentials
        self._secret = webhook_secret
        self._workers = workers
        self._retention = retention
        self._now = now
        self._limiter = RequestRateLimiter(rate_limit_per_minute)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._maintenance_tasks: list[Callable[[], object]] = []

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def create(
        cls,
        *,
        app_id: int,
        private_key: Secret,
        webhook_secret: Secret,
        data_dir: Path,
        mandatory_policy_file: Path | None = None,
        transport: Transport | None = None,
        api_url: str = API_URL,
        remote_locator: RemoteLocator | None = None,
        allowed_git_protocols: tuple[str, ...] = ("https",),
        sleep: Callable[[float], None] = time.sleep,
        **options: Any,
    ) -> "GitHubAppService":
        credentials = AppCredentials(app_id, private_key)  # fails closed on a bad key
        mandatory = load_mandatory_policy(mandatory_policy_file) if mandatory_policy_file else None
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        metrics = options.pop("metrics", None) or InMemoryMetrics()
        client = GitHubClient(transport, api_url=api_url, sleep=sleep, metrics=metrics)
        mirrors = MirrorManager(
            data_dir / "mirrors",
            remote_locator or GitHubRemoteLocator(),
            allowed_protocols=allowed_git_protocols,
        )
        return cls(
            credentials=credentials,
            webhook_secret=webhook_secret,
            store=SqliteStateStore(data_dir / DATABASE_FILENAME),
            client=client,
            mirrors=mirrors,
            mandatory_policy=mandatory,
            metrics=metrics,
            **options,
        )

    @classmethod
    def from_settings(cls, settings: AppSettings, **overrides: Any) -> "GitHubAppService":
        return cls.create(
            app_id=settings.app_id,
            private_key=settings.private_key,
            webhook_secret=settings.webhook_secret,
            data_dir=settings.data_dir,
            mandatory_policy_file=settings.mandatory_policy_file,
            workers=settings.workers,
            retention=settings.retention,
            max_commits=settings.max_commits,
            **overrides,
        )

    # ------------------------------------------------------------------ #
    # Webhooks
    # ------------------------------------------------------------------ #
    def handle_webhook(
        self, headers: Mapping[str, str], body: bytes, *, remote_addr: str | None = None
    ) -> WebhookResult:
        self.metrics.increment(WEBHOOKS_RECEIVED)
        if not self._limiter.allow(remote_addr or "unknown"):
            self.metrics.increment(WEBHOOKS_REJECTED, reason="rate_limited")
            return WebhookResult(429, {"error": "too many requests"})
        try:
            delivery = parse_delivery(headers, body, self._secret)
        except WebhookValidationError as exc:
            self.metrics.increment(WEBHOOKS_REJECTED, reason=str(exc.status))
            log.warning("webhook_rejected", status=exc.status, reason=str(exc))
            return WebhookResult(exc.status, {"error": str(exc)})
        with correlation(delivery_id=delivery.delivery_id):
            return self._handle_verified(delivery)

    def _handle_verified(self, delivery: WebhookDelivery) -> WebhookResult:
        self.metrics.increment(GITHUB_EVENTS_RECEIVED, event=delivery.event)
        try:
            event = normalize_webhook(delivery.event, delivery.payload)
        except WebhookValidationError as exc:
            self.metrics.increment(WEBHOOKS_REJECTED, reason="invalid_payload")
            log.warning("webhook_invalid", event_name=delivery.event, reason=str(exc))
            self.audit.record(
                AuditEventType.WEBHOOK_REJECTED, event_name=delivery.event, reason=str(exc)
            )
            return WebhookResult(exc.status, {"error": str(exc)})

        action = delivery.payload.get("action")
        status = self.store.record_delivery(
            delivery.delivery_id,
            delivery.event,
            delivery.body_sha256,
            self._now(),
            action=action if isinstance(action, str) and len(action) <= 64 else None,
        )
        if status is DeliveryStatus.DUPLICATE:
            self.metrics.increment(WEBHOOKS_DUPLICATE)
            self.metrics.increment(GITHUB_EVENTS_REPLAYED, event=delivery.event)
            log.info("webhook_duplicate", event_name=delivery.event)
            return WebhookResult(200, {"status": "duplicate"})
        if status is DeliveryStatus.RETRY:
            log.info("webhook_redelivery_processed_again", event_name=delivery.event)
        if status is DeliveryStatus.CONFLICT:
            self.metrics.increment(WEBHOOKS_REJECTED, reason="delivery_conflict")
            log.warning("webhook_delivery_id_conflict", event_name=delivery.event)
            self.audit.record(
                AuditEventType.WEBHOOK_REJECTED,
                event_name=delivery.event,
                reason="delivery ID reused with a different payload",
            )
            return WebhookResult(409, {"error": "delivery already received with different content"})

        log.info("webhook_accepted", event_name=delivery.event, kind=event.kind)
        try:
            result = self._dispatch(event, delivery)
        except Exception as exc:
            # The event record stays "failed": GitHub's redelivery of this delivery ID is
            # processed again instead of being dropped as a duplicate.
            self.metrics.increment(GITHUB_EVENTS_FAILED, event=delivery.event)
            self.store.finish_delivery(
                delivery.delivery_id,
                EventProcessingStatus.FAILED,
                self._now(),
                detail=f"processing failed ({type(exc).__name__})",
            )
            raise
        ignored = result.body.get("status") in ("ignored", "duplicate")
        self.store.finish_delivery(
            delivery.delivery_id,
            EventProcessingStatus.IGNORED if ignored else EventProcessingStatus.PROCESSED,
            self._now(),
            installation_id=getattr(event, "installation_id", None),
            repository_id=getattr(getattr(event, "repository", None), "id", None),
            detail=str(result.body.get("status", "")),
        )
        return result

    def _dispatch(self, event: GitHubWebhookEvent, delivery: WebhookDelivery) -> WebhookResult:
        if isinstance(event, IgnoredEvent):
            return WebhookResult(202, {"status": "ignored"})
        if isinstance(event, InstallationEvent):
            self.installations.handle_installation(event)
            return WebhookResult(200, {"status": "processed"})
        if isinstance(event, InstallationRepositoriesEvent):
            self.installations.handle_repositories(event)
            return WebhookResult(200, {"status": "processed"})

        with correlation(
            installation_id=event.installation_id, repository=event.repository.full_name
        ):
            denial = self.installations.denial_reason(event.installation_id)
            if denial is not None:
                self.audit.record(
                    AuditEventType.AUTHORIZATION_DENIED,
                    installation_id=event.installation_id,
                    repository_id=event.repository.id,
                    repository=event.repository.full_name,
                    reason=denial,
                )
                return WebhookResult(202, {"status": "ignored"})
            if isinstance(event, PushEvent):
                return self._push(event, delivery)
            if isinstance(event, MergeGroupEvent):
                return self._merge_group(event, delivery)
            if isinstance(event, CheckRunRerequestedEvent):
                return self._check_run_rerequested(event, delivery)
            if isinstance(event, CheckSuiteRerequestedEvent):
                return self._check_suite_rerequested(event, delivery)
            return self._pull_request(event, delivery)

    def _monitoring_paused(self, installation_id: int, repository_id: int) -> bool:
        if self.store.monitoring_enabled(installation_id, repository_id):
            return False
        log.info("repository_monitoring_paused")
        return True

    def _push(self, event: PushEvent, delivery: WebhookDelivery) -> WebhookResult:
        context = event.context
        ref = context.ref or ""
        if not ref.startswith("refs/heads/"):
            return WebhookResult(202, {"status": "ignored"})  # tags are not scanned
        if context.ref_deleted or context.after_sha is None:
            # Nothing to scan; violations seen only on this branch are no longer present.
            self.recorder.branch_deleted(event.installation_id, event.repository.id, ref)
            return WebhookResult(202, {"status": "ignored"})
        if self._monitoring_paused(event.installation_id, event.repository.id):
            return WebhookResult(202, {"status": "ignored"})
        return self._enqueue(
            NewScanJob(
                job_key=fingerprint(["push", ref, context.before_sha or "", context.after_sha]),
                installation_id=event.installation_id,
                repository=event.repository,
                delivery_id=delivery.delivery_id,
                event="push",
                group_key=branch_group_key(ref),
                head_sha=context.after_sha,
                check_name=APP_PUSH_CHECK_NAME,
                pull_request_number=None,
                context=context,
            )
        )

    def _pull_request(self, event: PullRequestEvent, delivery: WebhookDelivery) -> WebhookResult:
        action = disposition(event)
        if action is PullRequestDisposition.IGNORE:
            return WebhookResult(202, {"status": "ignored"})
        if action is PullRequestDisposition.CLOSE:
            self.store.cancel_queued_group(
                event.installation_id, event.repository.id, group_key(event.number), self._now()
            )
            self.recorder.pull_request_closed(
                event.installation_id,
                event.repository.id,
                event.number,
                merged=False,
                base_ref=None,
            )
            return WebhookResult(200, {"status": "processed"})
        if action is PullRequestDisposition.RECORD_MERGE:
            self.recorder.pull_request_closed(
                event.installation_id,
                event.repository.id,
                event.number,
                merged=True,
                base_ref=event.context.ref,
            )
            self.audit.record(
                AuditEventType.PULL_REQUEST_MERGED,
                installation_id=event.installation_id,
                repository_id=event.repository.id,
                repository=event.repository.full_name,
                head_sha=event.context.head_sha,
                pull_request=event.number,
            )
            return WebhookResult(200, {"status": "processed"})
        if self._monitoring_paused(event.installation_id, event.repository.id):
            return WebhookResult(202, {"status": "ignored"})
        context = event.context
        head = context.head_sha or ""
        result = self._enqueue(
            NewScanJob(
                job_key=fingerprint(
                    ["pull_request", str(event.number), context.base_sha or "", head]
                ),
                installation_id=event.installation_id,
                repository=event.repository,
                delivery_id=delivery.delivery_id,
                event="pull_request",
                group_key=group_key(event.number),
                head_sha=head,
                check_name=APP_CHECK_NAME,
                pull_request_number=event.number,
                context=context,
            )
        )
        if event.action == "reopened" and result.body.get("status") == "duplicate":
            # Same commits as an earlier completed scan: nothing is re-scanned, so the
            # violations that closing the pull request ended are present again.
            self.recorder.pull_request_reopened(
                event.installation_id, event.repository.id, event.number
            )
        return result

    # ------------------------------------------------------------------ #
    # Merge queue
    # ------------------------------------------------------------------ #
    def _merge_group(self, event: MergeGroupEvent, delivery: WebhookDelivery) -> WebhookResult:
        """Validate the exact merge group commit the merge queue waits on.

        The pull request's own check is not reused: a merge group combines the pull
        request with the latest base branch and the changes queued ahead of it, so it
        is scanned as ``base_sha..head_sha`` and the result is published to
        ``head_sha``. Every merge group SHA has its own scan; a recreated group is a
        new SHA and gets a new scan.
        """
        common: dict[str, Any] = {
            "installation_id": event.installation_id,
            "repository_id": event.repository.id,
            "head_sha": event.head_sha,
            "head_ref": event.head_ref,
            "base_sha": event.base_sha,
            "base_ref": event.base_ref,
            "pull_requests": event.pull_requests,
        }
        prs = ",".join(f"#{n}" for n in event.pull_requests) or None
        if event.action is MergeGroupAction.DESTROYED:
            record, changed = self.store.destroy_merge_group(
                **common, reason=event.reason, now=self._now()
            )
            if not changed:
                return WebhookResult(202, {"status": "duplicate"})
            self.store.cancel_queued_group(
                event.installation_id,
                event.repository.id,
                merge_group_key(event.head_sha),
                self._now(),
            )
            self.recorder.merge_group_destroyed(
                event.installation_id,
                event.repository.id,
                event.head_sha,
                reason=event.reason,
                base_ref=event.base_ref,
            )
            self.audit.record(
                AuditEventType.MERGE_GROUP_DESTROYED,
                installation_id=event.installation_id,
                repository_id=event.repository.id,
                repository=event.repository.full_name,
                head_sha=event.head_sha,
                reason=event.reason,
                pull_requests=prs,
                job=record.job_id,
            )
            return WebhookResult(200, {"status": "processed"})

        if self._monitoring_paused(event.installation_id, event.repository.id):
            return WebhookResult(202, {"status": "ignored"})
        record, requested = self.store.record_merge_group(
            **common, delivery_id=delivery.delivery_id, now=self._now()
        )
        if not requested:
            reason = (
                "merge group already destroyed"
                if record.state is MergeGroupState.DESTROYED
                else "merge group already requested"
            )
            log.info("merge_group_not_scanned", reason=reason)
            return WebhookResult(202, {"status": "duplicate"})
        self.audit.record(
            AuditEventType.MERGE_GROUP_CREATED,
            installation_id=event.installation_id,
            repository_id=event.repository.id,
            repository=event.repository.full_name,
            head_sha=event.head_sha,
            base_sha=event.base_sha,
            base_ref=event.base_ref,
            pull_requests=prs,
        )
        result = self._enqueue(
            NewScanJob(
                job_key=fingerprint(
                    ["merge_group", event.head_ref, event.base_sha, event.head_sha]
                ),
                installation_id=event.installation_id,
                repository=event.repository,
                delivery_id=delivery.delivery_id,
                event="merge_group",
                group_key=merge_group_key(event.head_sha),
                head_sha=event.head_sha,
                check_name=APP_CHECK_NAME,  # the required check, on the merge group commit
                pull_request_number=None,  # queued pull requests are listed on the merge group
                context=event.context,
            )
        )
        job = self.store.latest_group_job(
            event.installation_id, event.repository.id, merge_group_key(event.head_sha)
        )
        if job is not None:
            self.store.set_merge_group_job(
                event.installation_id, event.repository.id, event.head_sha, job.job_id, self._now()
            )
        return result

    # ------------------------------------------------------------------ #
    # Check re-runs
    # ------------------------------------------------------------------ #
    def _reject_rerun(
        self, event: CheckRunRerequestedEvent | CheckSuiteRerequestedEvent, reason: str
    ) -> WebhookResult:
        self.audit.record(
            AuditEventType.CHECK_RERUN_REJECTED,
            installation_id=event.installation_id,
            repository_id=event.repository.id,
            repository=event.repository.full_name,
            head_sha=event.head_sha,
            reason=reason,
        )
        log.info("check_rerun_rejected", reason=reason)
        return WebhookResult(202, {"status": "ignored"})

    def _check_run_rerequested(
        self, event: CheckRunRerequestedEvent, delivery: WebhookDelivery
    ) -> WebhookResult:
        """GitHub "Re-run" on a CommitGuard check run: a new execution of the same scan.

        The stored scan is found through the check run's ``external_id`` (the job ID
        CommitGuard set when it created the run) and must match the event's
        installation, repository, commit and check name exactly; nothing else in the
        payload is used.
        """
        if event.app_id != self._credentials.app_id:
            return WebhookResult(202, {"status": "ignored"})  # another App's check
        job = self.store.get_job(event.external_id) if event.external_id else None
        if (
            job is None
            or job.installation_id != event.installation_id
            or job.repository.id != event.repository.id
            or job.head_sha != event.head_sha
            or job.check_name != event.name
        ):
            return self._reject_rerun(event, "the check run does not match a CommitGuard scan")
        return self._request_rerun(event, job, delivery)

    def _check_suite_rerequested(
        self, event: CheckSuiteRerequestedEvent, delivery: WebhookDelivery
    ) -> WebhookResult:
        """GitHub "Re-run all checks": re-run CommitGuard's newest scan per check on the commit."""
        if event.app_id != self._credentials.app_id:
            return WebhookResult(202, {"status": "ignored"})
        jobs = [
            j
            for j in self.store.latest_jobs_for_commit(
                event.installation_id, event.repository.id, event.head_sha
            )
            if j.check_name in APP_CHECK_NAMES
        ]
        if not jobs:
            return self._reject_rerun(event, "no CommitGuard scan exists for this commit")
        results = [self._request_rerun(event, job, delivery) for job in jobs]
        statuses = {r.body.get("status") for r in results}
        status = "queued" if "queued" in statuses else sorted(str(x) for x in statuses)[0]
        return WebhookResult(202, {"status": status})

    def _request_rerun(
        self,
        event: CheckRunRerequestedEvent | CheckSuiteRerequestedEvent,
        job: ScanJob,
        delivery: WebhookDelivery,
    ) -> WebhookResult:
        if self._monitoring_paused(job.installation_id, job.repository.id):
            return self._reject_rerun(event, "monitoring is paused for this repository")
        latest = self.store.latest_group_job(job.installation_id, job.repository.id, job.group_key)
        if latest is not None and latest.scan_key != job.scan_key:
            # Re-running an outdated commit's check would record old commits as the
            # current state of the pull request or branch.
            return self._reject_rerun(
                event, "a newer commit has been scanned for this pull request or branch"
            )
        if job.event == "merge_group":
            group = self.store.get_merge_group(job.installation_id, job.repository.id, job.head_sha)
            if group is None or group.state is MergeGroupState.DESTROYED:
                return self._reject_rerun(event, "the merge group no longer exists")
        execution, created = self.store.create_execution(
            job, trigger=ScanTrigger.RERUN, now=self._now(), delivery_id=delivery.delivery_id
        )
        if not created:
            self.metrics.increment(WEBHOOKS_DUPLICATE, reason="rerun")
            return WebhookResult(202, {"status": "duplicate"})
        self.metrics.increment(CHECK_RERUNS)
        self.audit.record(
            AuditEventType.CHECK_RERUN_REQUESTED,
            actor=GITHUB_ACTOR,
            installation_id=job.installation_id,
            repository_id=job.repository.id,
            repository=job.repository.full_name,
            head_sha=job.head_sha,
            job=execution.job_id,
            previous_scan=job.job_id,
            execution=execution.execution,
            check=job.check_name,
        )
        self.queue.put(execution.job_id)
        return WebhookResult(202, {"status": "queued"})

    def _enqueue(self, new_job: NewScanJob) -> WebhookResult:
        job, created = self.store.create_job(new_job, self._now())
        if not created:
            self.metrics.increment(WEBHOOKS_DUPLICATE, reason="scan")
            log.info("scan_already_known", job_id=job.job_id, state=job.state.value)
            return WebhookResult(202, {"status": "duplicate"})
        self.metrics.increment(SCANS_QUEUED)
        self.audit.record(
            AuditEventType.SCAN_QUEUED,
            installation_id=job.installation_id,
            repository_id=job.repository.id,
            repository=job.repository.full_name,
            head_sha=job.head_sha,
            job=job.job_id,
        )
        self.queue.put(job.job_id)  # if full, recovery picks the stored job up later
        return WebhookResult(202, {"status": "queued"})

    # ------------------------------------------------------------------ #
    # Workers and maintenance
    # ------------------------------------------------------------------ #
    def process_pending(self, max_jobs: int | None = None) -> int:
        """Process queued jobs synchronously on the calling thread (tests, one-off runs)."""
        processed = 0
        while max_jobs is None or processed < max_jobs:
            job_id = self.queue.get(timeout=0)
            if job_id is None:
                break
            if self.worker.process(job_id) is not None:
                processed += 1
        return processed

    def recover(self, *, queued_before: datetime | None = None) -> int:
        """Re-enqueue stored jobs that were queued long ago or abandoned by a crash."""
        now = self._now()
        ids = self.store.recoverable_jobs(now, queued_before=queued_before or now)
        for job_id in ids:
            self.queue.put(job_id)
        return len(ids)

    def add_maintenance_task(self, task: Callable[[], object]) -> None:
        """Run ``task`` with the hourly retention purge (e.g. expired dashboard sessions)."""
        self._maintenance_tasks.append(task)

    def purge_expired(self) -> dict[str, int]:
        cutoff = self._now() - self._retention
        counts = self.store.purge_expired(cutoff)
        counts["mirrors"] = self.mirrors.purge_unused(self._retention.total_seconds())
        counts["notifications"] = self.notifications.purge_expired()
        for task in self._maintenance_tasks:
            task()
        log.info("retention_purge", **counts)
        return counts

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            job_id = self.queue.get(timeout=1.0)
            if job_id is None:
                continue
            try:
                self.worker.process(job_id)
            except Exception as exc:  # noqa: BLE001 - keep the worker alive
                log.error("worker_crashed_on_job", error_type=type(exc).__name__)

    def _notification_loop(self) -> None:
        while not self._stop.wait(RUN_INTERVAL_SECONDS):
            try:
                self.notifications.run_once()
            except Exception as exc:  # noqa: BLE001 - keep notifications alive
                log.error("notifications_failed", error_type=type(exc).__name__)

    def _maintenance_loop(self) -> None:
        last_purge = 0.0
        while not self._stop.wait(RECOVERY_INTERVAL_SECONDS):
            try:
                self.recover(queued_before=self._now() - RECOVER_QUEUED_AFTER)
                self.recovery.run_once()
                if time.monotonic() - last_purge > RETENTION_INTERVAL_SECONDS:
                    self.purge_expired()
                    last_purge = time.monotonic()
            except Exception as exc:  # noqa: BLE001 - keep maintenance alive
                log.error("maintenance_failed", error_type=type(exc).__name__)

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        self.recover()
        for index in range(self._workers):
            thread = threading.Thread(
                target=self._worker_loop, name=f"commitguard-worker-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)
        maintenance = threading.Thread(
            target=self._maintenance_loop, name="commitguard-maintenance", daemon=True
        )
        maintenance.start()
        self._threads.append(maintenance)
        notifications = threading.Thread(
            target=self._notification_loop, name="commitguard-notifications", daemon=True
        )
        notifications.start()
        self._threads.append(notifications)
        log.info("service_started", workers=self._workers)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout)
        self._threads.clear()

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #
    def readiness(self) -> tuple[bool, dict[str, str]]:
        checks = {
            "configuration": "ok",  # credentials were parsed at construction
            "store": "ok" if self.store.ping() else "unavailable",
            "workers": "ok"
            if self._threads and all(t.is_alive() for t in self._threads)
            else "not running",
            "queue": "ok" if self.queue.size() < DEFAULT_QUEUE_SIZE * 0.9 else "saturated",
        }
        return all(v == "ok" for v in checks.values()), checks


# --------------------------------------------------------------------------- #
# WSGI
# --------------------------------------------------------------------------- #
_SECURITY_HEADERS = [
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Content-Security-Policy", "default-src 'none'"),
]


def _json_response(
    start_response: StartResponse,
    status: int,
    body: Mapping[str, object],
    extra_headers: Iterable[tuple[str, str]] = (),
) -> list[bytes]:
    payload = json.dumps(body, ensure_ascii=True, sort_keys=True).encode("ascii")
    reason = {
        200: "OK",
        202: "Accepted",
        400: "Bad Request",
        401: "Unauthorized",
        404: "Not Found",
        405: "Method Not Allowed",
        409: "Conflict",
        411: "Length Required",
        413: "Payload Too Large",
        415: "Unsupported Media Type",
        429: "Too Many Requests",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }.get(status, "Error")
    headers = [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(payload))),
        *_SECURITY_HEADERS,
        *extra_headers,
    ]
    start_response(f"{status} {reason}", headers)
    return [payload]


def _read_body(environ: WSGIEnvironment, length: int) -> bytes | None:
    stream = environ["wsgi.input"]
    chunks: list[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = stream.read(min(remaining, 65536))
        if not chunk:
            return None  # client sent less than Content-Length
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def create_wsgi_app(
    service: GitHubAppService, *, webhook_path: str = WEBHOOK_PATH
) -> Callable[[WSGIEnvironment, StartResponse], Iterable[bytes]]:
    def application(environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "")
        try:
            if path == "/health":
                if method not in ("GET", "HEAD"):
                    return _json_response(start_response, 405, {"error": "method not allowed"})
                return _json_response(start_response, 200, {"status": "ok"})
            if path == "/ready":
                if method not in ("GET", "HEAD"):
                    return _json_response(start_response, 405, {"error": "method not allowed"})
                ready, checks = service.readiness()
                return _json_response(
                    start_response,
                    200 if ready else 503,
                    {"status": "ready" if ready else "not_ready", "checks": checks},
                )
            if path != webhook_path:
                return _json_response(start_response, 404, {"error": "not found"})
            if method != "POST":
                return _json_response(
                    start_response, 405, {"error": "method not allowed"}, [("Allow", "POST")]
                )
            content_type = environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                return _json_response(start_response, 415, {"error": "expected application/json"})
            raw_length = environ.get("CONTENT_LENGTH", "")
            if not raw_length or not raw_length.isascii() or not raw_length.isdigit():
                return _json_response(start_response, 411, {"error": "content length required"})
            length = int(raw_length)
            if length > MAX_WEBHOOK_BYTES:
                return _json_response(start_response, 413, {"error": "payload too large"})
            body = _read_body(environ, length)
            if body is None:
                return _json_response(start_response, 400, {"error": "incomplete body"})
            headers = {
                key[5:].replace("_", "-").lower(): value
                for key, value in environ.items()
                if key.startswith("HTTP_") and isinstance(value, str)
            }
            result = service.handle_webhook(headers, body, remote_addr=environ.get("REMOTE_ADDR"))
            return _json_response(start_response, result.status, result.body)
        except Exception as exc:  # noqa: BLE001 - never leak internals to the client
            log.error("http_internal_error", error_type=type(exc).__name__)
            return _json_response(start_response, 500, {"error": "internal error"})

    return application


def wsgi_app_from_environment() -> Callable[[WSGIEnvironment, StartResponse], Iterable[bytes]]:
    """Entry point for WSGI servers: settings from the environment, workers started.

    With ``COMMITGUARD_DASHBOARD_URL`` set, the dashboard API (and, with
    ``COMMITGUARD_DASHBOARD_STATIC_DIR``, the dashboard) is served too.

    Example (one process, several threads, behind a TLS reverse proxy)::

        gunicorn --workers 1 --threads 8 \\
            'commitguard.github.app:wsgi_app_from_environment()'
    """
    from commitguard.api.hosting import build_dashboard, create_server_app
    from commitguard.api.settings import dashboard_enabled, load_dashboard_settings
    from commitguard.notifications.settings import load_notification_settings

    configure_json_logging()
    service = GitHubAppService.from_settings(
        load_settings(), notification_settings=load_notification_settings()
    )
    dashboard = build_dashboard(service, load_dashboard_settings()) if dashboard_enabled() else None
    service.start()
    return create_server_app(service, dashboard)
