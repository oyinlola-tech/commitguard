"""Outbox dispatcher: notification events -> inbox entries and delivery records.

Each pending event is claimed with a conditional update (``dispatched_at IS
NULL``) inside the same transaction that writes its in-app notifications,
e-mail and webhook delivery records and the ``notification_created`` audit
event. Several processes can run the dispatcher: only one claims an event, and
a crash before commit leaves the event pending for the next run.
"""

import json
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from commitguard.audit.models import AuditEventType
from commitguard.core.result import Severity
from commitguard.github.storage import SqliteStateStore
from commitguard.notifications.channels import in_app
from commitguard.notifications.deduplication import delivery_idempotency_key
from commitguard.notifications.models import (
    DEFINITIONS,
    DeliveryStatus,
    NotificationChannel,
    NotificationEvent,
    NotificationType,
    StoredNotificationEvent,
)
from commitguard.notifications.preferences import load_organization_settings
from commitguard.notifications.settings import NotificationSettings
from commitguard.observability.logging import correlation, get_logger
from commitguard.observability.metrics import NOTIFICATIONS_CREATED, Metrics
from commitguard.services.audit import AuditService

log = get_logger(__name__)

DISPATCH_BATCH = 100


def _dt(value: float | None) -> datetime | None:
    return None if value is None else datetime.fromtimestamp(float(value), UTC)


def stored_event(row: sqlite3.Row) -> StoredNotificationEvent:
    event = NotificationEvent.model_construct(
        event_id=row["event_id"],
        type=NotificationType(row["type"]),
        account_id=int(row["account_id"]),
        severity=Severity(row["severity"]),
        installation_id=row["installation_id"],
        repository_id=row["repository_id"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        dedup_key=row["dedup_key"],
        title=row["title"],
        body=row["body"],
        metadata=json.loads(row["metadata"] or "{}"),
    )
    return StoredNotificationEvent(
        event=event,
        occurrences=int(row["occurrences"]),
        created_at=datetime.fromtimestamp(float(row["created_at"]), UTC),
        last_occurred_at=datetime.fromtimestamp(float(row["last_occurred_at"]), UTC),
        dispatched_at=_dt(row["dispatched_at"]),
        request_id=row["request_id"],
        delivery_id=row["delivery_id"],
        job_id=row["job_id"],
    )


class NotificationDispatcher:
    def __init__(
        self,
        store: SqliteStateStore,
        audit: AuditService,
        metrics: Metrics,
        settings: NotificationSettings,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._audit = audit
        self._metrics = metrics
        self._settings = settings
        self._now = now

    def dispatch_pending(self, limit: int = DISPATCH_BATCH) -> int:
        rows = self._store.query(
            "SELECT event_id FROM notification_events WHERE dispatched_at IS NULL "
            "ORDER BY created_at LIMIT ?",
            (int(limit),),
        )
        dispatched = 0
        for row in rows:
            with correlation(notification_event_id=row["event_id"]):
                if self._dispatch(str(row["event_id"])):
                    dispatched += 1
        return dispatched

    def _dispatch(self, event_id: str) -> bool:
        now = self._now()
        audit_event = None
        with self._store.transaction() as db:
            claimed = db.execute(
                "UPDATE notification_events SET dispatched_at = ? WHERE event_id = ? "
                "AND dispatched_at IS NULL",
                (now.timestamp(), event_id),
            ).rowcount
            if claimed != 1:
                return False
            row = db.execute(
                "SELECT * FROM notification_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            stored = stored_event(row)
            event = stored.event
            definition = DEFINITIONS[event.type]
            organization = load_organization_settings(db, event.account_id)
            users = in_app.recipients(db, event.account_id, definition, organization)
            db.executemany(
                "INSERT OR IGNORE INTO notifications (notification_id, event_id, account_id, "
                "user_id, state, severity, created_at, updated_at, sort_at) "
                "VALUES (?, ?, ?, ?, 'unread', ?, ?, ?, ?)",
                [
                    (
                        uuid.uuid4().hex,
                        event_id,
                        event.account_id,
                        user,
                        event.severity.value,
                        now.timestamp(),
                        now.timestamp(),
                        stored.last_occurred_at.timestamp(),
                    )
                    for user in users
                ],
            )
            channel = organization.types[event.type]
            deliveries: list[tuple[str, str, str]] = []  # channel, destination, provider
            if channel.email and self._settings.email_available:
                deliveries += [
                    (NotificationChannel.EMAIL.value, address, "email")
                    for address in organization.email_recipients
                ]
            if channel.webhook and self._settings.webhook_available:
                deliveries += [
                    (NotificationChannel.WEBHOOK.value, str(endpoint["endpoint_id"]), "webhook")
                    for endpoint in db.execute(
                        "SELECT endpoint_id FROM notification_webhooks WHERE account_id = ? "
                        "AND removed_at IS NULL ORDER BY created_at",
                        (event.account_id,),
                    ).fetchall()
                ]
            db.executemany(
                "INSERT OR IGNORE INTO notification_deliveries (delivery_id, event_id, account_id, "
                "channel, destination, idempotency_key, status, attempt_count, provider, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                [
                    (
                        uuid.uuid4().hex,
                        event_id,
                        event.account_id,
                        channel_name,
                        destination,
                        delivery_idempotency_key(event_id, channel_name, destination),
                        DeliveryStatus.PENDING.value,
                        provider,
                        now.timestamp(),
                        now.timestamp(),
                    )
                    for channel_name, destination, provider in deliveries
                ],
            )
            audit_event = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.NOTIFICATION_CREATED,
                    account_id=event.account_id,
                    installation_id=event.installation_id,
                    repository_id=event.repository_id,
                    notification_type=event.type.value,
                    notification_event=event_id,
                    severity=event.severity.value,
                    recipients=len(users),
                    email_deliveries=sum(1 for d in deliveries if d[0] == "email"),
                    webhook_deliveries=sum(1 for d in deliveries if d[0] == "webhook"),
                    source_request=stored.request_id,
                    source_delivery=stored.delivery_id,
                    source_job=stored.job_id,
                ),
            )
        self._audit.log_stored(audit_event)
        self._metrics.increment(NOTIFICATIONS_CREATED, type=event.type.value)
        log.info(
            "notification_dispatched",
            type=event.type.value,
            recipients=len(users),
            deliveries=len(deliveries),
        )
        return True
