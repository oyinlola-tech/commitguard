"""The dashboard's notification center, preferences and organization settings.

Access model
============

A user sees a notification only when **all** of these hold at read time:

1. it is addressed to them (an inbox row created for their user ID when the
   event was dispatched);
2. they still have a CommitGuard role in the organization, and that role grants
   the notification type's permission (for example ``github:manage`` for
   installation disconnects);
3. GitHub reported the organization's installation for their session at
   sign-in - including an installation that has since been removed, so
   administrators can read the disconnect notification about it;
4. for a repository notification, GitHub reported that repository for their
   session.

Anything else is "not found", exactly like every other resource. Marking read
or archiving touches only the caller's own inbox row.

Organization settings (``notifications:manage``) are versioned with optimistic
concurrency; changes that turn a delivery off need ``confirm: true``. Adding a
webhook endpoint sends security data outside CommitGuard, so it needs explicit
confirmation and a recent sign-in, and its signing secret is shown only once.
"""

import json
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from commitguard.audit.models import Actor, AuditEventType
from commitguard.controlplane.access import Permission, Principal, Role
from commitguard.controlplane.errors import (
    ConfirmationRequiredError,
    ConflictError,
    InputValidationError,
    NotFoundError,
    PermissionDeniedError,
    ReauthenticationRequiredError,
)
from commitguard.controlplane.pagination import Page, decode_cursor, encode_cursor
from commitguard.controlplane.policies import REAUTHENTICATION_WINDOW
from commitguard.controlplane.views import (
    ChannelPreferenceView,
    CreatedWebhookView,
    NotificationChannelsView,
    NotificationCounts,
    NotificationDeliveryView,
    NotificationView,
    OrganizationNotificationSettingsView,
    OrganizationRef,
    RepositoryLink,
    TypePreferenceView,
    WebhookEndpointView,
)
from commitguard.core.result import Severity
from commitguard.github.storage import SqliteStateStore
from commitguard.notifications.channels.webhook import (
    WebhookUrlError,
    endpoint_secret,
    validate_webhook_url,
)
from commitguard.notifications.models import (
    DEFINITIONS,
    NotificationCategory,
    NotificationState,
    NotificationType,
)
from commitguard.notifications.preferences import (
    disabled_deliveries,
    load_organization_settings,
    load_user_preferences,
    parse_type,
    serialize,
    validate_email_recipients,
    validate_organization_document,
)
from commitguard.notifications.retry import mask_destination
from commitguard.notifications.settings import NotificationSettings
from commitguard.notifications.templates import resource_path
from commitguard.services.audit import AuditService

MAX_WEBHOOKS = 10
MAX_READ_ALL = 10_000
MAX_COUNTED = 1000
CATEGORY_FILTERS = ("critical", *[c.value for c in NotificationCategory])


def _dt(value: float | None) -> datetime | None:
    return None if value is None else datetime.fromtimestamp(float(value), UTC)


@dataclass(frozen=True, slots=True)
class _Access:
    """Organizations whose notifications the session may read, with the member's role."""

    roles: Mapping[int, Role]
    logins: Mapping[int, tuple[str, str]]  # account -> (login, type)
    installations: tuple[int, ...]

    def pairs_json(self) -> str:
        """[[account_id, type], ...] the caller's roles may read."""
        return json.dumps(
            [
                [account_id, notification_type.value]
                for account_id, role in sorted(self.roles.items())
                for notification_type, definition in DEFINITIONS.items()
                if definition.permission in role.permissions
            ]
        )


_VISIBLE = (
    "n.user_id = ? AND EXISTS (SELECT 1 FROM json_each(?) p WHERE "
    "json_extract(p.value, '$[0]') = e.account_id AND json_extract(p.value, '$[1]') = e.type) "
    "AND (e.installation_id IS NULL OR e.installation_id IN (SELECT value FROM json_each(?))) "
    "AND (e.repository_id IS NULL OR EXISTS (SELECT 1 FROM session_repositories sr WHERE "
    "sr.session_hash = ? AND sr.installation_id = e.installation_id "
    "AND sr.repository_id = e.repository_id))"
)
_COLUMNS = (
    "n.notification_id, n.state, n.read_at, n.sort_at, e.event_id, e.type, "
    "e.severity, e.title, e.body, e.account_id, e.installation_id, e.repository_id, "
    "e.resource_type, e.resource_id, e.occurrences, e.created_at, e.last_occurred_at, "
    "(SELECT owner || '/' || name FROM known_repositories k WHERE "
    "k.installation_id = e.installation_id AND k.repository_id = e.repository_id) AS full_name"
)


