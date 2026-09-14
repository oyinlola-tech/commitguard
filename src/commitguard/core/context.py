"""Scan context: everything a detector is allowed to see.

Detectors receive a context rather than a repository handle. This keeps them
pure (no I/O, cannot modify the repository) and trivially unit-testable.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from commitguard.git.commit import Commit


class ScanTrigger(StrEnum):
    """Why a scan is running. Detectors may adapt (e.g. pending commits)."""

    MANUAL = "manual"
    PRE_COMMIT = "pre-commit"
    COMMIT_MSG = "commit-msg"
    PRE_PUSH = "pre-push"
    CI = "ci"


class ScanContext(BaseModel):
    """Immutable input to a single detection run over one commit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    commit: Commit
    trigger: ScanTrigger = ScanTrigger.MANUAL
