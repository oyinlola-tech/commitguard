"""Notification deduplication.

Two mechanisms keep one underlying event from producing many notifications:

1. **Idempotent emission.** Every notification event has a *storage key*: its
   domain identity (``dedup_key``) plus, for types with a coalescing window, the
   window it falls in. The outbox has a unique constraint on
   ``(account_id, storage key)``. Emitting the same key again - a replayed
   webhook, a retried job, twenty commits of one pull request detected in one
   scan - updates the existing event (``occurrences`` + 1, newest text) instead
   of inserting another one, and it creates no new e-mail or webhook deliveries.
2. **Idempotent delivery.** Each delivery has an idempotency key derived from
   the event, the channel and the destination, also unique in the database, and
   sent to the provider (``Message-ID`` for e-mail, ``X-CommitGuard-Delivery``
   for webhooks) so a retry after an ambiguous failure can be recognised.

Domain keys, per type:

=============================  ==================================================
``critical_violation``,        installation, repository, pull request / branch /
``high_violation``             merge group, rule - one notification per rule per
                               change within an hour, however many commits
``policy_changed``,            organization and the new version number
``policy_rolled_back``
``installation_disconnected``  installation (within an hour: flapping
``installation_reconnected``   suspend/unsuspend does not repeat the alert)
``merge_queue_failure``        repository and merge group commit
``check_rerun_failed``         the failed execution
=============================  ==================================================
"""

from collections.abc import Iterable
from datetime import datetime

from commitguard.notifications.models import DEFINITIONS, NotificationType
from commitguard.security.hashing import fingerprint


def domain_key(notification_type: NotificationType, *parts: str | int) -> str:
    return ":".join([notification_type.value, *(str(p) for p in parts)])


def storage_key(notification_type: NotificationType, dedup_key: str, occurred_at: datetime) -> str:
    """The outbox uniqueness key: the domain key, bucketed by the coalescing window."""
    window = DEFINITIONS[notification_type].coalesce_seconds
    if not window:
        return dedup_key
    bucket = int(occurred_at.timestamp() // window)
    return f"{dedup_key}@{bucket}"


def delivery_idempotency_key(event_id: str, channel: str, destination: str) -> str:
    return fingerprint(["notification-delivery", event_id, channel, destination])


def unique_sorted(values: Iterable[str]) -> list[str]:
    return sorted(set(values))
