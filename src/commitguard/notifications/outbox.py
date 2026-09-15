"""The notification outbox.

::

    domain transaction (scan result / policy version / installation state)
      ├── state change
      ├── audit event
      └── notification event  ── emit(db, event)      <- same transaction
                                     │
    dispatcher (background)          ▼
      notification_events WHERE dispatched_at IS NULL
      -> in-app notifications, e-mail and webhook deliveries (one transaction)

Because the event is written with the state change, a crash can never leave a
policy change or a blocked commit without its notification, and a failed
transaction never produces a notification for a change that did not happen.
Delivery is a separate, later step: a provider outage cannot undo or delay the
security decision.
"""

import json
import sqlite3
from datetime import datetime

from commitguard.notifications.deduplication import storage_key
from commitguard.notifications.models import NotificationEvent
from commitguard.observability.logging import current_correlation, get_logger

log = get_logger(__name__)


def emit(db: sqlite3.Connection, event: NotificationEvent, now: datetime) -> str:
    """Write ``event`` inside the caller's transaction. Returns the stored event ID.

    A repeat of an event with the same storage key (see
    :mod:`commitguard.notifications.deduplication`) updates the stored event and
    marks its in-app notifications unread again; it does not fan out again.
    """
    key = storage_key(event.type, event.dedup_key, now)
    correlation = current_correlation()

    def _corr(name: str) -> str | None:
        value = correlation.get(name)
        return None if value is None else str(value)[:128]

    cursor = db.execute(
        "INSERT INTO notification_events (event_id, account_id, type, severity, installation_id, "
        "repository_id, resource_type, resource_id, dedup_key, title, body, metadata, "
        "occurrences, created_at, last_occurred_at, request_id, delivery_id, job_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?) "
        "ON CONFLICT (account_id, dedup_key) DO NOTHING",
        (
            event.event_id,
            event.account_id,
            event.type.value,
            event.severity.value,
            event.installation_id,
            event.repository_id,
            event.resource_type,
            event.resource_id,
            key,
            event.title,
            event.body,
            json.dumps(event.metadata, sort_keys=True),
            now.timestamp(),
            now.timestamp(),
            _corr("request_id"),
            _corr("delivery_id"),
            _corr("job_id"),
        ),
    )
    if cursor.rowcount == 1:
        log.info("notification_event_emitted", type=event.type.value, event_id=event.event_id)
        return event.event_id
    row = db.execute(
        "SELECT event_id FROM notification_events WHERE account_id = ? AND dedup_key = ?",
        (event.account_id, key),
    ).fetchone()
    existing = str(row["event_id"])
    db.execute(
        "UPDATE notification_events SET occurrences = occurrences + 1, last_occurred_at = ?, "
        "title = ?, body = ?, metadata = ?, resource_id = ? WHERE event_id = ?",
        (
            now.timestamp(),
            event.title,
            event.body,
            json.dumps(event.metadata, sort_keys=True),
            event.resource_id,
            existing,
        ),
    )
    db.execute(
        "UPDATE notifications SET state = CASE WHEN state = 'read' THEN 'unread' ELSE state END, "
        "read_at = CASE WHEN state = 'read' THEN NULL ELSE read_at END, "
        "updated_at = ?, sort_at = ? WHERE event_id = ? AND state != 'archived'",
        (now.timestamp(), now.timestamp(), existing),
    )
    log.info("notification_event_coalesced", type=event.type.value, event_id=existing)
    return existing


def account_for_installation(db: sqlite3.Connection, installation_id: int) -> int | None:
    row = db.execute(
        "SELECT account_id FROM installations WHERE installation_id = ?", (int(installation_id),)
    ).fetchone()
    return int(row["account_id"]) if row is not None else None
