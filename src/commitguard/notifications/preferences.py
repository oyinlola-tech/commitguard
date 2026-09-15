"""Notification preferences: organization defaults, personal choices, effective result.

::

    built-in defaults (per type)
      + organization settings   administrators (notifications:manage), versioned
      + personal preferences    each member, in-app only, for their own inbox
      = effective preferences

Ownership rules:

* **Organization settings** decide which types reach the organization's e-mail
  recipients and webhooks, and whether a non-mandatory type appears in members'
  inboxes at all. Only ``notifications:manage`` may change them; turning a
  delivery *off* needs explicit confirmation.
* **Personal preferences** only mute non-mandatory types in the member's own
  inbox. They cannot touch e-mail, webhooks or anyone else's inbox, so a viewer
  can never silence security notifications for the organization.
* **Mandatory** in-app types (see :mod:`commitguard.notifications.models`) are
  delivered to every eligible member regardless of either setting.
"""

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from commitguard.controlplane.errors import InputValidationError
from commitguard.notifications.models import (
    DEFINITIONS,
    NotificationChannel,
    NotificationType,
    TypeDefinition,
)

MAX_EMAIL_RECIPIENTS = 20
MAX_EMAIL_CHARS = 254
# Deliberately strict: a plain mailbox address, no display name, comments or quoting.
_EMAIL_RE = re.compile(
    r"\A[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9-]{1,63}(\.[A-Za-z0-9-]{1,63})+\Z"
)


@dataclass(frozen=True, slots=True)
class ChannelSetting:
    in_app: bool
    email: bool
    webhook: bool


@dataclass(frozen=True, slots=True)
class OrganizationSettings:
    account_id: int
    version: int  # 0: built-in defaults, never saved
    types: Mapping[NotificationType, ChannelSetting]
    email_recipients: tuple[str, ...]
    updated_at: datetime | None = None
    updated_by: str | None = None


@dataclass(frozen=True, slots=True)
class UserPreferences:
    user_id: int
    account_id: int
    muted: frozenset[NotificationType] = field(default_factory=frozenset)


def default_setting(definition: TypeDefinition) -> ChannelSetting:
    return ChannelSetting(
        in_app=definition.default_in_app or definition.mandatory_in_app,
        email=definition.default_email,
        webhook=definition.default_webhook,
    )


def _normalize(document: Mapping[str, object]) -> dict[NotificationType, ChannelSetting]:
    types: dict[NotificationType, ChannelSetting] = {}
    for notification_type, definition in DEFINITIONS.items():
        default = default_setting(definition)
        raw = document.get(notification_type.value)
        entry = raw if isinstance(raw, Mapping) else {}
        in_app = entry.get("in_app", default.in_app)
        email = entry.get("email", default.email)
        webhook = entry.get("webhook", default.webhook)
        types[notification_type] = ChannelSetting(
            in_app=True if definition.mandatory_in_app else bool(in_app),
            email=bool(email),
            webhook=bool(webhook),
        )
    return types


def load_organization_settings(db: sqlite3.Connection, account_id: int) -> OrganizationSettings:
    row = db.execute(
        "SELECT * FROM notification_settings WHERE account_id = ?", (int(account_id),)
    ).fetchone()
    if row is None:
        return OrganizationSettings(account_id, 0, _normalize({}), ())
    return OrganizationSettings(
        account_id=int(account_id),
        version=int(row["version"]),
        types=_normalize(json.loads(row["document"])),
        email_recipients=tuple(json.loads(row["email_recipients"])),
        updated_at=datetime.fromtimestamp(float(row["updated_at"]), UTC),
        updated_by=row["updated_by_login"],
    )


