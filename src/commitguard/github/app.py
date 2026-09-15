"""The CommitGuard GitHub App service.

::

    GitHub --HTTPS webhook--> WSGI endpoint (POST /webhooks/github)
             rate limit -> size/content-type -> signature -> headers -> JSON
             -> normalise -> delivery-ID dedup -> installation events: apply
                                              -> push / pull_request: store job, enqueue
             <- 2xx within milliseconds (no scanning on the request path)

    worker threads: queue -> ScanWorker -> Check Run
    maintenance:    re-enqueue abandoned jobs, retention purge

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

from commitguard.audit.models import AuditEventType
from commitguard.config.sources import MandatoryPolicy, load_mandatory_policy
from commitguard.github.auth import AppCredentials, InstallationTokenProvider
from commitguard.github.checks import APP_CHECK_NAME, APP_PUSH_CHECK_NAME
from commitguard.github.client import API_URL, GitHubClient, Transport
from commitguard.github.errors import WebhookValidationError
from commitguard.github.events import (
    GitHubWebhookEvent,
    IgnoredEvent,
    InstallationEvent,
    InstallationRepositoriesEvent,
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
)
from commitguard.github.queue import DEFAULT_QUEUE_SIZE, EventQueue, InProcessEventQueue
from commitguard.github.repositories import GitHubRemoteLocator, MirrorManager, RemoteLocator
from commitguard.github.settings import AppSettings, load_settings
from commitguard.github.storage import (
    DATABASE_FILENAME,
    DeliveryStatus,
    NewScanJob,
    SqliteStateStore,
)
from commitguard.github.webhooks import MAX_WEBHOOK_BYTES, WebhookDelivery, parse_delivery
from commitguard.github.worker import ScanWorker
from commitguard.observability.logging import configure_json_logging, correlation, get_logger
from commitguard.observability.metrics import (
    SCANS_QUEUED,
    WEBHOOKS_DUPLICATE,
    WEBHOOKS_RECEIVED,
    WEBHOOKS_REJECTED,
    InMemoryMetrics,
)
from commitguard.security.hashing import fingerprint
from commitguard.security.secrets import Secret
from commitguard.services.audit import AuditService

log = get_logger(__name__)

WEBHOOK_PATH = "/webhooks/github"
DEFAULT_RATE_LIMIT_PER_MINUTE = 600
RECOVERY_INTERVAL_SECONDS = 60.0
RECOVER_QUEUED_AFTER = timedelta(minutes=5)
RETENTION_INTERVAL_SECONDS = 3600.0


@dataclass(frozen=True, slots=True)
class WebhookResult:
    status: int
    body: Mapping[str, str | int] = field(default_factory=dict)


class RequestRateLimiter:
    """Fixed-window request counter per client address (bounded memory)."""

    MAX_TRACKED = 10_000

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = per_minute
        self._clock = clock
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, int]] = {}

    def allow(self, key: str) -> bool:
        window = int(self._clock() // 60)
        with self._lock:
            if len(self._windows) > self.MAX_TRACKED:
                self._windows.clear()
            start, count = self._windows.get(key, (window, 0))
            if start != window:
                start, count = window, 0
            count += 1
            self._windows[key] = (start, count)
            return count <= self._limit


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
        self.worker = ScanWorker(
            store=store,
            installations=self.installations,
            client=client,
            mirrors=mirrors,
            audit=self.audit,
            metrics=self.metrics,
            mandatory_policy=mandatory_policy,
            max_commits=max_commits,
            now=now,
        )
        self._credentials = credentials
        self._secret = webhook_secret
        self._workers = workers
        self._retention = retention
        self._now = now
        self._limiter = RequestRateLimiter(rate_limit_per_minute)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

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
        try:
            event = normalize_webhook(delivery.event, delivery.payload)
        except WebhookValidationError as exc:
            self.metrics.increment(WEBHOOKS_REJECTED, reason="invalid_payload")
            log.warning("webhook_invalid", event_name=delivery.event, reason=str(exc))
            self.audit.record(
                AuditEventType.WEBHOOK_REJECTED, event_name=delivery.event, reason=str(exc)
            )
            return WebhookResult(exc.status, {"error": str(exc)})

        status = self.store.record_delivery(
            delivery.delivery_id, delivery.event, delivery.body_sha256, self._now()
        )
        if status is DeliveryStatus.DUPLICATE:
            self.metrics.increment(WEBHOOKS_DUPLICATE)
            log.info("webhook_duplicate", event_name=delivery.event)
            return WebhookResult(200, {"status": "duplicate"})
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
        return self._dispatch(event, delivery)

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
            return self._pull_request(event, delivery)

    def _push(self, event: PushEvent, delivery: WebhookDelivery) -> WebhookResult:
        context = event.context
        if context.ref_deleted or context.after_sha is None:
            return WebhookResult(202, {"status": "ignored"})  # nothing to scan
        if not (context.ref or "").startswith("refs/heads/"):
            return WebhookResult(202, {"status": "ignored"})  # tags are not scanned
        ref = context.ref or ""
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
            return WebhookResult(200, {"status": "processed"})
        if action is PullRequestDisposition.RECORD_MERGE:
            self.audit.record(
                AuditEventType.PULL_REQUEST_MERGED,
                installation_id=event.installation_id,
                repository_id=event.repository.id,
                repository=event.repository.full_name,
                head_sha=event.context.head_sha,
                pull_request=event.number,
            )
            return WebhookResult(200, {"status": "processed"})
        context = event.context
        head = context.head_sha or ""
        return self._enqueue(
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

    def purge_expired(self) -> dict[str, int]:
        cutoff = self._now() - self._retention
        counts = self.store.purge_expired(cutoff)
        counts["mirrors"] = self.mirrors.purge_unused(self._retention.total_seconds())
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

    def _maintenance_loop(self) -> None:
        last_purge = 0.0
        while not self._stop.wait(RECOVERY_INTERVAL_SECONDS):
            try:
                self.recover(queued_before=self._now() - RECOVER_QUEUED_AFTER)
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

    Example (one process, several threads, behind a TLS reverse proxy)::

        gunicorn --workers 1 --threads 8 \\
            'commitguard.github.app:wsgi_app_from_environment()'
    """
    configure_json_logging()
    service = GitHubAppService.from_settings(load_settings())
    service.start()
    return create_wsgi_app(service)
