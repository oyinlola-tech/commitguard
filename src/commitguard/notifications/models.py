"""Notification vocabulary: types, channels, states and the normalised event.

A notification never makes a security decision. It is produced *after* a
domain service has decided and stored something - a scan blocked a commit, an
administrator changed or rolled back policy, a GitHub installation lost access
- and it is written into the notification outbox in the same database
transaction as that change (:mod:`commitguard.notifications.outbox`).

Types
=====

=============================  ===========  ===============================  =========
Type                           Category     In-app recipients (permission)   Mandatory
=============================  ===========  ===============================  =========
``critical_violation``         violations   ``violations:read``              in-app
``high_violation``             violations   ``violations:read``
``policy_changed``             policy       ``audit:read``                   in-app
``policy_rolled_back``         policy       ``audit:read``                   in-app
``installation_disconnected``  github       ``github:manage``                in-app
``installation_reconnected``   github       ``github:manage``
``merge_queue_failure``        scans        ``scans:read``
``check_rerun_failed``         scans        ``scans:read``
=============================  ===========  ===============================  =========

"Mandatory" in-app notifications cannot be muted by a user or turned off by an
organization: they cover events after which enforcement may no longer be what
people believe it is. E-mail and webhook delivery is configurable per type by
administrators (``notifications:manage``).

Severity reuses the core :class:`~commitguard.core.result.Severity` model.
Violation notifications take the finding's severity; the bundled detectors
classify AI attribution as ``high``, so ``critical_violation`` is produced only
by rules that report ``critical``.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from commitguard.controlplane.access import Permission
from commitguard.core.result import Severity
from commitguard.security.sanitization import sanitize_for_terminal
from commitguard.security.secrets import redact

MAX_TITLE_CHARS = 200
MAX_BODY_CHARS = 1000
MAX_METADATA_ITEMS = 24
MAX_METADATA_STRING = 256
_KEY_RE = re.compile(r"\A[a-z][a-z0-9_]{0,63}\Z")

type MetadataValue = str | int | bool | None


def clean(value: str, limit: int) -> str:
    """Untrusted text as stored in a notification: secrets redacted, controls visible."""
    return sanitize_for_terminal(redact(value), max_length=limit)


class NotificationType(StrEnum):
    CRITICAL_VIOLATION = "critical_violation"
    HIGH_VIOLATION = "high_violation"
    POLICY_CHANGED = "policy_changed"
    POLICY_ROLLED_BACK = "policy_rolled_back"
    INSTALLATION_DISCONNECTED = "installation_disconnected"
    INSTALLATION_RECONNECTED = "installation_reconnected"
    MERGE_QUEUE_FAILURE = "merge_queue_failure"
    CHECK_RERUN_FAILED = "check_rerun_failed"


class NotificationCategory(StrEnum):
    VIOLATIONS = "violations"
    POLICY = "policy"
    GITHUB = "github"
    SCANS = "scans"


class NotificationChannel(StrEnum):
    IN_APP = "in_app"
    EMAIL = "email"
    WEBHOOK = "webhook"


class NotificationState(StrEnum):
    UNREAD = "unread"
    READ = "read"
    ARCHIVED = "archived"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


ResourceType = Literal["violation", "scan", "policy", "installation", "repository"]


@dataclass(frozen=True, slots=True)
class TypeDefinition:
    type: NotificationType
    label: str
    description: str
    category: NotificationCategory
    permission: Permission  # who receives the in-app notification
    repository_scoped: bool  # only users who can see the repository on GitHub see it
    mandatory_in_app: bool
    default_email: bool
    default_webhook: bool
    default_in_app: bool = True
    #: repeated events with the same key inside this window update one notification
    coalesce_seconds: int | None = None


DEFINITIONS: dict[NotificationType, TypeDefinition] = {
    d.type: d
    for d in (
        TypeDefinition(
            NotificationType.CRITICAL_VIOLATION,
            "Critical violations",
            "A scan blocked commits for a finding classified critical.",
            NotificationCategory.VIOLATIONS,
            Permission.VIOLATIONS_READ,
            repository_scoped=True,
            mandatory_in_app=True,
            default_email=True,
            default_webhook=True,
            coalesce_seconds=3600,
        ),
        TypeDefinition(
            NotificationType.HIGH_VIOLATION,
            "High-severity violations",
            "A scan blocked commits for a finding classified high (for example AI attribution).",
            NotificationCategory.VIOLATIONS,
            Permission.VIOLATIONS_READ,
            repository_scoped=True,
            mandatory_in_app=False,
            default_email=False,
            default_webhook=False,
            coalesce_seconds=3600,
        ),
        TypeDefinition(
            NotificationType.POLICY_CHANGED,
            "Policy changes",
            "An administrator published a new organization policy version.",
            NotificationCategory.POLICY,
            Permission.AUDIT_READ,
            repository_scoped=False,
            mandatory_in_app=True,
            default_email=True,
            default_webhook=True,
        ),
        TypeDefinition(
            NotificationType.POLICY_ROLLED_BACK,
            "Policy rollbacks",
            "An administrator restored an earlier organization policy version.",
            NotificationCategory.POLICY,
            Permission.AUDIT_READ,
            repository_scoped=False,
            mandatory_in_app=True,
            default_email=True,
            default_webhook=True,
        ),
        TypeDefinition(
            NotificationType.INSTALLATION_DISCONNECTED,
            "Installation disconnects",
            "The GitHub App was uninstalled or suspended: GitHub enforcement is at risk.",
            NotificationCategory.GITHUB,
            Permission.GITHUB_MANAGE,
            repository_scoped=False,
            mandatory_in_app=True,
            default_email=True,
            default_webhook=True,
            coalesce_seconds=3600,
        ),
        TypeDefinition(
            NotificationType.INSTALLATION_RECONNECTED,
            "Installation reconnects",
            "A GitHub App installation became available again.",
            NotificationCategory.GITHUB,
            Permission.GITHUB_MANAGE,
            repository_scoped=False,
            mandatory_in_app=False,
            default_email=True,
            default_webhook=True,
            coalesce_seconds=3600,
        ),
        TypeDefinition(
            NotificationType.MERGE_QUEUE_FAILURE,
            "Merge queue failures",
            "A merge group was blocked, or could not be validated.",
            NotificationCategory.SCANS,
            Permission.SCANS_READ,
            repository_scoped=True,
            mandatory_in_app=False,
            default_email=False,
            default_webhook=True,
        ),
        TypeDefinition(
            NotificationType.CHECK_RERUN_FAILED,
            "Check re-run failures",
            "A re-run of the CommitGuard check could not be completed.",
            NotificationCategory.SCANS,
            Permission.SCANS_READ,
            repository_scoped=True,
            mandatory_in_app=False,
            default_email=False,
            default_webhook=False,
        ),
    )
}


class NotificationEvent(BaseModel):
    """A normalised notification event, as written to the outbox."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    type: NotificationType
    account_id: int
    severity: Severity
    installation_id: int | None = None
    repository_id: int | None = None
    resource_type: ResourceType
    resource_id: str = Field(min_length=1, max_length=64)
    #: Domain identity of the underlying occurrence, before the coalescing window.
    dedup_key: str = Field(min_length=1, max_length=512)
    title: str
    body: str
    metadata: dict[str, MetadataValue] = {}

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        return clean(value, MAX_TITLE_CHARS)

    @field_validator("body")
    @classmethod
    def _body(cls, value: str) -> str:
        return clean(value, MAX_BODY_CHARS)

    @field_validator("metadata")
    @classmethod
    def _metadata(cls, value: dict[str, MetadataValue]) -> dict[str, MetadataValue]:
        if len(value) > MAX_METADATA_ITEMS:
            raise ValueError(f"notification metadata has more than {MAX_METADATA_ITEMS} items")
        result: dict[str, MetadataValue] = {}
        for key, item in value.items():
            if not _KEY_RE.match(key):
                raise ValueError(f"invalid notification metadata key {key!r}")
            result[key] = clean(item, MAX_METADATA_STRING) if isinstance(item, str) else item
        return result

    @property
    def definition(self) -> TypeDefinition:
        return DEFINITIONS[self.type]


class StoredNotificationEvent(BaseModel):
    """A notification event as read back from the outbox."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event: NotificationEvent
    occurrences: int
    created_at: datetime
    last_occurred_at: datetime
    dispatched_at: datetime | None
    request_id: str | None
    delivery_id: str | None
    job_id: str | None