def load_user_preferences(db: sqlite3.Connection, user_id: int, account_id: int) -> UserPreferences:
    rows = db.execute(
        "SELECT type, in_app FROM notification_user_preferences WHERE user_id = ? "
        "AND account_id = ?",
        (int(user_id), int(account_id)),
    ).fetchall()
    muted = frozenset(
        NotificationType(r["type"]) for r in rows if not r["in_app"] and r["type"] in DEFINITIONS
    )
    return UserPreferences(int(user_id), int(account_id), muted)


def in_app_enabled(
    definition: TypeDefinition,
    organization: OrganizationSettings,
    user: UserPreferences | None,
) -> bool:
    if definition.mandatory_in_app:
        return True
    if not organization.types[definition.type].in_app:
        return False
    return user is None or definition.type not in user.muted


# --------------------------------------------------------------------------- #
# Validation of API input
# --------------------------------------------------------------------------- #
def parse_type(value: object, field_name: str) -> NotificationType:
    if not isinstance(value, str) or value not in {t.value for t in NotificationType}:
        raise InputValidationError("unknown notification type", field=field_name)
    return NotificationType(value)


def validate_organization_document(raw: object) -> dict[NotificationType, ChannelSetting]:
    """``{"type": {"in_app": bool, "email": bool, "webhook": bool}}`` - partial updates allowed."""
    if not isinstance(raw, Mapping):
        raise InputValidationError("types must be an object of notification types", field="types")
    document: dict[str, dict[str, bool]] = {}
    for key, entry in raw.items():
        notification_type = parse_type(key, "types")
        if not isinstance(entry, Mapping):
            raise InputValidationError("each type must be an object", field=f"types.{key}")
        clean: dict[str, bool] = {}
        for channel, value in entry.items():
            if channel not in {c.value for c in NotificationChannel}:
                raise InputValidationError("unknown channel", field=f"types.{key}")
            if not isinstance(value, bool):
                raise InputValidationError(
                    "channel settings must be true or false", field=f"types.{key}.{channel}"
                )
            if (
                channel == NotificationChannel.IN_APP.value
                and not value
                and DEFINITIONS[notification_type].mandatory_in_app
            ):
                raise InputValidationError(
                    "in-app delivery of this notification type cannot be turned off",
                    field=f"types.{key}.in_app",
                )
            clean[channel] = value
        document[notification_type.value] = clean
    return _normalize(document)


def validate_email_recipients(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise InputValidationError("email_recipients must be a list", field="email_recipients")
    if len(raw) > MAX_EMAIL_RECIPIENTS:
        raise InputValidationError(
            f"at most {MAX_EMAIL_RECIPIENTS} e-mail recipients", field="email_recipients"
        )
    recipients: list[str] = []
    for item in raw:
        if (
            not isinstance(item, str)
            or len(item) > MAX_EMAIL_CHARS
            or not _EMAIL_RE.match(item.strip())
        ):
            raise InputValidationError(
                "each recipient must be a plain e-mail address", field="email_recipients"
            )
        address = item.strip().lower()
        if address not in recipients:
            recipients.append(address)
    return tuple(recipients)


def serialize(types: Mapping[NotificationType, ChannelSetting]) -> str:
    return json.dumps(
        {
            t.value: {"in_app": s.in_app, "email": s.email, "webhook": s.webhook}
            for t, s in sorted(types.items())
        },
        sort_keys=True,
    )


def disabled_deliveries(
    before: OrganizationSettings,
    types: Mapping[NotificationType, ChannelSetting],
    recipients: Sequence[str],
) -> list[str]:
    """Human-readable list of deliveries a settings change turns off (needs confirmation)."""
    removed: list[str] = []
    for notification_type, setting in types.items():
        old = before.types[notification_type]
        label = DEFINITIONS[notification_type].label
        for channel in ("in_app", "email", "webhook"):
            if getattr(old, channel) and not getattr(setting, channel):
                removed.append(f"{label}: {channel.replace('_', '-')}")
    dropped = sorted(set(before.email_recipients) - set(recipients))
    if dropped:
        removed.append(f"e-mail recipients removed: {len(dropped)}")
    return removed
