"""Staged and committed change inspection.

TODO(phase-3): implement read-only diff inspection for the ``pre-commit`` and
``pre-push`` hooks, e.g. ``git diff --cached --name-status -z`` for staged
paths and ``git diff-tree`` for pushed commits. Requirements:

* NUL-delimited output only (paths may contain newlines or escape codes);
* binary-safe, size-bounded reads of blob content (for future secret detection);
* no content ever leaves the machine unless a policy explicitly opts in.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ChangeType(StrEnum):
    """Git name-status change codes."""

    ADDED = "A"
    COPIED = "C"
    DELETED = "D"
    MODIFIED = "M"
    RENAMED = "R"
    TYPE_CHANGED = "T"
    UNMERGED = "U"


class FileChange(BaseModel):
    """A single changed path. Not populated yet (Phase 3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    change: ChangeType
    old_path: str | None = None
