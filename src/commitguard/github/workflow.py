"""GitHub workflow template and static inspection.

CommitGuard cannot see repository settings (branch protection, rulesets)
without the GitHub API, which Phase 4 deliberately does not use. What it can
do locally is inspect workflow files for the properties enforcement depends on.
"""

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from commitguard.exceptions.base import UnsafeInputError
from commitguard.security.safe_yaml import load_yaml_for_inspection
from commitguard.utils.filesystem import read_text_limited

WORKFLOWS_DIR = Path(".github") / "workflows"
WORKFLOW_FILE = WORKFLOWS_DIR / "commitguard.yml"
WORKFLOW_NAME = "CommitGuard"
JOB_ID = "commitguard"
#: The status check context GitHub creates for the job (the job's ``name``).
CHECK_NAME = "commitguard"
MAX_WORKFLOW_BYTES = 512 * 1024

_REPOSITORY_RE = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}\Z")
_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")
_USES_PINNED_RE = re.compile(r"\A[^@\s]+@[0-9a-f]{40}\Z")
_SECRETS_RE = re.compile(r"\$\{\{[^}]*\bsecrets\.")

# Actions used by the template, pinned to verified commits.
CHECKOUT_ACTION = "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"  # v4.2.2


def render_workflow(action_repository: str, action_ref: str) -> str:
    """Workflow for a repository that uses the CommitGuard Action.

    ``action_ref`` must be a full commit SHA: the Action is part of the security
    boundary and a movable tag or branch could be changed underneath you.
    """
    if not _REPOSITORY_RE.match(action_repository):
        raise UnsafeInputError(f"invalid action repository {action_repository!r} (owner/name)")
    if not _SHA_RE.match(action_ref):
        raise UnsafeInputError("action ref must be a full 40-character commit SHA")
    return f"""\
# CommitGuard server-side enforcement.
#
# This check only prevents merges once branch protection (or a ruleset) marks
# the "{CHECK_NAME}" status check as required. See the CommitGuard documentation:
# docs/github-enforcement.md.
name: {WORKFLOW_NAME}

on:
  pull_request:
  merge_group:
  push:

# The scanner only reads Git objects: no write access and no secrets.
permissions:
  contents: read

# No `concurrency: cancel-in-progress`: a push check that is cancelled by a later
# push would leave the earlier push's commits unchecked.

jobs:
  {JOB_ID}:
    name: {CHECK_NAME}
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - name: Check out full history
        uses: {CHECKOUT_ACTION}  # v4.2.2
        with:
          fetch-depth: 0  # base and head commits must both be present
          persist-credentials: false

      - name: CommitGuard
        uses: {action_repository}@{action_ref}
"""


