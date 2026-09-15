"""Effective policy cache invalidation (targeted, transactional).

``repository_effective_policies`` holds one row per repository: the resolved
governance inputs, their fingerprint and a propagation ``state``:

==============  ===================================================================
``up_to_date``  resolved from the current policy versions, group memberships,
                exceptions and mode; valid until ``valid_until`` (the earliest
                exception expiry it includes)
``stale``       something that affects this repository changed; the next scan or
                the propagation job resolves it again
``syncing``     the propagation job is resolving it
``error``       resolving failed; scans resolve directly and fail closed if that
                fails too, and administrators are notified
==============  ===================================================================

Every governance change marks exactly the affected rows stale **in the same
transaction** as the change:

=================================  ==========================================
Change                             Affected repositories
=================================  ==========================================
organization policy, baseline,     every repository of the organization
organization-wide exception
group policy, group exception,     the group's members
group archived
group membership                   the repositories added or removed
repository policy, repository      that repository
exception, onboarding mode
=================================  ==========================================

A row is therefore never ``up_to_date`` for inputs that no longer apply, and a
scan that finds its row anything but ``up_to_date`` resolves from the database.
"""

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime


def invalidate_repositories(
    db: sqlite3.Connection,
    account_id: int,
    repository_ids: Iterable[int] | None,
    now: datetime,
) -> int:
    """Mark effective policies stale; ``None`` means every repository of the account."""
    if repository_ids is None:
        return db.execute(
            "UPDATE repository_effective_policies SET state = 'stale', invalidated_at = ? "
            "WHERE account_id = ?",
            (now.timestamp(), int(account_id)),
        ).rowcount
    ids = sorted({int(i) for i in repository_ids})
    if not ids:
        return 0
    return db.execute(
        "UPDATE repository_effective_policies SET state = 'stale', invalidated_at = ? "
        "WHERE account_id = ? AND repository_id IN (SELECT value FROM json_each(?))",
        (now.timestamp(), int(account_id), json.dumps(ids)),
    ).rowcount


def group_member_ids(db: sqlite3.Connection, account_id: int, group_id: str) -> list[int]:
    return [
        int(row["repository_id"])
        for row in db.execute(
            "SELECT repository_id FROM repository_group_members WHERE account_id = ? "
            "AND group_id = ?",
            (int(account_id), group_id),
        ).fetchall()
    ]


def invalidate_scope(
    db: sqlite3.Connection, account_id: int, scope_type: str, scope_id: str, now: datetime
) -> int:
    """Invalidate the repositories a policy target or exception scope covers."""
    if scope_type == "organization":
        return invalidate_repositories(db, account_id, None, now)
    if scope_type == "group":
        return invalidate_repositories(
            db, account_id, group_member_ids(db, account_id, scope_id), now
        )
    if scope_id.isdigit():
        return invalidate_repositories(db, account_id, [int(scope_id)], now)
    return 0
