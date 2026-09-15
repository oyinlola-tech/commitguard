"""Commit ranges: which commits an operation introduces.

One abstraction serves every enforcement point:

* ``pre-push``      - head = pushed commit, exclude = remote's old commit + tracking refs
* pull requests     - head = PR head, exclude = PR base
* push events (CI)  - head = ``after``, exclude = ``before`` (or the default branch)
* ``scan a..b``     - head = b, exclude = a

The selected commits are those reachable from ``head`` but from none of the
``exclude`` commits (``git rev-list head ^exclude...``), oldest first. Merge
commits are included like any other commit; history reachable from an excluded
commit is never walked into the result.
"""

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from commitguard.exceptions.git import GitError
from commitguard.git.repository import Repository


class MissingCommitError(GitError):
    """A commit needed to compute a range is not present in the local clone."""


class CommitRange(BaseModel):
    """A resolved range. ``head=None`` means nothing to analyse (e.g. deletion)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base: str | None = None  # primary excluded commit, for display (PR base, push before)
    head: str | None = None
    exclude: tuple[str, ...] = ()
    commits: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.commits


def resolve_commit_range(
    repository: Repository,
    head: str | None,
    exclude: Iterable[str] = (),
    *,
    base: str | None = None,
    max_count: int,
) -> CommitRange:
    """Resolve the commits reachable from ``head`` but not from any ``exclude``.

    ``head`` and ``exclude`` must be full commit IDs present in the repository;
    use :func:`require_commit` first for IDs from untrusted sources.
    """
    if head is None:
        return CommitRange(base=base)
    excluded = tuple(sorted(set(exclude)))
    commits = repository.rev_list([head], excluded, max_count=max_count)
    return CommitRange(base=base, head=head, exclude=excluded, commits=tuple(commits))


def require_commit(repository: Repository, oid: str, *, role: str) -> str:
    """Peel ``oid`` (commit or annotated tag) to a commit, or raise a clear error."""
    commit = repository.peel_to_commit(oid)
    if commit is None:
        raise MissingCommitError(
            f"{role} commit {oid} is not available in this clone. "
            "Fetch the full history (for actions/checkout: fetch-depth: 0)."
        )
    return commit
