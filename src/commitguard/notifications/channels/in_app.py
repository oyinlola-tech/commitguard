"""In-app channel: who receives a notification in their CommitGuard inbox.

Recipients are computed when the event is dispatched, from the organization's
current members:

* the member's role must grant the type's permission (for example
  ``github:manage`` for installation disconnects), and the owner of a personal
  (user-account) installation is always included;
* the type must be enabled for the member (mandatory types always are).

Repository visibility on GitHub is per session (reported by GitHub at
sign-in), so it is applied when notifications are *read*: a notification about
a repository is only listed for a session that can see that repository, and the
role check is applied again at read time. A member who lost their role or
their GitHub access stops seeing notifications immediately.
"""

import sqlite3

from commitguard.controlplane.access import Role
from commitguard.notifications.models import NotificationType, TypeDefinition
from commitguard.notifications.preferences import (
    OrganizationSettings,
    UserPreferences,
    in_app_enabled,
)


def eligible_members(
    db: sqlite3.Connection,
    account_id: int,
    definition: TypeDefinition,
) -> set[int]:
    users = {
        int(row["user_id"])
        for row in db.execute(
            "SELECT user_id, role FROM memberships WHERE account_id = ?", (int(account_id),)
        ).fetchall()
        if row["role"] in {r.value for r in Role}
        and definition.permission in Role(row["role"]).permissions
    }
    personal = db.execute(
        "SELECT 1 FROM installations WHERE account_id = ? AND account_type = 'User' LIMIT 1",
        (int(account_id),),
    ).fetchone()
    if personal is not None:
        users.add(int(account_id))  # a user account's GitHub user ID is its account ID
    return users


def recipients(
    db: sqlite3.Connection,
    account_id: int,
    definition: TypeDefinition,
    organization: OrganizationSettings,
) -> list[int]:
    muted = {
        int(row["user_id"])
        for row in db.execute(
            "SELECT user_id FROM notification_user_preferences WHERE account_id = ? "
            "AND type = ? AND in_app = 0",
            (int(account_id), definition.type.value),
        ).fetchall()
    }
    result = []
    for user_id in sorted(eligible_members(db, account_id, definition)):
        preferences = UserPreferences(
            user_id,
            account_id,
            frozenset({definition.type}) if user_id in muted else frozenset[NotificationType](),
        )
        if in_app_enabled(definition, organization, preferences):
            result.append(user_id)
    return result
