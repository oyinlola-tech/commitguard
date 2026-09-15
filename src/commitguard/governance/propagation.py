"""Effective policy cache invalidation.

Every repository has a row in ``repository_effective_policies`` describing its
resolved governance inputs (policy layers, exceptions, mode) and a state:

==============  ================================================================
``up_to_date``  resolved from the current governance data
``stale``       governance data changed; the next resolution recomputes it
``syncing``     a background propagation run is recomputing it
``error``       recomputation failed; the repository is reported ``SYNC_ERROR``
                and administrators are notified. Scans never use an errored or
                stale row: they resolve from the database directly.
==============  ================================================================

Invalidation is **targeted** and **transactional**: the service that changes
governance data marks exactly the affected repositories stale inside the same
database transaction as the change, so no process can read an ``up_to_date``
row that predates the change.

=================================  ====================================
Change                             Repositories marked stale
=================================  ====================================
organization policy, baseline,     all repositories of the organization
settings, organization exception
group policy, group exception,     the group's members
group archival
group membership change            the added or removed repositories
repository policy, repository      that repository
exception, onboarding mode
staged rollout stage change        the repositories enrolled or released
=================================  ====================================

A row can also expire on its own: ``valid_until`` is the earliest expiry of an
exception it applied, so an exception stops applying at its expiry time even
before the expiration worker runs.
"""

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime

from commitguard.governance.common import chunks

STATES = ("up_to_date", "stale", "syncing", "error")


def invalidate(
    db: sqlite3.Connection,
    account_id: int,
    now: datetime,
    repository_ids: Iterable[int] | None = None,
) -> int:
    """Mark effective policies stale inside the caller's transaction.

    ``repository_ids=None`` invalidates every repository of the account. Rows
    that do not exist yet are created stale so the propagation worker computes
    them. Returns the number of repositories marked.
    """
    if repository_ids is None:
        ids = [
            int(row["repository_id"])
            for row in db.execute(
                "SELECT DISTINCT k.repository_id FROM known_repositories k JOIN installations i "
                "ON i.installation_id = k.installation_id WHERE i.account_id = ? "
                "AND i.state != 'deleted'",
                (int(account_id),),
            ).fetchall()
        ]
    else:
        ids = sorted({int(r) for r in repository_ids})
    for chunk in chunks(ids, 500):
        db.execute(
            "INSERT INTO repository_effective_policies (account_id, repository_id, state, "
            "invalidated_at) SELECT ?, value, 'stale', ? FROM json_each(?) WHERE true "
            "ON CONFLICT (account_id, repository_id) DO UPDATE SET state = 'stale', "
            "invalidated_at = excluded.invalidated_at, error = NULL, attempts = 0",
            (int(account_id), now.timestamp(), json.dumps(list(chunk))),
        )
    return len(ids)


def group_member_ids(db: sqlite3.Connection, account_id: int, group_id: str) -> list[int]:
    return [
        int(row["repository_id"])
        for row in db.execute(
            "SELECT repository_id FROM repository_group_members WHERE account_id = ? "
            "AND group_id = ?",
            (int(account_id), group_id),
        ).fetchall()
    ]
