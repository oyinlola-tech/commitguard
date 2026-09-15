"""Delivery worker: bounded retries for e-mail and webhook deliveries.

::

    pending ──claim (lease)──> attempt ──success──> sent
                                  │
                                  ├─ retryable failure, attempts < 5 ──> pending
                                  │     next_retry_at = now + 1m, 5m, 30m, 2h
                                  └─ permanent failure, or 5th attempt ──> failed

* **Bounded.** At most :data:`MAX_ATTEMPTS` attempts; there is no infinite loop.
* **One sender at a time.** A delivery is claimed with a lease
  (:data:`LEASE_SECONDS`) through a conditional update; another worker or
  process skips it until the lease expires.
* **Idempotent.** The idempotency key travels with every attempt. If a process
  dies after the provider accepted a message but before ``sent`` was stored,
  the lease expires and the message is sent again with the *same* key, which
  receivers use to discard the duplicate (at-least-once delivery).
* **Secondary to security.** Failures change only the delivery record. The
  scan result, the violation, the policy version and the in-app notification
  are already stored.
"""

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from commitguard.audit.models import AuditEventType
from commitguard.github.storage import SqliteStateStore
from commitguard.notifications.channels.base import DeliveryError, DeliveryReceipt
from commitguard.notifications.channels.email import EmailProvider
from commitguard.notifications.channels.webhook import WebhookProvider
from commitguard.notifications.dispatcher import stored_event
from commitguard.notifications.models import (
    DeliveryStatus,
    NotificationChannel,
    StoredNotificationEvent,
)
from commitguard.notifications.templates import render_email, render_webhook
from commitguard.observability.logging import correlation, get_logger
from commitguard.observability.metrics import (
    NOTIFICATION_RETRIES,
    NOTIFICATIONS_FAILED,
    NOTIFICATIONS_SENT,
    Metrics,
)
from commitguard.services.audit import AuditService

log = get_logger(__name__)

MAX_ATTEMPTS = 5
BACKOFF_SECONDS = (60, 300, 1800, 7200)
LEASE_SECONDS = 120
DELIVERY_BATCH = 50


def retry_delay(attempts_made: int) -> timedelta | None:
    """Delay before the next attempt, or None when no attempt is left."""
    if attempts_made >= MAX_ATTEMPTS:
        return None
    return timedelta(seconds=BACKOFF_SECONDS[min(attempts_made, len(BACKOFF_SECONDS)) - 1])


def mask_destination(channel: str, destination: str) -> str:
    """How a destination appears in audit events (no full e-mail addresses)."""
    if channel != NotificationChannel.EMAIL.value or "@" not in destination:
        return destination
    local, _, domain = destination.partition("@")
    return f"{local[:1]}***@{domain}"


