"""Rendering notifications for each channel.

Every value that came from a repository or a user (repository names, rule
titles, reasons, logins) was sanitised when the event was created
(:func:`commitguard.notifications.models.clean`). Rendering adds only
channel-appropriate encoding:

* **in-app** - JSON through the API; the dashboard renders text, never HTML;
* **e-mail** - plain text; header values are passed to ``EmailMessage``;
* **webhook** - ``json.dumps`` with ASCII escaping, plus ``<``, ``>`` and ``&``
  escaped as ``\u003c``-style sequences.

No template includes tokens, keys, session data or internal database IDs other
than the resource ID needed for the dashboard link.
"""

import json
from datetime import UTC, datetime

from commitguard.notifications.channels.email import OutgoingEmail
from commitguard.notifications.models import DEFINITIONS, StoredNotificationEvent

_RESOURCE_PATHS = {
    "violation": "/violations/{id}",
    "scan": "/scans/{id}",
    "policy": "/policies/{id}",
    "installation": "/github/installations/{id}",
    "repository": "/repositories/{id}",
}


def resource_path(resource_type: str, resource_id: str) -> str | None:
    template = _RESOURCE_PATHS.get(resource_type)
    if template is None or not resource_id.isascii() or not resource_id.isalnum():
        return None
    return template.format(id=resource_id)


def render_email(
    stored: StoredNotificationEvent,
    *,
    to: str,
    organization: str,
    dashboard_origin: str | None,
) -> OutgoingEmail:
    event = stored.event
    definition = DEFINITIONS[event.type]
    path = resource_path(event.resource_type, event.resource_id)
    lines = [
        event.title,
        "",
        event.body,
        "",
        f"Organization: {organization}",
        f"Type: {definition.label}",
        f"Severity: {event.severity.value.upper()}",
        f"Time: {stored.last_occurred_at.astimezone(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
    ]
    if stored.occurrences > 1:
        lines.append(f"Occurrences: {stored.occurrences}")
    if dashboard_origin and path:
        lines += ["", f"Open in CommitGuard: {dashboard_origin}{path}"]
    lines += [
        "",
        "--",
        "You receive this because an administrator of this organization added this address "
        "to CommitGuard notifications.",
        "CommitGuard never rewrites Git history or changes repositories.",
    ]
    return OutgoingEmail(
        to=to,
        subject=f"[CommitGuard] {event.severity.value.upper()}: {event.title}",
        text="\n".join(lines) + "\n",
    )


def render_webhook(
    stored: StoredNotificationEvent,
    *,
    organization: str,
    repository: str | None,
    dashboard_origin: str | None,
    now: datetime,
) -> bytes:
    event = stored.event
    path = resource_path(event.resource_type, event.resource_id)
    document = {
        "id": event.event_id,
        "type": event.type.value,
        "severity": event.severity.value,
        "title": event.title,
        "summary": event.body,
        "organization": organization,
        "repository": repository,
        "occurrences": stored.occurrences,
        "created_at": stored.created_at.astimezone(UTC).isoformat(),
        "last_occurred_at": stored.last_occurred_at.astimezone(UTC).isoformat(),
        "sent_at": now.astimezone(UTC).isoformat(),
        "url": f"{dashboard_origin}{path}" if dashboard_origin and path else None,
        "details": event.metadata,
    }
    text = json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    # Still valid JSON; safe even if a receiver embeds the payload in an HTML page.
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").encode()