_FROM = "FROM notifications n JOIN notification_events e ON e.event_id = n.event_id"


class NotificationCenter:
    def __init__(
        self,
        store: SqliteStateStore,
        audit: AuditService,
        settings: NotificationSettings,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._audit = audit
        self._settings = settings
        self._now = now

    # ------------------------------------------------------------------ #
    # Access
    # ------------------------------------------------------------------ #
    def _access(self, principal: Principal) -> _Access:
        rows = self._store.query(
            "SELECT i.installation_id, i.account_id, i.account_login, i.account_type "
            "FROM session_installations si JOIN installations i ON i.installation_id = "
            "si.installation_id WHERE si.session_hash = ?",
            (principal.session_hash,),
        )
        accounts = {int(r["account_id"]): (r["account_login"], r["account_type"]) for r in rows}
        roles: dict[int, Role] = {}
        for row in self._store.query(
            "SELECT account_id, role FROM memberships WHERE user_id = ?", (principal.user_id,)
        ):
            if int(row["account_id"]) in accounts and row["role"] in {r.value for r in Role}:
                roles[int(row["account_id"])] = Role(row["role"])
        for account_id, (_, account_type) in accounts.items():
            if account_type == "User" and account_id == principal.user_id:
                roles[account_id] = Role.OWNER
        installations = tuple(
            sorted(int(r["installation_id"]) for r in rows if int(r["account_id"]) in roles)
        )
        return _Access(roles, accounts, installations)

    def _visible_params(self, principal: Principal, access: _Access) -> tuple[Any, ...]:
        return (
            principal.user_id,
            access.pairs_json(),
            json.dumps(list(access.installations)),
            principal.session_hash,
        )

    def _organization(
        self, principal: Principal, organization_id: int, permission: Permission
    ) -> tuple[OrganizationRef, Role]:
        access = self._access(principal)
        role = access.roles.get(organization_id)
        if role is None:
            raise NotFoundError()
        if permission not in role.permissions:
            raise PermissionDeniedError()
        login, account_type = access.logins[organization_id]
        return OrganizationRef(id=organization_id, login=login, type=account_type), role

    # ------------------------------------------------------------------ #
    # Inbox
    # ------------------------------------------------------------------ #
    @staticmethod
    def _view(row: sqlite3.Row) -> NotificationView:
        definition = DEFINITIONS[NotificationType(row["type"])]
        repository = None
        if row["repository_id"] is not None and row["installation_id"] is not None:
            repository = RepositoryLink(
                id=row["repository_id"],
                installation_id=row["installation_id"],
                full_name=row["full_name"] or f"repository {row['repository_id']}",
            )
        return NotificationView(
            id=row["notification_id"],
            type=row["type"],
            category=definition.category.value,
            severity=Severity(row["severity"]),
            state=row["state"],
            title=row["title"],
            body=row["body"],
            organization_id=row["account_id"],
            repository=repository,
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            link=resource_path(row["resource_type"], row["resource_id"]),
            occurrences=row["occurrences"],
            created_at=datetime.fromtimestamp(float(row["created_at"]), UTC),
            last_occurred_at=datetime.fromtimestamp(float(row["last_occurred_at"]), UTC),
            read_at=_dt(row["read_at"]),
        )

    def list_notifications(
        self,
        principal: Principal,
        *,
        state: str | None,
        category: str | None,
        organization_id: int | None,
        cursor: str | None,
        limit: int,
    ) -> Page[NotificationView]:
        access = self._access(principal)
        clauses = [_VISIBLE]
        params: list[Any] = list(self._visible_params(principal, access))
        if state is None:
            clauses.append("n.state != 'archived'")
        else:
            clauses.append("n.state = ?")
            params.append(state)
        if category == "critical":
            clauses.append("n.severity = 'critical'")
        elif category is not None:
            types = [t.value for t, d in DEFINITIONS.items() if d.category.value == category]
            clauses.append("e.type IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(types))
        if organization_id is not None:
            clauses.append("e.account_id = ?")
            params.append(organization_id)
        position = decode_cursor(cursor, (float, str))
        if position is not None:
            clauses.append("(n.sort_at < ? OR (n.sort_at = ? AND n.notification_id < ?))")
            params.extend([position[0], position[0], position[1]])
        # Clauses are static SQL fragments from this module; values are bound parameters.
        rows = self._store.query(
            " ".join(
                (
                    "SELECT",
                    _COLUMNS,
                    _FROM,
                    "WHERE",
                    " AND ".join(clauses),
                    "ORDER BY n.sort_at DESC, n.notification_id DESC LIMIT ?",
                )
            ),
            (*params, limit + 1),
        )
        items = [self._view(r) for r in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = encode_cursor([float(last["sort_at"]), last["notification_id"]])
        return Page(items, next_cursor, limit)

    def _row(self, principal: Principal, notification_id: str) -> sqlite3.Row | None:
        if len(notification_id) != 32 or not all(c in "0123456789abcdef" for c in notification_id):
            return None
        access = self._access(principal)
        rows = self._store.query(
            " ".join(("SELECT", _COLUMNS, _FROM, "WHERE n.notification_id = ? AND", _VISIBLE)),
            (notification_id, *self._visible_params(principal, access)),
        )
        return rows[0] if rows else None

    def get(self, principal: Principal, notification_id: str) -> NotificationView | None:
        row = self._row(principal, notification_id)
        return self._view(row) if row is not None else None

    def counts(self, principal: Principal) -> NotificationCounts:
        """Unread counts for the bell, bounded: at most :data:`MAX_COUNTED` rows are counted."""
        access = self._access(principal)
        params = self._visible_params(principal, access)
        unread = self._store.query(
            " ".join(
                (
                    "SELECT COUNT(*) AS n FROM (SELECT 1",
                    _FROM,
                    "WHERE n.state = 'unread' AND",
                    _VISIBLE,
                    "LIMIT ?)",
                )
            ),
            (*params, MAX_COUNTED + 1),
        )[0]["n"]
        critical = self._store.query(
            " ".join(
                (
                    "SELECT COUNT(*) AS n FROM (SELECT 1",
                    _FROM,
                    "WHERE n.severity = 'critical' AND n.state = 'unread' AND",
                    _VISIBLE,
                    "LIMIT ?)",
                )
            ),
            (*params, MAX_COUNTED + 1),
        )[0]["n"]
        return NotificationCounts(
            unread=min(int(unread), MAX_COUNTED),
            unread_critical=min(int(critical), MAX_COUNTED),
            capped=int(unread) > MAX_COUNTED,
        )

    def set_state(
        self, principal: Principal, notification_id: str, state: NotificationState
    ) -> NotificationView:
        row = self._row(principal, notification_id)
        if row is None:
            raise NotFoundError()
        now = self._now().timestamp()
        audit = None
        with self._store.transaction() as db:
            db.execute(
                "UPDATE notifications SET state = ?, updated_at = ?, "
                "read_at = CASE WHEN ? != 'unread' THEN COALESCE(read_at, ?) ELSE NULL END, "
                "archived_at = CASE WHEN ? = 'archived' THEN ? ELSE NULL END "
                "WHERE notification_id = ? AND user_id = ?",
                (
                    state.value,
                    now,
                    state.value,
                    now,
                    state.value,
                    now,
                    notification_id,
                    principal.user_id,
                ),
            )
            if (
                row["state"] == NotificationState.UNREAD.value
                and state is not NotificationState.UNREAD
            ):
                audit = self._store.insert_audit_event(
                    db,
                    self._audit.build(
                        AuditEventType.NOTIFICATION_READ,
                        actor=Actor.user(principal.user_id, principal.login),
                        account_id=int(row["account_id"]),
                        installation_id=row["installation_id"],
                        repository_id=row["repository_id"],
                        notification=notification_id,
                        notification_type=row["type"],
                        state=state.value,
                    ),
                )
        if audit is not None:
            self._audit.log_stored(audit)
        updated = self.get(principal, notification_id)
        assert updated is not None  # noqa: S101 - visible above
        return updated

    def mark_all_read(self, principal: Principal, organization_id: int | None) -> int:
        access = self._access(principal)
        params: list[Any] = list(self._visible_params(principal, access))
        clause = ""
        if organization_id is not None:
            clause = "AND e.account_id = ?"
            params.append(organization_id)
        rows = self._store.query(
            " ".join(
                (
                    "SELECT n.notification_id, e.account_id",
                    _FROM,
                    "WHERE n.state = 'unread' AND",
                    _VISIBLE,
                    clause,
                    "LIMIT ?",
                )
            ),
            (*params, MAX_READ_ALL),
        )
        if not rows:
            return 0
        now = self._now().timestamp()
        per_account: dict[int, int] = {}
        for row in rows:
            per_account[int(row["account_id"])] = per_account.get(int(row["account_id"]), 0) + 1
        events = []
        with self._store.transaction() as db:
            db.execute(
                "UPDATE notifications SET state = 'read', read_at = ?, updated_at = ? "
                "WHERE user_id = ? AND state = 'unread' AND notification_id IN "
                "(SELECT value FROM json_each(?))",
                (now, now, principal.user_id, json.dumps([r["notification_id"] for r in rows])),
            )
            for account_id, count in sorted(per_account.items()):
                events.append(
                    self._store.insert_audit_event(
                        db,
                        self._audit.build(
                            AuditEventType.NOTIFICATION_READ,
                            actor=Actor.user(principal.user_id, principal.login),
                            account_id=account_id,
                            notifications=count,
                            state="read",
                            bulk=True,
                        ),
                    )
                )
        for event in events:
            self._audit.log_stored(event)
        return len(rows)

    # ------------------------------------------------------------------ #
    # Preferences and organization settings
    # ------------------------------------------------------------------ #
    def _channels(self) -> NotificationChannelsView:
        return NotificationChannelsView(
            in_app=True,
            email=self._settings.email_available,
            webhook=self._settings.webhook_available,
            mode=self._settings.mode.value,
        )

    def _settings_view(
        self, principal: Principal, organization: OrganizationRef, role: Role
    ) -> OrganizationNotificationSettingsView:
        with self._store.transaction() as db:
            settings = load_organization_settings(db, organization.id)
            personal = load_user_preferences(db, principal.user_id, organization.id)
            webhooks = db.execute(
                "SELECT endpoint_id, url, created_at, created_by_login FROM notification_webhooks "
                "WHERE account_id = ? AND removed_at IS NULL ORDER BY created_at",
                (organization.id,),
            ).fetchall()
        can_manage = Permission.NOTIFICATIONS_MANAGE in role.permissions
        types = []
        for notification_type, definition in DEFINITIONS.items():
            setting = settings.types[notification_type]
            types.append(
                TypePreferenceView(
                    type=notification_type.value,
                    label=definition.label,
                    description=definition.description,
                    category=definition.category.value,
                    mandatory_in_app=definition.mandatory_in_app,
                    organization=ChannelPreferenceView(
                        in_app=setting.in_app, email=setting.email, webhook=setting.webhook
                    ),
                    personal_in_app=definition.mandatory_in_app
                    or notification_type not in personal.muted,
                    receives_in_app=definition.permission in role.permissions,
                )
            )
        return OrganizationNotificationSettingsView(
            organization=organization,
            version=settings.version,
            updated_at=settings.updated_at,
            updated_by=settings.updated_by,
            channels=self._channels(),
            types=tuple(types),
            email_recipients=settings.email_recipients if can_manage else (),
            webhooks=tuple(
                WebhookEndpointView(
                    id=w["endpoint_id"],
                    url=w["url"],
                    created_at=datetime.fromtimestamp(float(w["created_at"]), UTC),
                    created_by=w["created_by_login"],
                )
                for w in webhooks
            )
            if can_manage
            else (),
            can_manage=can_manage,
        )

    def preferences(self, principal: Principal) -> list[OrganizationNotificationSettingsView]:
        access = self._access(principal)
        return [
            self._settings_view(
                principal,
                OrganizationRef(id=account_id, login=login, type=account_type),
                access.roles[account_id],
            )
            for account_id, (login, account_type) in sorted(
                access.logins.items(), key=lambda item: item[1][0].lower()
            )
            if account_id in access.roles
        ]

    def organization_settings(
        self, principal: Principal, organization_id: int
    ) -> OrganizationNotificationSettingsView:
        organization, role = self._organization(
            principal, organization_id, Permission.NOTIFICATIONS_READ
        )
        return self._settings_view(principal, organization, role)

    def update_personal(
        self, principal: Principal, organization_id: object, raw: object
    ) -> OrganizationNotificationSettingsView:
        if not isinstance(organization_id, int) or isinstance(organization_id, bool):
            raise InputValidationError("organization_id is required", field="organization_id")
        organization, role = self._organization(
            principal, organization_id, Permission.NOTIFICATIONS_READ
        )
        if not isinstance(raw, Mapping) or not raw:
            raise InputValidationError(
                "in_app must be an object of notification types", field="in_app"
            )
        changes: dict[NotificationType, bool] = {}
        for key, value in raw.items():
            notification_type = parse_type(key, "in_app")
            if not isinstance(value, bool):
                raise InputValidationError("values must be true or false", field=f"in_app.{key}")
            if DEFINITIONS[notification_type].mandatory_in_app and not value:
                raise InputValidationError(
                    "this notification type cannot be muted", field=f"in_app.{key}"
                )
            changes[notification_type] = value
        now = self._now().timestamp()
        with self._store.transaction() as db:
            db.executemany(
                "INSERT INTO notification_user_preferences (user_id, account_id, type, in_app, "
                "updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT (user_id, account_id, type) DO "
                "UPDATE SET in_app = excluded.in_app, updated_at = excluded.updated_at",
                [
                    (principal.user_id, organization.id, t.value, int(v), now)
                    for t, v in sorted(changes.items())
                ],
            )
            event = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.NOTIFICATION_PREFERENCES_CHANGED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=organization.id,
                    scope="personal",
                    muted=",".join(sorted(t.value for t, v in changes.items() if not v)) or None,
                    unmuted=",".join(sorted(t.value for t, v in changes.items() if v)) or None,
                ),
            )
        self._audit.log_stored(event)
        return self._settings_view(principal, organization, role)

    def update_organization(
        self, principal: Principal, organization_id: int, body: Mapping[str, object]
    ) -> OrganizationNotificationSettingsView:
        organization, role = self._organization(
            principal, organization_id, Permission.NOTIFICATIONS_MANAGE
        )
        expected = body.get("expected_version")
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
            raise InputValidationError(
                "expected_version must be a non-negative integer", field="expected_version"
            )
        types = validate_organization_document(body.get("types", {}))
        with self._store.transaction() as db:
            current = load_organization_settings(db, organization.id)
        recipients = (
            validate_email_recipients(body["email_recipients"])
            if "email_recipients" in body
            else current.email_recipients
        )
        if current.version != expected:
            raise ConflictError(
                f"Notification settings were changed by someone else (now version "
                f"{current.version}). Reload before saving."
            )
        disabled = disabled_deliveries(current, types, recipients)
        if disabled and body.get("confirm") is not True:
            raise ConfirmationRequiredError(
                "This change turns off notification deliveries and must be confirmed: "
                + "; ".join(disabled[:10])
            )
        now = self._now().timestamp()
        with self._store.transaction() as db:
            latest = load_organization_settings(db, organization.id)
            if latest.version != expected:
                raise ConflictError(
                    f"Notification settings were changed by someone else (now version "
                    f"{latest.version}). Reload before saving."
                )
            db.execute(
                "INSERT INTO notification_settings (account_id, version, document, "
                "email_recipients, updated_at, updated_by_login) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (account_id) DO UPDATE SET version = excluded.version, "
                "document = excluded.document, email_recipients = excluded.email_recipients, "
                "updated_at = excluded.updated_at, updated_by_login = excluded.updated_by_login",
                (
                    organization.id,
                    expected + 1,
                    serialize(types),
                    json.dumps(list(recipients)),
                    now,
                    principal.login,
                ),
            )
            added = sorted(set(recipients) - set(latest.email_recipients))
            event = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.NOTIFICATION_SETTINGS_CHANGED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=organization.id,
                    old_version=expected,
                    new_version=expected + 1,
                    disabled="; ".join(disabled)[:500] or None,
                    email_recipients=len(recipients),
                    email_recipients_added=",".join(mask_destination("email", a) for a in added)[
                        :500
                    ]
                    or None,
                ),
            )
        self._audit.log_stored(event)
        return self._settings_view(principal, organization, role)

    # ------------------------------------------------------------------ #
    # Webhook endpoints
    # ------------------------------------------------------------------ #
    def add_webhook(
        self, principal: Principal, organization_id: int, body: Mapping[str, object]
    ) -> CreatedWebhookView:
        organization, _ = self._organization(
            principal, organization_id, Permission.NOTIFICATIONS_MANAGE
        )
        if not self._settings.webhook_available or self._settings.signing_key is None:
            raise ConflictError("Webhook delivery is not configured on this CommitGuard server.")
        try:
            url = validate_webhook_url(
                body.get("url"), allow_insecure_local=not self._settings.production
            )
        except WebhookUrlError as exc:
            raise InputValidationError(str(exc), field="url") from None
        if body.get("confirm") is not True:
            raise ConfirmationRequiredError(
                "A webhook sends security notifications outside CommitGuard and must be confirmed."
            )
        now = self._now()
        if now - principal.authenticated_at > REAUTHENTICATION_WINDOW:
            raise ReauthenticationRequiredError()
        endpoint_id = uuid.uuid4().hex
        with self._store.transaction() as db:
            count = db.execute(
                "SELECT COUNT(*) AS n FROM notification_webhooks WHERE account_id = ? "
                "AND removed_at IS NULL",
                (organization.id,),
            ).fetchone()["n"]
            if int(count) >= MAX_WEBHOOKS:
                raise ConflictError(f"An organization can have at most {MAX_WEBHOOKS} webhooks.")
            db.execute(
                "INSERT INTO notification_webhooks (endpoint_id, account_id, url, created_at, "
                "created_by_login) VALUES (?, ?, ?, ?, ?)",
                (endpoint_id, organization.id, url, now.timestamp(), principal.login),
            )
            event = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.NOTIFICATION_WEBHOOK_ADDED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=organization.id,
                    endpoint=endpoint_id,
                    host=urlsplit(url).hostname,
                ),
            )
        self._audit.log_stored(event)
        return CreatedWebhookView(
            endpoint=WebhookEndpointView(
                id=endpoint_id, url=url, created_at=now, created_by=principal.login
            ),
            signing_secret=endpoint_secret(self._settings.signing_key, endpoint_id).reveal(),
        )

    def remove_webhook(self, principal: Principal, organization_id: int, endpoint_id: str) -> None:
        organization, _ = self._organization(
            principal, organization_id, Permission.NOTIFICATIONS_MANAGE
        )
        now = self._now().timestamp()
        with self._store.transaction() as db:
            row = db.execute(
                "SELECT url FROM notification_webhooks WHERE endpoint_id = ? AND account_id = ? "
                "AND removed_at IS NULL",
                (endpoint_id, organization.id),
            ).fetchone()
            if row is None:
                raise NotFoundError()
            db.execute(
                "UPDATE notification_webhooks SET removed_at = ? WHERE endpoint_id = ?",
                (now, endpoint_id),
            )
            db.execute(
                "UPDATE notification_deliveries SET status = 'cancelled', "
                "failure_code = 'webhook_endpoint_removed', updated_at = ? "
                "WHERE destination = ? AND account_id = ? AND status = 'pending'",
                (now, endpoint_id, organization.id),
            )
            event = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.NOTIFICATION_WEBHOOK_REMOVED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=organization.id,
                    endpoint=endpoint_id,
                    host=urlsplit(str(row["url"])).hostname,
                ),
            )
        self._audit.log_stored(event)

    def deliveries(
        self, principal: Principal, organization_id: int, *, cursor: str | None, limit: int
    ) -> Page[NotificationDeliveryView]:
        organization, _ = self._organization(
            principal, organization_id, Permission.NOTIFICATIONS_MANAGE
        )
        params: list[Any] = [organization.id]
        clause = ""
        position = decode_cursor(cursor, (float, str))
        if position is not None:
            clause = "AND (d.created_at < ? OR (d.created_at = ? AND d.delivery_id < ?))"
            params.extend([position[0], position[0], position[1]])
        rows = self._store.query(
            " ".join(
                (
                    "SELECT d.*, e.type, e.title FROM notification_deliveries d JOIN "
                    "notification_events e ON e.event_id = d.event_id WHERE d.account_id = ?",
                    clause,
                    "ORDER BY d.created_at DESC, d.delivery_id DESC LIMIT ?",
                )
            ),
            (*params, limit + 1),
        )
        items = [
            NotificationDeliveryView(
                id=r["delivery_id"],
                notification_type=r["type"],
                title=r["title"],
                channel=r["channel"],
                destination=r["destination"],
                status=r["status"],
                attempts=r["attempt_count"],
                failure_code=r["failure_code"],
                last_attempt_at=_dt(r["last_attempt_at"]),
                next_retry_at=_dt(r["next_retry_at"]),
                created_at=datetime.fromtimestamp(float(r["created_at"]), UTC),
            )
            for r in rows[:limit]
        ]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = encode_cursor([float(last["created_at"]), last["delivery_id"]])
        return Page(items, next_cursor, limit)


def parse_state_filter(value: str | None) -> str | None:
    if value is None or value == "" or value == "all":
        return None
    if value not in {s.value for s in NotificationState}:
        raise InputValidationError("state must be unread, read, archived or all", field="state")
    return value


def parse_category_filter(value: str | None) -> str | None:
    if value is None or value == "" or value == "all":
        return None
    if value not in CATEGORY_FILTERS:
        raise InputValidationError(
            "category must be one of: all, " + ", ".join(CATEGORY_FILTERS), field="category"
        )
    return value
