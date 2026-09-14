"""Normalised commit model.

This module is pure data: it has no Git or filesystem access, so detectors can
depend on it without depending on Git itself.

A :class:`Commit` may describe a commit that does not exist yet. During a
``commit-msg`` check there is a message and an identity but no object ID, so
``sha`` and ``parents`` are optional.

Trailers are always *derived* from ``message`` by
:func:`~commitguard.provenance.trailers.parse_trailers`; they cannot be passed
in separately, so they can never disagree with the message they came from.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from commitguard.provenance.author import Identity
from commitguard.provenance.signatures import SignatureInfo
from commitguard.provenance.trailers import Trailer, parse_trailers
from commitguard.security.validation import validate_git_sha

SHORT_SHA_LENGTH = 7


class Commit(BaseModel):
    """A normalised, immutable view of a Git commit.

    All string fields hold *untrusted* data exactly as recorded in the commit.
    Sanitise before displaying (see :mod:`commitguard.security.sanitization`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sha: str | None = Field(default=None, description="Object ID; None for a pending commit")
    parents: tuple[str, ...] = ()
    author: Identity
    committer: Identity
    authored_at: datetime | None = None
    committed_at: datetime | None = None
    message: str
    trailers: tuple[Trailer, ...] = Field(default=(), description="Derived from message")
    trailers_truncated: bool = Field(
        default=False, description="True if the message had more trailers than can be analysed"
    )
    signature: SignatureInfo | None = Field(
        default=None, description="Not collected yet (Phase 5); None means unknown"
    )

    @model_validator(mode="before")
    @classmethod
    def _derive_trailers(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("message"), str):
            parsed = parse_trailers(data["message"])
            data = {**data, "trailers": parsed.trailers, "trailers_truncated": parsed.truncated}
        return data

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
    def short_sha(self) -> str:
        return self.sha[:SHORT_SHA_LENGTH] if self.sha else "pending"

    @property
    def timestamp(self) -> datetime | None:
        """Commit time (committer date), falling back to the author date."""
        return self.committed_at or self.authored_at

    @property
    def subject(self) -> str:
        """First line of the message (untrusted; sanitise before display)."""
        return self.message.split("\n", 1)[0]

    def trailers_with_key(self, normalized_key: str) -> tuple[Trailer, ...]:
        return tuple(t for t in self.trailers if t.normalized_key == normalized_key)
