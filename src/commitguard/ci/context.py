"""Normalised CI event context. Contains only what CommitGuard needs."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from commitguard.security.validation import validate_git_sha


class CIProvider(StrEnum):
    GITHUB = "github"


class CIEventKind(StrEnum):
    PULL_REQUEST = "pull_request"  # changes proposed for a base branch
    PUSH = "push"  # commits that already reached the server
    MERGE_GROUP = "merge_group"  # merge queue candidate


def _oid(value: str | None) -> str | None:
    return None if value is None else validate_git_sha(value)


class CIContext(BaseModel):
    """What changed, according to the CI provider.

    SHAs are full object IDs. For pushes, ``before_sha``/``after_sha`` are None
    when Git reports the all-zero ID (new ref / deleted ref).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: CIProvider
    event: CIEventKind
    event_name: str = Field(description="Provider's own event name, for display")
    repository: str | None = None  # e.g. "owner/name"
    ref: str | None = None  # fully qualified ref that changed / PR base ref
    default_branch: str | None = None
    base_sha: str | None = None
    head_sha: str | None = None
    before_sha: str | None = None
    after_sha: str | None = None
    pull_request_number: int | None = Field(default=None, ge=1)
    from_fork: bool = False
    ref_deleted: bool = False

    @field_validator("base_sha", "head_sha", "before_sha", "after_sha")
    @classmethod
    def _valid_oids(cls, value: str | None) -> str | None:
        return _oid(value)

    @model_validator(mode="after")
    def _consistent(self) -> "CIContext":
        needs_both = self.event in (CIEventKind.PULL_REQUEST, CIEventKind.MERGE_GROUP)
        if needs_both and (self.base_sha is None or self.head_sha is None):
            raise ValueError(f"{self.event.value} requires base and head commits")
        push_mismatch = self.ref_deleted != (self.after_sha is None)
        if self.event is CIEventKind.PUSH and push_mismatch:
            raise ValueError("push: after commit must be absent exactly when the ref is deleted")
        return self
