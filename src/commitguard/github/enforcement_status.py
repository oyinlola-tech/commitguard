"""Evidence about how a repository enforces CommitGuard on GitHub.

The dashboard shows several independent signals and never merges them into
a claim they cannot support:

* **GitHub Actions** - whether a workflow on the default branch runs
  CommitGuard. The workflow files are read with the installation token
  (Contents: read) and inspected with the same static inspection
  ``commitguard doctor`` uses; nothing is executed. Reading failures give
  ``unknown``, never ``not_detected``.
* **Required check** - whether the default branch requires a CommitGuard
  check before merging. Evidence comes from two read-only endpoints available
  with the App's existing permissions:

  - ``GET /repos/{owner}/{repo}/rules/branches/{branch}``: active rulesets and
    their ``required_status_checks`` rules;
  - ``GET /repos/{owner}/{repo}/branches/{branch}``: the ``protected`` flag
    and, where GitHub includes it, the classic protection's required contexts.

  ``required`` needs a CommitGuard check context (``commitguard-app``,
  ``commitguard``) in one of those lists. ``not_required`` needs both
  endpoints to answer and show no protection that could require it (branch
  not protected and no required-status-check rule). Everything else - errors,
  a protected branch whose required contexts are not visible without
  Administration permission (which CommitGuard does not request) - is
  ``unknown``.
* **Merge queue** - ``enabled`` when an active ruleset on the default branch
  has a ``merge_queue`` rule; ``not_enabled`` when both endpoints answer, no
  ruleset requires a merge queue and the branch has no classic protection; a
  protected branch is ``unknown``, because classic branch protection settings
  (which can also require a merge queue) are not visible with the App's
  permissions.
* **Local hooks** cannot be verified by a server and are always reported as
  such.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from commitguard.github.checks import APP_CHECK_NAME
from commitguard.github.client import GitHubClient
from commitguard.github.errors import GitHubAPIError, GitHubNotFoundError
from commitguard.github.identifiers import RepositoryRef
from commitguard.github.workflow import CHECK_NAME as ACTION_CHECK_NAME
from commitguard.github.workflow import WORKFLOWS_DIR, inspect_workflow_text
from commitguard.observability.logging import get_logger
from commitguard.security.secrets import Secret

log = get_logger(__name__)

COMMITGUARD_CHECK_CONTEXTS = (APP_CHECK_NAME, ACTION_CHECK_NAME)
MAX_WORKFLOW_FILES = 50


@dataclass(frozen=True, slots=True)
class EnforcementEvidence:
    checked_at: datetime
    branch: str | None
    actions: str  # detected | not_detected | unknown
    actions_detail: str
    branch_protection: str  # required | not_required | unknown
    required_checks: tuple[str, ...]
    branch_protection_detail: str
    merge_queue: str = "unknown"  # enabled | not_enabled | unknown
    merge_queue_detail: str = "Not checked yet."


class EnforcementProbe:
    def __init__(
        self,
        client: GitHubClient,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._now = now

    def probe(
        self, token: Secret, repository: RepositoryRef, default_branch: str | None
    ) -> EnforcementEvidence:
        now = self._now()
        if not default_branch:
            return EnforcementEvidence(
                now,
                None,
                "unknown",
                "the repository has no default branch",
                "unknown",
                (),
                "the repository has no default branch",
            )
        actions, actions_detail = self._actions(token, repository, default_branch)
        protection, checks, protection_detail, queue, queue_detail = self._required_check(
            token, repository, default_branch
        )
        return EnforcementEvidence(
            now,
            default_branch,
            actions,
            actions_detail,
            protection,
            checks,
            protection_detail,
            queue,
            queue_detail,
        )

    def _actions(self, token: Secret, repository: RepositoryRef, branch: str) -> tuple[str, str]:
        directory = str(WORKFLOWS_DIR)
        try:
            entries = self._client.list_directory(token, repository, directory, branch)
        except GitHubNotFoundError:
            return "not_detected", f"no {directory} directory on {branch}"
        except GitHubAPIError as exc:
            return "unknown", f"workflows could not be read ({exc.category.value})"
        workflows = [e for e in entries if e.type == "file" and e.name.endswith((".yml", ".yaml"))][
            :MAX_WORKFLOW_FILES
        ]
        for entry in workflows:
            try:
                text = self._client.get_file_text(token, repository, entry.path, branch)
            except GitHubAPIError as exc:
                return "unknown", f"{entry.name} could not be read ({exc.category.value})"
            if text is None:
                continue
            inspection = inspect_workflow_text(Path(entry.path), text)
            if inspection is not None and inspection.check_names:
                names = ", ".join(inspection.check_names)
                return "detected", f"{entry.path} runs CommitGuard (check: {names})"
        return "not_detected", f"no workflow on {branch} runs CommitGuard"

    def _required_check(
        self, token: Secret, repository: RepositoryRef, branch: str
    ) -> tuple[str, tuple[str, ...], str, str, str]:
        rules_ok = branch_ok = False
        required: set[str] = set()
        has_status_rule = False
        has_merge_queue = False
        protected = None
        try:
            rules = self._client.get_branch_rules(token, repository, branch)
            rules_ok = True
            for rule in rules:
                if rule.type == "required_status_checks":
                    has_status_rule = True
                    required.update(rule.required_contexts)
                elif rule.type == "merge_queue":
                    has_merge_queue = True
        except GitHubAPIError as exc:
            log.info("branch_rules_unavailable", category=exc.category.value)
        try:
            info = self._client.get_branch(token, repository, branch)
            branch_ok = True
            protected = info.protected
            required.update(info.required_contexts)
        except GitHubAPIError as exc:
            log.info("branch_unavailable", category=exc.category.value)
        if has_merge_queue:
            queue, queue_detail = "enabled", f"a ruleset on {branch} requires a merge queue"
        elif rules_ok and branch_ok and not protected:
            queue, queue_detail = (
                "not_enabled",
                f"no ruleset requires a merge queue on {branch}, and it has no branch protection",
            )
        elif branch_ok and protected:
            queue, queue_detail = (
                "unknown",
                f"{branch} is protected; whether classic protection requires a merge queue is "
                "not visible with the App's permissions",
            )
        else:
            queue, queue_detail = "unknown", "merge queue settings could not be read"
        ours = tuple(sorted(c for c in required if c in COMMITGUARD_CHECK_CONTEXTS))
        if ours:
            return (
                "required",
                ours,
                f"{branch} requires {', '.join(ours)} before merging",
                queue,
                queue_detail,
            )
        if rules_ok and branch_ok and not protected and not has_status_rule:
            return (
                "not_required",
                (),
                f"{branch} is not protected and no ruleset requires status checks",
                queue,
                queue_detail,
            )
        if rules_ok and branch_ok and has_status_rule and not protected:
            return (
                "not_required",
                (),
                f"rulesets on {branch} require status checks, but not a CommitGuard check",
                queue,
                queue_detail,
            )
        if branch_ok and protected:
            return (
                "unknown",
                (),
                f"{branch} is protected, but its required checks are not visible with the "
                "App's permissions",
                queue,
                queue_detail,
            )
        return "unknown", (), "branch protection could not be read", queue, queue_detail
