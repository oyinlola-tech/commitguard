"""Check-run shaped output built from a report.

This is the data a future GitHub Checks API integration (or a GitHub App)
would send: a title, a Markdown summary and annotations. Today it feeds the
Actions job summary and workflow annotations; nothing here talks to GitHub.
Findings already carry rule ID, severity, message, evidence and commit, so a
SARIF exporter can be built from the same report later.
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
