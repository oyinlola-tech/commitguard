"""Audit event model.

Audit events record security-relevant actions; they are not application logs.
Each has a stable ``type``, an **actor** (CommitGuard itself, GitHub, or a
signed-in dashboard user), tenant keys (``account_id`` - the GitHub
organisation or user account - ``installation_id``, ``repository_id``),
correlation IDs and a small, validated ``data`` mapping. Values are bounded
and sanitised on creation, and events are never updated after they are stored.

Future notification channels (e-mail, Slack, webhooks) subscribe to the same
stream: :data:`SECURITY_ALERT_TYPES` lists the events that warrant one.
"""

import re
import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from commitguard.core.decision import Action
from commitguard.security.sanitization import sanitize_for_terminal
from commitguard.security.secrets import redact

MAX_DATA_ITEMS = 32
MAX_DATA_STRING = 512
_KEY_RE = re.compile(r"\A[a-z][a-z0-9_]{0,63}\Z")

type AuditValue = str | int | bool | None


class ActorType(StrEnum):
    SYSTEM = "system"  # CommitGuard acting on its own (scans, lifecycle updates)
    GITHUB = "github"  # a verified GitHub webhook
    USER = "user"  # a signed-in dashboard user


class Actor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: ActorType
    id: int | None = None
    login: str | None = Field(default=None, max_length=64)

    @classmethod
    def user(cls, user_id: int, login: str) -> "Actor":
        return cls(type=ActorType.USER, id=user_id, login=login)


SYSTEM_ACTOR = Actor(type=ActorType.SYSTEM)
GITHUB_ACTOR = Actor(type=ActorType.GITHUB)


class AuditEventType(StrEnum):
    INSTALLATION_CREATED = "installation_created"
    INSTALLATION_REMOVED = "installation_removed"
    INSTALLATION_SUSPENDED = "installation_suspended"
    INSTALLATION_UNSUSPENDED = "installation_unsuspended"
    INSTALLATION_PERMISSIONS_UPDATED = "installation_permissions_updated"
    REPOSITORIES_ADDED = "repositories_added"
    REPOSITORIES_REMOVED = "repositories_removed"
    WEBHOOK_REJECTED = "webhook_rejected"
    SCAN_QUEUED = "scan_queued"
    REPOSITORY_SCANNED = "repository_scanned"
    SCAN_PASSED = "scan_passed"
    SCAN_FAILED = "scan_failed"
    POLICY_VIOLATION = "policy_violation"
    POLICY_MODIFICATION = "policy_modification"
    CONFIGURATION_ERROR = "configuration_error"
    SCAN_ERROR = "scan_error"
    SCAN_CANCELLED = "scan_cancelled"
    AUTHORIZATION_DENIED = "authorization_denied"
    PULL_REQUEST_MERGED = "pull_request_merged"
    # Control plane (dashboard) events.
    USER_SIGNED_IN = "user_signed_in"
    USER_SIGNED_OUT = "user_signed_out"
    SESSION_REVOKED = "session_revoked"
    MEMBER_ROLE_GRANTED = "member_role_granted"
    MEMBER_ROLE_CHANGED = "member_role_changed"
    MEMBER_REMOVED = "member_removed"
    ORGANIZATION_POLICY_CHANGED = "organization_policy_changed"
    VIOLATION_OPENED = "violation_opened"
    VIOLATION_REOPENED = "violation_reopened"
    VIOLATION_RESOLVED = "violation_resolved"
    VIOLATION_ACKNOWLEDGED = "violation_acknowledged"
    VIOLATION_ACKNOWLEDGEMENT_REMOVED = "violation_acknowledgement_removed"
    REPOSITORY_MONITORING_DISABLED = "repository_monitoring_disabled"
    REPOSITORY_MONITORING_ENABLED = "repository_monitoring_enabled"
    REPOSITORIES_SYNCED = "repositories_synced"
    ENFORCEMENT_STATUS_CHECKED = "enforcement_status_checked"
    SCAN_REQUESTED = "scan_requested"


#: Events a future notification channel should deliver (not implemented yet).
SECURITY_ALERT_TYPES = frozenset(
    {
        AuditEventType.POLICY_VIOLATION,
        AuditEventType.POLICY_MODIFICATION,
        AuditEventType.ORGANIZATION_POLICY_CHANGED,
        AuditEventType.INSTALLATION_REMOVED,
        AuditEventType.INSTALLATION_SUSPENDED,
        AuditEventType.REPOSITORY_MONITORING_DISABLED,
        AuditEventType.MEMBER_ROLE_GRANTED,
        AuditEventType.MEMBER_ROLE_CHANGED,
    }
)


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    type: AuditEventType
    occurred_at: datetime
    delivery_id: str | None = None
    job_id: str | None = None
    scan_id: str | None = None
    actor_type: ActorType = ActorType.SYSTEM
    actor_id: int | None = None  # GitHub user ID for ActorType.USER
    actor_login: str | None = None
    account_id: int | None = None  # filled from the installation when stored
    installation_id: int | None = None
    repository_id: int | None = None
    repository: str | None = None
    head_sha: str | None = None
    action: Action | None = None
    data: dict[str, AuditValue] = {}

    @field_validator("data")
    @classmethod
    def _bounded(cls, value: dict[str, AuditValue]) -> dict[str, AuditValue]:
        if len(value) > MAX_DATA_ITEMS:
            raise ValueError(f"audit data has more than {MAX_DATA_ITEMS} items")
        clean: dict[str, AuditValue] = {}
        for key, item in value.items():
            if not _KEY_RE.match(key):
                raise ValueError(f"invalid audit data key {key!r}")
            if isinstance(item, str):
                item = sanitize_for_terminal(redact(item), max_length=MAX_DATA_STRING)
            clean[key] = item
        return clean
