"""Audit record models.

TODO(future): finalise the schema together with :mod:`commitguard.audit.storage`.
Records must store sanitised evidence and finding fingerprints, never
environment variables, tokens, or file contents.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from commitguard.core.context import ScanTrigger
from commitguard.core.decision import Action


class AuditEvent(BaseModel):
    """One evaluated scan. Not persisted anywhere yet."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    occurred_at: datetime
    trigger: ScanTrigger
    commit_sha: str | None
    action: Action
    finding_fingerprints: tuple[str, ...] = ()
