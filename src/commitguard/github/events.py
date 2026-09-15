"""GitHub Actions event payloads -> :class:`~commitguard.ci.context.CIContext`.

Only the fields CommitGuard needs are read; raw JSON never travels further
into the application. Payloads are untrusted input: SHAs are validated, ref
names checked for control characters, and anything unexpected is an error
(the check fails closed).

Supported events:

* ``pull_request``  - base/head SHAs from ``pull_request.base.sha`` / ``.head.sha``.
  These are the PR's real commits; GitHub's synthetic ``refs/pull/N/merge``
  commit that ``actions/checkout`` checks out is never analysed.
* ``merge_group``   - merge queue: ``merge_group.base_sha`` / ``.head_sha``.
* ``push``          - ``before`` / ``after`` (all-zero = new / deleted ref).

``pull_request_target`` is rejected: it runs with a privileged token in the
context of the base repository, and CommitGuard never needs that.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from commitguard.ci.context import CIContext, CIEventKind, CIProvider
from commitguard.exceptions.base import CommitGuardError
from commitguard.utils.filesystem import read_bytes_limited

MAX_EVENT_BYTES = 64 * 1024 * 1024
ZERO_OID_LENGTHS = (40, 64)


class GitHubEventError(CommitGuardError):
    """The GitHub event is unsupported, missing or malformed."""


def _is_zero(oid: str) -> bool:
    return len(oid) in ZERO_OID_LENGTHS and set(oid) == {"0"}


def _clean_ref(ref: object, field: str) -> str | None:
    if ref is None:
        return None
    if not isinstance(ref, str) or not ref or len(ref) > 4096:
        raise GitHubEventError(f"event field {field} is not a valid ref name")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in ref):
        raise GitHubEventError(f"event field {field} contains control characters")
    return ref


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _Repo(_Loose):
    full_name: str | None = None
    default_branch: str | None = None


class _PRSide(_Loose):
    sha: str
    ref: str | None = None
    repo: _Repo | None = None


class _PullRequest(_Loose):
    number: int
    base: _PRSide
    head: _PRSide


class GitHubPullRequestContext(_Loose):
    pull_request: _PullRequest
    repository: _Repo | None = None


class _MergeGroup(_Loose):
    base_sha: str
    head_sha: str
    base_ref: str | None = None


class GitHubMergeGroupContext(_Loose):
    merge_group: _MergeGroup
    repository: _Repo | None = None


class GitHubPushContext(_Loose):
    ref: str
    before: str
    after: str
    deleted: bool = False
    repository: _Repo | None = None


def _repo_fields(repo: _Repo | None) -> dict[str, Any]:
    if repo is None:
        return {}
    return {
        "repository": _clean_ref(repo.full_name, "repository.full_name"),
        "default_branch": _clean_ref(repo.default_branch, "repository.default_branch"),
    }


def parse_github_event(event_name: str, payload: object) -> CIContext:
    """Normalise a GitHub event payload."""
    if not isinstance(payload, dict):
        raise GitHubEventError("event payload must be a JSON object")
    try:
        if event_name == "pull_request":
            pr_event = GitHubPullRequestContext.model_validate(payload)
            pr = pr_event.pull_request
            base_repo = pr.base.repo.full_name if pr.base.repo else None
            head_repo = pr.head.repo.full_name if pr.head.repo else None
            return CIContext(
                provider=CIProvider.GITHUB,
                event=CIEventKind.PULL_REQUEST,
                event_name=event_name,
                ref=_clean_ref(f"refs/heads/{pr.base.ref}" if pr.base.ref else None, "base.ref"),
                base_sha=pr.base.sha,
                head_sha=pr.head.sha,
                pull_request_number=pr.number,
                # A deleted head repository (head.repo == null) is treated as a fork.
                from_fork=head_repo is None or head_repo != base_repo,
                **_repo_fields(pr_event.repository),
            )
        if event_name == "merge_group":
            mg_event = GitHubMergeGroupContext.model_validate(payload)
            mg = mg_event.merge_group
            return CIContext(
                provider=CIProvider.GITHUB,
                event=CIEventKind.MERGE_GROUP,
                event_name=event_name,
                ref=_clean_ref(mg.base_ref, "merge_group.base_ref"),
                base_sha=mg.base_sha,
                head_sha=mg.head_sha,
                **_repo_fields(mg_event.repository),
            )
        if event_name == "push":
            push = GitHubPushContext.model_validate(payload)
            deleted = _is_zero(push.after)
            if push.deleted != deleted:
                raise GitHubEventError("push event: `deleted` contradicts the `after` commit")
            return CIContext(
                provider=CIProvider.GITHUB,
                event=CIEventKind.PUSH,
                event_name=event_name,
                ref=_clean_ref(push.ref, "ref"),
                before_sha=None if _is_zero(push.before) else push.before,
                after_sha=None if deleted else push.after,
                ref_deleted=deleted,
                **_repo_fields(push.repository),
            )
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise GitHubEventError(
            f"malformed {event_name} event payload (fields: {', '.join(fields) or '?'})"
        ) from exc
    if event_name == "pull_request_target":
        raise GitHubEventError(
            "pull_request_target is not supported: use the pull_request event "
            "(CommitGuard needs no secrets or write access)"
        )
    raise GitHubEventError(
        f"unsupported GitHub event {event_name!r} (supported: pull_request, push, merge_group)"
    )


def load_github_event(event_name: str | None, event_path: Path | None) -> CIContext:
    """Read ``GITHUB_EVENT_NAME`` / ``GITHUB_EVENT_PATH`` style inputs."""
    if not event_name:
        raise GitHubEventError("GitHub event name missing (GITHUB_EVENT_NAME / --event-name)")
    if event_path is None:
        raise GitHubEventError("GitHub event payload missing (GITHUB_EVENT_PATH / --event-path)")
    try:
        raw = read_bytes_limited(event_path, max_bytes=MAX_EVENT_BYTES)
        payload = json.loads(raw.decode("utf-8"))
    except FileNotFoundError as exc:
        raise GitHubEventError(f"event payload file not found: {event_path}") from exc
    except (OSError, ValueError, CommitGuardError) as exc:
        raise GitHubEventError(f"event payload could not be read as JSON: {exc}") from exc
    return parse_github_event(event_name, payload)
