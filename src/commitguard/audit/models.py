"""Audit event model.

Events are designed for future consumers (dashboards, notifications to email,
Slack, Teams or webhooks): each has a stable ``type``, tenant keys
(``installation_id``, ``repository_id``), correlation IDs and a small,
validated ``data`` mapping. Values are bounded and sanitised on creation.
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


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    type: AuditEventType
    occurred_at: datetime
    delivery_id: str | None = None
    job_id: str | None = None
    scan_id: str | None = None
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
