"""CI enforcement service: the server-side counterpart of the pre-push hook.

Given a provider-neutral :class:`~commitguard.ci.context.CIContext` it decides

1. **which commits** the event introduces (a :class:`CommitRange`), and
2. **which policy** evaluates them (a trusted :class:`PolicySource`),

then runs the same :class:`~commitguard.services.analysis.Analyzer` as
``commitguard scan`` and the Git hooks. There is no CI-specific detection.

Trust model (see docs/github-enforcement.md):

==============================  ==========================  =========================
Event                           Commits analysed            Policy read from
==============================  ==========================  =========================
pull request / merge group      ``head ^base``              base commit's tree
push, ``before`` known          ``after ^before``           ``before`` commit's tree
push, new ref (not default)     ``after ^<default branch>`` default branch tip's tree
push, new default branch / no   ``after`` (bounded)         built-in defaults
trusted commit available
push, ref deleted               nothing                     -
==============================  ==========================  =========================

The evaluated commits' own ``.commitguard.yaml`` is never applied; a change to
it is reported as a notice and takes effect once it is on the trusted branch.
Detection rules always come from the installed CommitGuard package.
"""

from pydantic import BaseModel, ConfigDict

from commitguard.ci.context import CIContext, CIEventKind
from commitguard.config.sources import (
    PolicySource,
    PolicySourceKind,
    config_differs,
    load_policy_source,
)
from commitguard.core.context import ScanTrigger
from commitguard.git.ranges import CommitRange, require_commit, resolve_commit_range
from commitguard.git.repository import Repository
from commitguard.policies.loader import build_policy_set
from commitguard.rules.matcher import CompiledRules
from commitguard.services.analysis import Analyzer, build_report
from commitguard.services.reports import CIReport, ScanReport

DEFAULT_CI_MAX_COMMITS = 10_000


class CIPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    range: CommitRange
    policy_source: PolicySource
    config_changes: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()


class CIRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    context: CIContext
    plan: CIPlan
    report: ScanReport


def _default_branch_tip(repository: Repository, context: CIContext) -> str | None:
    if not context.default_branch:
        return None
    for ref in (
        f"refs/remotes/origin/{context.default_branch}",
        f"refs/heads/{context.default_branch}",
    ):
        tip = repository.ref_commit(ref)
        if tip is not None:
            return tip
    return None


def plan_ci(
    repository: Repository,
    context: CIContext,
    *,
    config_path: str | None = None,
    max_commits: int = DEFAULT_CI_MAX_COMMITS,
) -> CIPlan:
    notices: list[str] = []

    if context.event in (CIEventKind.PULL_REQUEST, CIEventKind.MERGE_GROUP):
        if context.base_sha is None or context.head_sha is None:  # guaranteed by CIContext
            raise ValueError("pull request context without base and head commits")
        base = require_commit(repository, context.base_sha, role="base")
        head = require_commit(repository, context.head_sha, role="head")
        commit_range = resolve_commit_range(
            repository, head, [base], base=base, max_count=max_commits
        )
        label = (
            "pull request base" if context.event is CIEventKind.PULL_REQUEST else "merge queue base"
        )
        source = PolicySource(
            kind=PolicySourceKind.REVISION,
            revision=base,
            description=label,
            config_path=config_path,
        )
        changes = tuple(config_differs(repository, base, head))
        if changes:
            notices.append(
                f"{', '.join(changes)} is changed by the evaluated commits; the policy from the "
                f"{label} was used. Policy changes take effect after they are merged."
            )
        return CIPlan(
            range=commit_range, policy_source=source, config_changes=changes, notices=tuple(notices)
        )

    # push
    if context.ref_deleted or context.after_sha is None:
        return CIPlan(
            range=CommitRange(),
            policy_source=PolicySource(kind=PolicySourceKind.BUILTIN, description="not needed"),
            notices=("ref deleted: no commits to analyse",),
        )
    if not repository.object_exists(context.after_sha):
        require_commit(repository, context.after_sha, role="pushed")  # raises a clear error
    pushed = repository.peel_to_commit(context.after_sha)
    if pushed is None:
        return CIPlan(
            range=CommitRange(),
            policy_source=PolicySource(kind=PolicySourceKind.BUILTIN, description="not needed"),
            notices=("pushed object is not a commit (e.g. a tag of a tree): nothing to analyse",),
        )

    before = repository.peel_to_commit(context.before_sha) if context.before_sha else None
    if context.before_sha and before is None:
        notices.append(
            f"previous commit {context.before_sha[:12]} is not in this clone (force push?); "
            "falling back to the default branch"
        )
    if before is not None:
        exclude = [before]
        source = PolicySource(
            kind=PolicySourceKind.REVISION,
            revision=before,
            description="commit before the push",
            config_path=config_path,
        )
        changes = tuple(config_differs(repository, before, pushed))
    else:
        is_default = bool(context.default_branch) and context.ref == (
            f"refs/heads/{context.default_branch}"
        )
        tip = None if is_default else _default_branch_tip(repository, context)
        if tip is not None:
            exclude = [tip]
            source = PolicySource(
                kind=PolicySourceKind.REVISION,
                revision=tip,
                description="default branch",
                config_path=config_path,
            )
            changes = tuple(config_differs(repository, tip, pushed))
        else:
            exclude = []
            source = PolicySource(
                kind=PolicySourceKind.BUILTIN,
                description="built-in defaults (no trusted commit available)",
                config_path=config_path,
            )
            changes = ()
            notices.append(
                "no trusted commit to read policy from or to limit the range; analysing all "
                "commits reachable from the pushed commit with built-in default policies"
            )
    if changes:
        notices.append(
            f"{', '.join(changes)} is changed by the pushed commits; the policy from the "
            f"{source.description} was used."
        )
    commit_range = resolve_commit_range(
        repository, pushed, exclude, base=exclude[0] if exclude else None, max_count=max_commits
    )
    return CIPlan(
        range=commit_range, policy_source=source, config_changes=changes, notices=tuple(notices)
    )


def run_ci(
    repository: Repository,
    context: CIContext,
    *,
    config_path: str | None = None,
    max_commits: int = DEFAULT_CI_MAX_COMMITS,
    rules: CompiledRules | None = None,
) -> CIRun:
    plan = plan_ci(repository, context, config_path=config_path, max_commits=max_commits)
    loaded = load_policy_source(repository, plan.policy_source)
    analyzer = Analyzer.create(build_policy_set(*loaded.configs), rules)
    reports = [
        analyzer.analyze(commit, ScanTrigger.CI)
        for commit in repository.read_commits(list(plan.range.commits))
    ]
    ci_report = CIReport(
        provider=context.provider.value,
        event=context.event_name,
        repository=context.repository,
        ref=context.ref,
        pull_request_number=context.pull_request_number,
        from_fork=context.from_fork,
        base_sha=plan.range.base,
        head_sha=plan.range.head,
        policy_source=str(plan.policy_source),
        config_changes=plan.config_changes,
        notices=plan.notices,
    )
    target = (
        f"{plan.range.base[:12]}..{plan.range.head[:12]}"
        if plan.range.base and plan.range.head
        else (plan.range.head[:12] if plan.range.head else "nothing")
    )
    report = build_report(
        reports,
        repository=None,
        target=target,
        trigger=ScanTrigger.CI,
        config=loaded,
        ci=ci_report,
    )
    return CIRun(context=context, plan=plan, report=report)
