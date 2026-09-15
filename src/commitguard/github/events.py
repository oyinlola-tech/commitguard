"""GitHub event payloads -> normalised events.

``parse_github_event`` turns an Actions event (or an App webhook for the same
event) into a :class:`~commitguard.ci.context.CIContext`; ``normalize_webhook``
additionally validates GitHub App webhooks (installation, repository identity)
and returns one of the typed events defined at the end of this module.

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
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from commitguard.ci.context import CIContext, CIEventKind, CIProvider
from commitguard.exceptions.base import CommitGuardError, UnsafeInputError
from commitguard.github.errors import WebhookValidationError
from commitguard.github.identifiers import MAX_GITHUB_ID, GitHubAccount, RepositoryRef
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


# --------------------------------------------------------------------------- #
# GitHub App webhooks -> normalised events
# --------------------------------------------------------------------------- #
# Webhook payloads for push and pull_request have the same shape as the
# Actions event payloads above, so the commit range and trust decisions are
# made by the same parser for both the GitHub Action and the GitHub App.

SUPPORTED_WEBHOOK_EVENTS = frozenset(
    {"installation", "installation_repositories", "pull_request", "push"}
)
PULL_REQUEST_SCAN_ACTIONS = frozenset({"opened", "synchronize", "reopened"})
MAX_EVENT_REPOSITORIES = 50_000


class InstallationAction(StrEnum):
    CREATED = "created"
    DELETED = "deleted"
    SUSPEND = "suspend"
    UNSUSPEND = "unsuspend"
    NEW_PERMISSIONS_ACCEPTED = "new_permissions_accepted"


class RepositoriesAction(StrEnum):
    ADDED = "added"
    REMOVED = "removed"


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InstallationEvent(_Strict):
    kind: Literal["installation"] = "installation"
    action: InstallationAction
    installation_id: int = Field(gt=0, lt=MAX_GITHUB_ID)
    account: GitHubAccount
    repository_selection: Literal["all", "selected"]
    repositories: tuple[RepositoryRef, ...] = ()
    permissions: dict[str, str] = {}


class InstallationRepositoriesEvent(_Strict):
    kind: Literal["installation_repositories"] = "installation_repositories"
    action: RepositoriesAction
    installation_id: int = Field(gt=0, lt=MAX_GITHUB_ID)
    account: GitHubAccount
    repository_selection: Literal["all", "selected"]
    added: tuple[RepositoryRef, ...] = ()
    removed: tuple[RepositoryRef, ...] = ()


class PushEvent(_Strict):
    kind: Literal["push"] = "push"
    installation_id: int = Field(gt=0, lt=MAX_GITHUB_ID)
    repository: RepositoryRef
    context: CIContext


class PullRequestEvent(_Strict):
    kind: Literal["pull_request"] = "pull_request"
    action: str = Field(pattern=r"^[a-z_]{1,64}$")
    installation_id: int = Field(gt=0, lt=MAX_GITHUB_ID)
    repository: RepositoryRef
    number: int = Field(gt=0, lt=MAX_GITHUB_ID)
    merged: bool = False
    base_changed: bool = False
    context: CIContext


class IgnoredEvent(_Strict):
    kind: Literal["ignored"] = "ignored"
    event: str
    action: str | None = None
    reason: str


GitHubWebhookEvent = (
    InstallationEvent | InstallationRepositoriesEvent | PushEvent | PullRequestEvent | IgnoredEvent
)


def _require(mapping: object, key: str, kind: type | tuple[type, ...], field: str) -> Any:
    value = mapping.get(key) if isinstance(mapping, dict) else None
    if not isinstance(value, kind) or (isinstance(value, bool) and kind is int):
        raise WebhookValidationError(f"webhook payload field {field} is missing or invalid")
    return value


def _installation_id(payload: dict[str, Any]) -> int:
    installation = _require(payload, "installation", dict, "installation")
    value = _require(installation, "id", int, "installation.id")
    if not 0 < value < MAX_GITHUB_ID:
        raise WebhookValidationError("webhook payload field installation.id is invalid")
    return int(value)


def _account(installation: dict[str, Any]) -> GitHubAccount:
    account = _require(installation, "account", dict, "installation.account")
    return GitHubAccount(
        id=_require(account, "id", int, "installation.account.id"),
        login=_require(account, "login", str, "installation.account.login"),
        type=_require(account, "type", str, "installation.account.type"),
    )


def _repository_list(payload: dict[str, Any], key: str) -> tuple[RepositoryRef, ...]:
    items = payload.get(key)
    if items is None:
        return ()
    if not isinstance(items, list) or len(items) > MAX_EVENT_REPOSITORIES:
        raise WebhookValidationError(f"webhook payload field {key} is invalid")
    return tuple(
        RepositoryRef.from_full_name(
            _require(item, "id", int, f"{key}[].id"),
            _require(item, "full_name", str, f"{key}[].full_name"),
        )
        for item in items
    )


def _event_repository(payload: dict[str, Any]) -> RepositoryRef:
    repository = _require(payload, "repository", dict, "repository")
    ref = RepositoryRef.from_full_name(
        _require(repository, "id", int, "repository.id"),
        _require(repository, "full_name", str, "repository.full_name"),
    )
    owner = repository.get("owner")
    login = owner.get("login") if isinstance(owner, dict) else None
    if repository.get("name") not in (None, ref.name) or login not in (None, ref.owner):
        raise WebhookValidationError("webhook repository name fields are inconsistent")
    return ref


def _permissions_field(installation: dict[str, Any]) -> dict[str, str]:
    raw = installation.get("permissions") or {}
    if not isinstance(raw, dict) or len(raw) > 200:
        raise WebhookValidationError("webhook payload field installation.permissions is invalid")
    return {
        k: v
        for k, v in raw.items()
        if isinstance(k, str) and isinstance(v, str) and len(k) <= 64 and len(v) <= 16
    }


def normalize_webhook(event_name: str, payload: object) -> GitHubWebhookEvent:
    """Validate a verified webhook payload and reduce it to a normalised event.

    Raises :class:`WebhookValidationError` (HTTP 400) when a supported event is
    missing mandatory fields; unsupported events are returned as
    :class:`IgnoredEvent` so GitHub is not told to retry them.
    """
    if not isinstance(payload, dict):
        raise WebhookValidationError("webhook payload must be a JSON object")
    raw_action = payload.get("action")
    action = raw_action if isinstance(raw_action, str) and len(raw_action) <= 64 else None
    if event_name not in SUPPORTED_WEBHOOK_EVENTS:
        reason = "ping" if event_name == "ping" else "event not used by CommitGuard"
        return IgnoredEvent(event=event_name, action=action, reason=reason)
    try:
        if event_name == "installation":
            if action not in set(InstallationAction):
                return IgnoredEvent(event=event_name, action=action, reason="action not used")
            installation = _require(payload, "installation", dict, "installation")
            return InstallationEvent(
                action=InstallationAction(action),
                installation_id=_installation_id(payload),
                account=_account(installation),
                repository_selection=installation.get("repository_selection", "selected"),
                repositories=_repository_list(payload, "repositories"),
                permissions=_permissions_field(installation),
            )
        if event_name == "installation_repositories":
            if action not in set(RepositoriesAction):
                return IgnoredEvent(event=event_name, action=action, reason="action not used")
            installation = _require(payload, "installation", dict, "installation")
            return InstallationRepositoriesEvent(
                action=RepositoriesAction(action),
                installation_id=_installation_id(payload),
                account=_account(installation),
                repository_selection=_require(
                    payload, "repository_selection", str, "repository_selection"
                ),
                added=_repository_list(payload, "repositories_added"),
                removed=_repository_list(payload, "repositories_removed"),
            )
        installation_id = _installation_id(payload)
        repository = _event_repository(payload)
        context = parse_github_event(event_name, payload)
        if context.repository != repository.full_name:
            raise WebhookValidationError("webhook repository fields are inconsistent")
        if event_name == "push":
            return PushEvent(
                installation_id=installation_id, repository=repository, context=context
            )
        if action is None:
            raise WebhookValidationError("webhook payload field action is missing or invalid")
        pull_request = _require(payload, "pull_request", dict, "pull_request")
        changes = payload.get("changes")
        return PullRequestEvent(
            action=action,
            installation_id=installation_id,
            repository=repository,
            number=_require(pull_request, "number", int, "pull_request.number"),
            merged=pull_request.get("merged") is True,
            base_changed=isinstance(changes, dict) and "base" in changes,
            context=context,
        )
    except GitHubEventError as exc:
        raise WebhookValidationError(str(exc)) from None
    except (ValidationError, UnsafeInputError, ValueError) as exc:
        detail = "invalid identifiers" if isinstance(exc, UnsafeInputError) else "invalid fields"
        raise WebhookValidationError(f"malformed {event_name} webhook payload ({detail})") from None
