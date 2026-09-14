"""Normalised commit model.

This module is pure data: it has no Git or filesystem access, so detectors can
depend on it without depending on Git itself.

A :class:`Commit` may describe a commit that does not exist yet. During the
``commit-msg`` hook there is a message and an identity but no object ID, so
``sha`` and ``parents`` are optional.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from commitguard.provenance.author import Identity
from commitguard.security.validation import validate_git_sha


class Commit(BaseModel):
    """A normalised, immutable view of a Git commit.

    All string fields hold *untrusted* data exactly as recorded in the commit.
    Sanitise before displaying (see :mod:`commitguard.security.sanitization`).

    TODO(phase-2): add ``trailers`` populated by
        :func:`commitguard.provenance.trailers.parse_trailers`.
    TODO(phase-3): add diff metadata from :mod:`commitguard.git.diff`.
    TODO(phase-5): add ``signature`` (:class:`~commitguard.provenance.signatures.SignatureInfo`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sha: str | None = Field(default=None, description="Object ID; None for a pending commit")
    parents: tuple[str, ...] = ()
    author: Identity
    committer: Identity
    authored_at: datetime | None = None
    committed_at: datetime | None = None
    message: str

    @field_validator("sha")
    @classmethod
    def _validate_sha(cls, value: str | None) -> str | None:
        return None if value is None else validate_git_sha(value)

    @field_validator("parents")
    @classmethod
    def _validate_parents(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for parent in value:
            validate_git_sha(parent)
        return value

    @property
    def is_pending(self) -> bool:
        """True if this commit has not been written to the object database."""
        return self.sha is None

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1

    @property
    def subject(self) -> str:
        """First line of the message (untrusted; sanitise before display)."""
        return self.message.split("\n", 1)[0]