class DeliveryWorker:
    def __init__(
        self,
        store: SqliteStateStore,
        audit: AuditService,
        metrics: Metrics,
        *,
        email: EmailProvider | None,
        webhook: WebhookProvider | None,
        dashboard_origin: str | None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._audit = audit
        self._metrics = metrics
        self._email = email
        self._webhook = webhook
        self._origin = dashboard_origin
        self._now = now

    # ------------------------------------------------------------------ #
    def deliver_due(self, limit: int = DELIVERY_BATCH) -> int:
        now = self._now().timestamp()
        rows = self._store.query(
            "SELECT delivery_id FROM notification_deliveries WHERE status = 'pending' "
            "AND (next_retry_at IS NULL OR next_retry_at <= ?) "
            "AND (lease_expires_at IS NULL OR lease_expires_at < ?) ORDER BY created_at LIMIT ?",
            (now, now, int(limit)),
        )
        attempted = 0
        for row in rows:
            if self._claim(str(row["delivery_id"])):
                self._attempt(str(row["delivery_id"]))
                attempted += 1
        return attempted

    def _claim(self, delivery_id: str) -> bool:
        now = self._now().timestamp()
        with self._store.transaction() as db:
            return (
                db.execute(
                    "UPDATE notification_deliveries SET lease_expires_at = ?, updated_at = ? "
                    "WHERE delivery_id = ? AND status = 'pending' AND (next_retry_at IS NULL OR "
                    "next_retry_at <= ?) AND (lease_expires_at IS NULL OR lease_expires_at < ?)",
                    (now + LEASE_SECONDS, now, delivery_id, now, now),
                ).rowcount
                == 1
            )

    def _attempt(self, delivery_id: str) -> None:
        rows = self._store.query(
            "SELECT d.*, e.event_id AS e_event_id, i.account_login, "
            "(SELECT owner || '/' || name FROM known_repositories k WHERE "
            "k.installation_id = e.installation_id AND k.repository_id = e.repository_id) "
            "AS repository_name, w.url AS webhook_url, w.removed_at AS webhook_removed_at "
            "FROM notification_deliveries d JOIN notification_events e ON e.event_id = d.event_id "
            "LEFT JOIN (SELECT account_id, MIN(account_login) AS account_login FROM installations "
            "GROUP BY account_id) i ON i.account_id = d.account_id "
            "LEFT JOIN notification_webhooks w ON w.endpoint_id = d.destination "
            "AND w.account_id = d.account_id WHERE d.delivery_id = ?",
            (delivery_id,),
        )
        if not rows:
            return
        row = rows[0]
        event_rows = self._store.query(
            "SELECT * FROM notification_events WHERE event_id = ?", (row["event_id"],)
        )
        stored = stored_event(event_rows[0])
        organization = row["account_login"] or f"account {row['account_id']}"
        channel = str(row["channel"])
        with correlation(notification_event_id=stored.event.event_id):
            try:
                receipt = self._send(row, stored, organization, channel)
            except DeliveryError as exc:
                self._failed(row, exc)
            except Exception as exc:  # noqa: BLE001 - a delivery bug must not stop the worker
                log.error("notification_delivery_crashed", error_type=type(exc).__name__)
                self._failed(row, DeliveryError("internal_error"))
            else:
                self._sent(row, receipt)

    def _send(
        self,
        row: sqlite3.Row,
        stored: StoredNotificationEvent,
        organization: str,
        channel: str,
    ) -> DeliveryReceipt:
        if channel == NotificationChannel.EMAIL.value:
            if self._email is None:
                raise _Cancelled("email_channel_unavailable")
            return self._email.send(
                render_email(
                    stored,
                    to=str(row["destination"]),
                    organization=organization,
                    dashboard_origin=self._origin,
                ),
                idempotency_key=str(row["idempotency_key"]),
            )
        if self._webhook is None:
            raise _Cancelled("webhook_channel_unavailable")
        if row["webhook_url"] is None or row["webhook_removed_at"] is not None:
            raise _Cancelled("webhook_endpoint_removed")
        now = self._now()
        return self._webhook.send(
            url=str(row["webhook_url"]),
            endpoint_id=str(row["destination"]),
            event_type=stored.event.type.value,
            body=render_webhook(
                stored,
                organization=organization,
                repository=row["repository_name"],
                dashboard_origin=self._origin,
                now=now,
            ),
            idempotency_key=str(row["idempotency_key"]),
            timestamp=int(now.timestamp()),
        )

    # ------------------------------------------------------------------ #
    def _sent(self, row: sqlite3.Row, receipt: DeliveryReceipt) -> None:
        now = self._now().timestamp()
        with self._store.transaction() as db:
            db.execute(
                "UPDATE notification_deliveries SET status = 'sent', attempt_count = "
                "attempt_count + 1, provider = ?, provider_message_id = ?, failure_code = NULL, "
                "last_attempt_at = ?, next_retry_at = NULL, lease_expires_at = NULL, "
                "updated_at = ? WHERE delivery_id = ?",
                (
                    receipt.provider,
                    (receipt.provider_message_id or "")[:200] or None,
                    now,
                    now,
                    row["delivery_id"],
                ),
            )
            event = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.NOTIFICATION_DELIVERED,
                    account_id=int(row["account_id"]),
                    channel=str(row["channel"]),
                    destination=mask_destination(str(row["channel"]), str(row["destination"])),
                    notification_delivery=str(row["delivery_id"]),
                    notification_event=str(row["event_id"]),
                    attempts=int(row["attempt_count"]) + 1,
                ),
            )
        self._audit.log_stored(event)
        self._metrics.increment(NOTIFICATIONS_SENT, channel=str(row["channel"]))

    def _failed(self, row: sqlite3.Row, error: DeliveryError) -> None:
        now = self._now()
        attempts = int(row["attempt_count"]) + 1
        cancelled = isinstance(error, _Cancelled)
        delay = None if (error.permanent or cancelled) else retry_delay(attempts)
        if cancelled:
            status = DeliveryStatus.CANCELLED
        elif delay is None:
            status = DeliveryStatus.FAILED
        else:
            status = DeliveryStatus.PENDING
        with self._store.transaction() as db:
            db.execute(
                "UPDATE notification_deliveries SET status = ?, attempt_count = ?, "
                "failure_code = ?, last_attempt_at = ?, next_retry_at = ?, "
                "lease_expires_at = NULL, updated_at = ? WHERE delivery_id = ?",
                (
                    status.value,
                    attempts,
                    error.code,
                    now.timestamp(),
                    (now + delay).timestamp() if delay is not None else None,
                    now.timestamp(),
                    row["delivery_id"],
                ),
            )
            event = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.NOTIFICATION_DELIVERY_FAILED,
                    account_id=int(row["account_id"]),
                    channel=str(row["channel"]),
                    destination=mask_destination(str(row["channel"]), str(row["destination"])),
                    notification_delivery=str(row["delivery_id"]),
                    notification_event=str(row["event_id"]),
                    attempts=attempts,
                    failure_code=error.code,
                    status=status.value,
                    retry_scheduled=delay is not None,
                ),
            )
        self._audit.log_stored(event)
        if status is DeliveryStatus.PENDING:
            self._metrics.increment(NOTIFICATION_RETRIES, channel=str(row["channel"]))
        else:
            self._metrics.increment(NOTIFICATIONS_FAILED, channel=str(row["channel"]))
        log.warning(
            "notification_delivery_failed",
            channel=str(row["channel"]),
            failure_code=error.code,
            attempts=attempts,
            status=status.value,
        )


class _Cancelled(DeliveryError):
    def __init__(self, code: str) -> None:
        super().__init__(code, permanent=True)