class WorkflowIssueLevel(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


class WorkflowIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    level: WorkflowIssueLevel
    message: str


class WorkflowInspection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    check_names: tuple[str, ...]
    issues: tuple[WorkflowIssue, ...]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _triggers(document: dict[Any, Any]) -> set[str]:
    raw = document.get("on", document.get(True))  # YAML 1.1 parses a bare `on` as True
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {str(item) for item in raw}
    if isinstance(raw, dict):
        return {str(key) for key in raw}
    return set()


def _permission_issues(permissions: Any, where: str) -> list[WorkflowIssue]:
    if permissions is None:
        return []
    if isinstance(permissions, str):
        if permissions in ("read-all", "{}"):
            return []
        return [
            WorkflowIssue(level=WorkflowIssueLevel.WARN, message=f"{where} grants {permissions}")
        ]
    if isinstance(permissions, dict):
        writes = sorted(str(k) for k, v in permissions.items() if v == "write")
        if writes:
            return [
                WorkflowIssue(
                    level=WorkflowIssueLevel.WARN,
                    message=f"{where} grants write access ({', '.join(writes)}); "
                    "CommitGuard needs only contents: read",
                )
            ]
    return []


def _step_runs_commitguard(step: dict[Any, Any]) -> bool:
    uses = str(step.get("uses", ""))
    run = str(step.get("run", ""))
    return "commitguard" in uses.lower() or "commitguard ci github" in run


def inspect_workflow_text(path: Path, text: str) -> WorkflowInspection | None:
    """Inspect one workflow; None if it does not run CommitGuard."""
    try:
        document = load_yaml_for_inspection(text)
    except Exception as exc:  # noqa: BLE001 - any parse failure is reported, not raised
        return WorkflowInspection(
            path=path,
            check_names=(),
            issues=(
                WorkflowIssue(
                    level=WorkflowIssueLevel.FAIL, message=f"cannot parse workflow: {exc}"
                ),
            ),
        )
    if not isinstance(document, dict) or not isinstance(document.get("jobs"), dict):
        return None

    issues: list[WorkflowIssue] = []
    check_names: list[str] = []
    for job_id, job in document["jobs"].items():
        if not isinstance(job, dict):
            continue
        steps = [s for s in _as_list(job.get("steps")) if isinstance(s, dict)]
        if not any(_step_runs_commitguard(s) for s in steps):
            continue
        check_names.append(str(job.get("name", job_id)))
        issues.extend(_permission_issues(job.get("permissions"), f"job {job_id}"))
        checkout = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")]
        if not checkout:
            issues.append(
                WorkflowIssue(
                    level=WorkflowIssueLevel.FAIL,
                    message=f"job {job_id} does not check out the repository",
                )
            )
        elif not any(
            isinstance(s.get("with"), dict) and str(s["with"].get("fetch-depth")) == "0"
            for s in checkout
        ):
            issues.append(
                WorkflowIssue(
                    level=WorkflowIssueLevel.WARN,
                    message=f"job {job_id}: checkout without fetch-depth: 0; base commits may "
                    "be missing and the check will fail",
                )
            )
        for step in steps:
            uses = str(step.get("uses", ""))
            if uses and not uses.startswith("./") and not _USES_PINNED_RE.match(uses):
                issues.append(
                    WorkflowIssue(
                        level=WorkflowIssueLevel.WARN,
                        message=f"job {job_id}: {uses} is not pinned to a commit SHA",
                    )
                )
    if not check_names:
        return None

    triggers = _triggers(document)
    if "pull_request_target" in triggers:
        issues.append(
            WorkflowIssue(
                level=WorkflowIssueLevel.FAIL,
                message="uses pull_request_target; use pull_request (no secrets or write token)",
            )
        )
    if "pull_request" not in triggers:
        issues.append(
            WorkflowIssue(
                level=WorkflowIssueLevel.WARN,
                message="does not run on pull_request; pull requests are not checked",
            )
        )
    if "merge_group" not in triggers:
        issues.append(
            WorkflowIssue(
                level=WorkflowIssueLevel.OK,
                message="no merge_group trigger (required only if you use a merge queue)",
            )
        )
    concurrency = document.get("concurrency")
    if isinstance(concurrency, dict) and concurrency.get("cancel-in-progress") is True:
        issues.append(
            WorkflowIssue(
                level=WorkflowIssueLevel.WARN,
                message="concurrency cancel-in-progress can cancel a push check whose commits "
                "a later run does not re-check",
            )
        )
    if "permissions" not in document and not all(
        isinstance(j, dict) and "permissions" in j for j in document["jobs"].values()
    ):
        issues.append(
            WorkflowIssue(
                level=WorkflowIssueLevel.WARN,
                message="no permissions block; the default GITHUB_TOKEN may have write access",
            )
        )
    issues.extend(_permission_issues(document.get("permissions"), "workflow"))
    if _SECRETS_RE.search(text):
        issues.append(
            WorkflowIssue(
                level=WorkflowIssueLevel.WARN,
                message="references secrets; CommitGuard scanning needs none",
            )
        )
    return WorkflowInspection(path=path, check_names=tuple(check_names), issues=tuple(issues))


def inspect_repository_workflows(root: Path) -> list[WorkflowInspection]:
    directory = root / WORKFLOWS_DIR
    if not directory.is_dir():
        return []
    results = []
    for path in sorted(directory.iterdir()):
        if path.suffix not in (".yml", ".yaml") or not path.is_file():
            continue
        try:
            text = read_text_limited(path, max_bytes=MAX_WORKFLOW_BYTES)
        except (OSError, UnsafeInputError) as exc:
            results.append(
                WorkflowInspection(
                    path=path,
                    check_names=(),
                    issues=(WorkflowIssue(level=WorkflowIssueLevel.FAIL, message=str(exc)),),
                )
            )
            continue
        inspection = inspect_workflow_text(path, text)
        if inspection is not None:
            results.append(inspection)
    return results
