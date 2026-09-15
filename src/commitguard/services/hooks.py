"""Git hook runtime: what ``commitguard hook <name>`` does.

Hooks are enforcement points only. Every stage builds commits from Git data
and runs the same :class:`~commitguard.services.analysis.Analyzer` used by
``scan``/``check``.

=============  ===========================================  ==================
Stage          Input                                         What is analysed
=============  ===========================================  ==================
pre-commit     pending author/committer (``git var``)        identity rules
commit-msg     message file after Git's default cleanup      message + identity
               plus pending author/committer
pre-push       ref updates on stdin                          every outgoing commit
=============  ===========================================  ==================

commit-msg sees the message *before* Git creates the commit object; options
such as ``--cleanup=verbatim`` are invisible to it. pre-push analyses the real
commit objects and is the authoritative local check.
"""

from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from commitguard.config.enforcement import Enforcement, build_enforcement
from commitguard.config.loader import LoadedConfig
from commitguard.core.context import ScanTrigger
from commitguard.exceptions.git import GitError
from commitguard.git.push import PushUpdate, parse_pre_push_input
from commitguard.git.repository import Repository
from commitguard.services.analysis import (
    MAX_MESSAGE_FILE_BYTES,
    Analyzer,
    build_report,
    load_analyzer,
    pending_commit,
)
from commitguard.services.reports import ScanReport
from commitguard.utils.filesystem import read_bytes_limited


class HookName(StrEnum):
    PRE_COMMIT = "pre-commit"
    COMMIT_MSG = "commit-msg"
    PRE_PUSH = "pre-push"


class UpdateDisposition(StrEnum):
    SCANNED = "scanned"
    DELETED = "deleted"  # nothing to analyse
    NON_COMMIT = "non_commit"  # e.g. tag pointing at a tree or blob
    UP_TO_DATE = "up_to_date"  # no commits the remote does not already have


class UpdatePlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    update: PushUpdate
    disposition: UpdateDisposition
    commits: tuple[str, ...] = ()


class HookRun(BaseModel):
    """Outcome of one hook invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hook: HookName
    enabled: bool
    report: ScanReport | None = None
    updates: tuple[UpdatePlan, ...] = ()
    remote: str | None = None


def _setup(
    repository: Repository, config_path: Path | None
) -> tuple[Analyzer, LoadedConfig, Enforcement]:
    analyzer, loaded = load_analyzer(repository, config_path=config_path)
    return analyzer, loaded, build_enforcement(*loaded.configs)


def run_pre_commit(repository: Repository, *, config_path: Path | None = None) -> HookRun:
    analyzer, loaded, enforcement = _setup(repository, config_path)
    if not enforcement.pre_commit:
        return HookRun(hook=HookName.PRE_COMMIT, enabled=False)
    author, committer = repository.pending_identities()
    # No message exists yet: only identity-based rules can match.
    report = analyzer.analyze(pending_commit("", author, committer), ScanTrigger.PRE_COMMIT)
    return HookRun(
        hook=HookName.PRE_COMMIT,
        enabled=True,
        report=build_report(
            [report],
            repository=repository,
            target="pending commit",
            trigger=ScanTrigger.PRE_COMMIT,
            config=loaded,
        ),
    )


def run_commit_msg(
    repository: Repository, message_file: Path, *, config_path: Path | None = None
) -> HookRun:
    analyzer, loaded, enforcement = _setup(repository, config_path)
    if not enforcement.commit_msg:
        return HookRun(hook=HookName.COMMIT_MSG, enabled=False)
    raw = read_bytes_limited(message_file, max_bytes=MAX_MESSAGE_FILE_BYTES)
    message = repository.cleanup_message(raw.decode("utf-8", errors="replace"))
    author, committer = repository.pending_identities()
    report = analyzer.analyze(pending_commit(message, author, committer), ScanTrigger.COMMIT_MSG)
    return HookRun(
        hook=HookName.COMMIT_MSG,
        enabled=True,
        report=build_report(
            [report],
            repository=repository,
            target="pending commit message",
            trigger=ScanTrigger.COMMIT_MSG,
            config=loaded,
        ),
    )


def plan_push(
    repository: Repository,
    updates: Sequence[PushUpdate],
    remote: str,
    *,
    max_commits: int,
) -> tuple[list[UpdatePlan], list[str]]:
    """Work out which commits a push would introduce, deduplicated, oldest first.

    For each update the commits reachable from the local object but not from
    the remote's current object (when present locally) or from any of the
    remote's tracking refs are selected. That covers new branches (all-zero
    remote object) without rescanning history the remote already has, and
    force pushes (only commits the remote lacks). Deletions have no outgoing
    commits; tags are peeled to the commit they reference.
    """
    known_remote = repository.remote_tracking_tips(remote)
    plans: list[UpdatePlan] = []
    ordered: dict[str, None] = {}
    for update in updates:
        if update.is_delete:
            plans.append(UpdatePlan(update=update, disposition=UpdateDisposition.DELETED))
            continue
        commit = repository.peel_to_commit(update.local_oid)
        if commit is None:
            plans.append(UpdatePlan(update=update, disposition=UpdateDisposition.NON_COMMIT))
            continue
        exclude = set(known_remote)
        if not update.is_new_ref:
            remote_commit = repository.peel_to_commit(update.remote_oid)
            if remote_commit is not None:
                exclude.add(remote_commit)
        commits = repository.rev_list([commit], sorted(exclude), max_count=max_commits)
        for sha in commits:
            ordered.setdefault(sha, None)
        if len(ordered) > max_commits:
            raise GitError(f"push would introduce more than {max_commits} commits")
        plans.append(
            UpdatePlan(
                update=update,
                disposition=UpdateDisposition.SCANNED if commits else UpdateDisposition.UP_TO_DATE,
                commits=tuple(commits),
            )
        )
    return plans, list(ordered)


def run_pre_push(
    repository: Repository,
    remote: str,
    stdin_text: str,
    *,
    config_path: Path | None = None,
) -> HookRun:
    analyzer, loaded, enforcement = _setup(repository, config_path)
    if not enforcement.pre_push:
        return HookRun(hook=HookName.PRE_PUSH, enabled=False, remote=remote)
    updates = parse_pre_push_input(stdin_text)
    plans, shas = plan_push(repository, updates, remote, max_commits=enforcement.max_push_commits)
    reports = [
        analyzer.analyze(commit, ScanTrigger.PRE_PUSH) for commit in repository.read_commits(shas)
    ]
    return HookRun(
        hook=HookName.PRE_PUSH,
        enabled=True,
        remote=remote,
        updates=tuple(plans),
        report=build_report(
            reports,
            repository=repository,
            target=f"push to {remote}",
            trigger=ScanTrigger.PRE_PUSH,
            config=loaded,
        ),
    )
