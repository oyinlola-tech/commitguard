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
``violation_digest``           violations   ``violations:read``
``policy_approval_requested``  policy       ``policies:approve``
``policy_emergency_published`` policy       ``audit:read``                   in-app
``policy_rollout_failed``      policy       ``policies:publish``             in-app
``policy_propagation_failed``  policy       ``policies:publish``             in-app
``exception_requested``        policy       ``exceptions:approve``
``exception_approved``         policy       ``exceptions:read``
``exception_expiring``         policy       ``exceptions:read``
``exception_ended``            policy       ``exceptions:read``
``repository_unprotected``     github       ``repositories:manage``          in-app
``organization_settings_changed`` policy    ``audit:read``
=============================  ===========  ===============================  =========

``violation_digest`` is the organization-level aggregation of violation
alerts: when an organization enables it, one notification per rule and hour
("blocked commits in 14 repositories") replaces the per-pull-request e-mails and
webhooks; the dashboard keeps the per-repository detail.

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
    # Phase 8: organization governance.
    VIOLATION_DIGEST = "violation_digest"
    POLICY_APPROVAL_REQUESTED = "policy_approval_requested"
    POLICY_EMERGENCY_PUBLISHED = "policy_emergency_published"
    POLICY_ROLLOUT_FAILED = "policy_rollout_failed"
    POLICY_PROPAGATION_FAILED = "policy_propagation_failed"
    EXCEPTION_REQUESTED = "exception_requested"
    EXCEPTION_APPROVED = "exception_approved"
    EXCEPTION_EXPIRING = "exception_expiring"
    EXCEPTION_ENDED = "exception_ended"
    REPOSITORY_UNPROTECTED = "repository_unprotected"
    ORGANIZATION_SETTINGS_CHANGED = "organization_settings_changed"


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


ResourceType = Literal[
    "violation",
    "scan",
    "policy",
    "installation",
    "repository",
    "exception",
    "draft",
    "rollout",
    "organization",
]


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
        TypeDefinition(
            NotificationType.VIOLATION_DIGEST,
            "Violation digests",
            "Blocked violations across repositories, aggregated per rule and hour.",
            NotificationCategory.VIOLATIONS,
            Permission.VIOLATIONS_READ,
            repository_scoped=False,
            mandatory_in_app=False,
            default_email=True,
            default_webhook=True,
            coalesce_seconds=3600,
        ),
        TypeDefinition(
            NotificationType.POLICY_APPROVAL_REQUESTED,
            "Policy approval requests",
            "A policy change is waiting for approval.",
            NotificationCategory.POLICY,
            Permission.POLICIES_APPROVE,
            repository_scoped=False,
            mandatory_in_app=False,
            default_email=True,
            default_webhook=False,
        ),
        TypeDefinition(
            NotificationType.POLICY_EMERGENCY_PUBLISHED,
            "Emergency policy publications",
            "A policy change was published without the approval workflow.",
            NotificationCategory.POLICY,
            Permission.AUDIT_READ,
            repository_scoped=False,
            mandatory_in_app=True,
            default_email=True,
            default_webhook=True,
        ),
        TypeDefinition(
            NotificationType.POLICY_ROLLOUT_FAILED,
            "Policy rollout failures",
            "A staged policy rollout was paused or rolled back by its safety thresholds.",
            NotificationCategory.POLICY,
            Permission.POLICIES_PUBLISH,
            repository_scoped=False,
            mandatory_in_app=True,
            default_email=True,
            default_webhook=True,
        ),
        TypeDefinition(
            NotificationType.POLICY_PROPAGATION_FAILED,
            "Policy propagation failures",
            "The effective policy of one or more repositories could not be updated.",
            NotificationCategory.POLICY,
            Permission.POLICIES_PUBLISH,
            repository_scoped=False,
            mandatory_in_app=True,
            default_email=True,
            default_webhook=True,
            coalesce_seconds=3600,
        ),
        TypeDefinition(
            NotificationType.EXCEPTION_REQUESTED,
            "Exception requests",
            "A policy exception for a high or critical rule is waiting for approval.",
            NotificationCategory.POLICY,
            Permission.EXCEPTIONS_APPROVE,
            repository_scoped=False,
            mandatory_in_app=False,
            default_email=True,
            default_webhook=False,
        ),
        TypeDefinition(
            NotificationType.EXCEPTION_APPROVED,
            "Exception approvals",
            "A policy exception became active and lowers enforcement until it expires.",
            NotificationCategory.POLICY,
            Permission.EXCEPTIONS_READ,
            repository_scoped=False,
            mandatory_in_app=False,
            default_email=True,
            default_webhook=True,
        ),
        TypeDefinition(
            NotificationType.EXCEPTION_EXPIRING,
            "Exceptions expiring soon",
            "An active policy exception expires soon; the policy applies again afterwards.",
            NotificationCategory.POLICY,
            Permission.EXCEPTIONS_READ,
            repository_scoped=False,
            mandatory_in_app=False,
            default_email=False,
            default_webhook=False,
        ),
        TypeDefinition(
            NotificationType.EXCEPTION_ENDED,
            "Exceptions ended",
            "A policy exception expired or was revoked: the policy applies again.",
            NotificationCategory.POLICY,
            Permission.EXCEPTIONS_READ,
            repository_scoped=False,
            mandatory_in_app=False,
            default_email=False,
            default_webhook=True,
        ),
        TypeDefinition(
            NotificationType.REPOSITORY_UNPROTECTED,
            "Repositories losing protection",
            "A repository that was protected is no longer protected by GitHub.",
            NotificationCategory.GITHUB,
            Permission.REPOSITORIES_MANAGE,
            repository_scoped=False,
            mandatory_in_app=True,
            default_email=True,
            default_webhook=True,
            coalesce_seconds=3600,
        ),
        TypeDefinition(
            NotificationType.ORGANIZATION_SETTINGS_CHANGED,
            "Security settings changes",
            "An administrator changed organization security settings.",
            NotificationCategory.POLICY,
            Permission.AUDIT_READ,
            repository_scoped=False,
            mandatory_in_app=False,
            default_email=True,
            default_webhook=True,
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
