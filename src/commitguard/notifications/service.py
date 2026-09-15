"""NotificationService: the notification subsystem, wired from settings.

::

    domain services ── emit() ──> outbox ──> NotificationDispatcher ──> inbox rows
                                                        └──────────> delivery records
                                                                          │
                                                   DeliveryWorker (retries) ▼
                                                        e-mail (SMTP) / signed webhook

The GitHub App service runs :meth:`run_once` on a background thread every few
seconds; tests call it directly. Nothing here is on the webhook request path
or the scan path: a slow or unavailable provider delays only notifications.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from commitguard.github.storage import SqliteStateStore
from commitguard.notifications.channels.email import EmailProvider, SmtpEmailProvider
from commitguard.notifications.channels.sink import (
    RecordingEmailProvider,
    RecordingWebhookTransport,
)
from commitguard.notifications.channels.webhook import (
    PinnedHttpsTransport,
    WebhookProvider,
    WebhookTransport,
)
from commitguard.notifications.dispatcher import NotificationDispatcher
from commitguard.notifications.retry import DeliveryWorker
from commitguard.notifications.settings import NotificationMode, NotificationSettings
from commitguard.observability.logging import get_logger
from commitguard.observability.metrics import Metrics
from commitguard.services.audit import AuditService

log = get_logger(__name__)

RUN_INTERVAL_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class RunResult:
    dispatched: int
    attempted: int


class NotificationService:
    def __init__(
        self,
        store: SqliteStateStore,
        audit: AuditService,
        metrics: Metrics,
        settings: NotificationSettings,
        *,
        email: EmailProvider | None = None,
        webhook_transport: WebhookTransport | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self._store = store
        self._now = now
        if settings.mode is NotificationMode.TEST:
            email = email or RecordingEmailProvider()
            webhook_transport = webhook_transport or RecordingWebhookTransport()
        elif settings.mode is NotificationMode.DELIVER:
            if email is None and settings.smtp is not None:
                email = SmtpEmailProvider(
                    host=settings.smtp.host,
                    port=settings.smtp.port,
                    sender=settings.smtp.sender,
                    security=settings.smtp.security,  # type: ignore[arg-type]
                    username=settings.smtp.username,
                    password=settings.smtp.password,
                )
            if webhook_transport is None and settings.signing_key is not None:
                webhook_transport = PinnedHttpsTransport(allow_private=not settings.production)
        else:
            email, webhook_transport = None, None
        self.email = email if settings.email_available else None
        self.webhook_transport = webhook_transport
        self.webhook = (
            WebhookProvider(settings.signing_key, webhook_transport)
            if settings.webhook_available
            and settings.signing_key is not None
            and webhook_transport is not None
            else None
        )
        self.dispatcher = NotificationDispatcher(store, audit, metrics, settings, now=now)
        self.worker = DeliveryWorker(
            store,
            audit,
            metrics,
            email=self.email,
            webhook=self.webhook,
            dashboard_origin=settings.dashboard_origin,
            now=now,
        )

    def run_once(self) -> RunResult:
        """Fan out pending events, then attempt due deliveries."""
        dispatched = self.dispatcher.dispatch_pending()
        attempted = self.worker.deliver_due()
        return RunResult(dispatched, attempted)

    def purge_expired(self) -> int:
        """Delete notification history older than the notification retention period.

        Events with a delivery still pending are kept; deleting an event removes
        its inbox entries and delivery records (foreign keys cascade). Audit
        events about notifications follow the audit retention instead.
        """
        cutoff = (self._now() - self.settings.retention).timestamp()
        with self._store.transaction() as db:
            removed = db.execute(
                "DELETE FROM notification_events WHERE last_occurred_at < ? AND dispatched_at "
                "IS NOT NULL AND NOT EXISTS (SELECT 1 FROM notification_deliveries d WHERE "
                "d.event_id = notification_events.event_id AND d.status = 'pending')",
                (cutoff,),
            ).rowcount
            db.execute(
                "DELETE FROM notification_webhooks WHERE removed_at IS NOT NULL AND removed_at < ?",
                (cutoff,),
            )
        if removed:
            log.info("notification_retention_purge", events=removed)
        return removed
