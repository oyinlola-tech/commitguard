"""Check output built from a report, for both GitHub integrations.

* ``build_check_output`` feeds the Actions job summary and workflow annotations.
* The Check Run types at the end of this module are what the GitHub App sends
  to the Checks API (content rendered by :mod:`commitguard.github.check_runs`).

Nothing here talks to GitHub. Findings already carry rule ID, severity,
message, evidence and commit, so a SARIF exporter can be built from the same
report later.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from commitguard.core.decision import Action
from commitguard.services.reports import ScanReport

MAX_ANNOTATIONS_PER_LEVEL = 10  # GitHub shows at most 10 errors and 10 warnings per step


class AnnotationLevel(StrEnum):
    FAILURE = "failure"
    WARNING = "warning"
    NOTICE = "notice"


class Annotation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    level: AnnotationLevel
    title: str
    message: str
    rule_id: str | None = None
    commit_sha: str | None = None


class CheckOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    conclusion: str  # "success" | "failure"
    title: str
    counts: dict[str, int]
    annotations: tuple[Annotation, ...]
    omitted_annotations: int = 0


def check_conclusion(action: Action, fail_on: Action = Action.BLOCK) -> str:
    return "failure" if action.rank >= fail_on.rank else "success"


def build_check_output(report: ScanReport, *, fail_on: Action = Action.BLOCK) -> CheckOutput:
    commits = report.commits
    counts = {
        "commits": len(commits),
        "block": sum(1 for c in commits if c.action is Action.BLOCK),
        "warn": sum(1 for c in commits if c.action is Action.WARN),
        "allow": sum(1 for c in commits if c.action is Action.ALLOW),
        "findings": sum(len(c.findings) for c in commits),
        "failures": sum(len(c.failures) for c in commits),
    }
    annotations: list[Annotation] = []
    per_level: dict[AnnotationLevel, int] = {}
    omitted = 0

    def add(annotation: Annotation) -> None:
        nonlocal omitted
        used = per_level.get(annotation.level, 0)
        if used >= MAX_ANNOTATIONS_PER_LEVEL:
            omitted += 1
            return
        per_level[annotation.level] = used + 1
        annotations.append(annotation)

    for commit in sorted(commits, key=lambda c: -c.action.rank):
        for failure in commit.failures:
            add(
                Annotation(
                    level=AnnotationLevel.FAILURE,
                    title="CommitGuard: detector failure",
                    message=f"{commit.short_sha}: {failure.failure.detector} did not complete "
                    f"({failure.failure.message}); analysis incomplete",
                    commit_sha=commit.commit_sha,
                )
            )
        for item in commit.findings:
            if item.action is Action.ALLOW:
                continue
            finding = item.finding
            level = (
                AnnotationLevel.FAILURE
                if item.action.rank >= fail_on.rank
                else AnnotationLevel.WARNING
            )
            add(
                Annotation(
                    level=level,
                    title=f"CommitGuard: {finding.title}",
                    message=(
                        f"{commit.short_sha} {commit.subject}: {finding.evidence[0].value} "
                        f"[{finding.rule_id}, {finding.severity.value}, action {item.action.value}]"
                    ),
                    rule_id=finding.rule_id,
                    commit_sha=commit.commit_sha,
                )
            )
    if report.ci is not None:
        for notice in report.ci.notices:
            add(Annotation(level=AnnotationLevel.NOTICE, title="CommitGuard", message=notice))

    conclusion = check_conclusion(report.action, fail_on)
    if conclusion == "failure":
        title = f"CommitGuard: {counts['block']} of {counts['commits']} commit(s) blocked"
        if counts["block"] == 0:
            title = f"CommitGuard: failed on warnings ({counts['warn']} commit(s))"
    elif counts["warn"]:
        title = f"CommitGuard: passed with warnings ({counts['warn']} commit(s))"
    else:
        title = f"CommitGuard: passed ({counts['commits']} commit(s) checked)"
    return CheckOutput(
        conclusion=conclusion,
        title=title,
        counts=counts,
        annotations=tuple(annotations),
        omitted_annotations=omitted,
    )


# --------------------------------------------------------------------------- #
# GitHub App Check Runs (Checks API)
# --------------------------------------------------------------------------- #
# The App publishes its own check, separate from the Action's "commitguard" job:
#
# * "commitguard-app"       - pull requests: the check to require in branch protection;
# * "commitguard-app/push"  - pushes: informational (the commits are already on GitHub).
#
# Separate names keep a push scan (only the newly pushed commits) from ever
# replacing a pull request scan (all commits of the PR) on the same SHA.
#
# Commit metadata has no file or line, so no annotations are sent: findings are
# listed in the summary and text instead of being attached to invented locations.

APP_CHECK_NAME = "commitguard-app"
APP_PUSH_CHECK_NAME = "commitguard-app/push"
MAX_CHECK_FINDINGS = 20
MAX_CHECK_TITLE_CHARS = 200
MAX_CHECK_TEXT_CHARS = 60_000  # GitHub limit: 65535 characters for summary and text


class CheckRunStatus(StrEnum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class CheckRunConclusion(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    NEUTRAL = "neutral"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    ACTION_REQUIRED = "action_required"


class CheckRunOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    summary: str
    text: str | None = None

    def to_api(self) -> dict[str, str]:
        output = {
            "title": self.title[:MAX_CHECK_TITLE_CHARS],
            "summary": _clip(self.summary),
        }
        if self.text:
            output["text"] = _clip(self.text)
        return output


def _clip(text: str) -> str:
    if len(text) <= MAX_CHECK_TEXT_CHARS:
        return text
    return text[: MAX_CHECK_TEXT_CHARS - 40] + "\n\n_Output truncated by CommitGuard._\n"


def check_run_create_payload(
    *, name: str, head_sha: str, external_id: str, output: CheckRunOutput
) -> dict[str, object]:
    return {
        "name": name,
        "head_sha": head_sha,
        "status": CheckRunStatus.QUEUED.value,
        "external_id": external_id,
        "output": output.to_api(),
    }


def check_run_update_payload(
    status: CheckRunStatus,
    output: CheckRunOutput,
    conclusion: CheckRunConclusion | None = None,
) -> dict[str, object]:
    if (status is CheckRunStatus.COMPLETED) != (conclusion is not None):
        raise ValueError("a conclusion is required exactly when the check run is completed")
    payload: dict[str, object] = {"status": status.value, "output": output.to_api()}
    if conclusion is not None:
        payload["conclusion"] = conclusion.value
    return payload
