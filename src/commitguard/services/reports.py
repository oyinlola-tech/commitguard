"""Structured, serialisable analysis reports.

Reports are the stable output contract for the CLI's JSON format and the
future audit log. They contain commit IDs, findings, evidence (concise
metadata only) and decisions - never file contents or full commit messages.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, computed_field

from commitguard.core.decision import Action, Decision
from commitguard.core.result import DetectionResult, DetectorFailure, Finding

REPORT_SCHEMA_VERSION: Literal[1] = 1


class EvaluatedFinding(BaseModel):
    """A finding together with the policy outcome applied to it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    finding: Finding
    action: Action
    policy_id: str | None
    reason: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fingerprint(self) -> str:
        return self.finding.fingerprint


class EvaluatedFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    failure: DetectorFailure
    action: Action
    reason: str


class CommitReport(BaseModel):
    """Detection + decision for one commit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    commit_sha: str | None
    short_sha: str
    action: Action
    findings: tuple[EvaluatedFinding, ...] = ()
    failures: tuple[EvaluatedFailure, ...] = ()
    detectors_run: tuple[str, ...] = ()
    detectors_skipped: tuple[str, ...] = ()

    @classmethod
    def build(
        cls, short_sha: str, detection: DetectionResult, decision: Decision
    ) -> "CommitReport":
        findings: list[EvaluatedFinding] = []
        failures: list[EvaluatedFailure] = []
        for explanation in decision.explanations:
            if explanation.finding is not None:
                findings.append(
                    EvaluatedFinding(
                        finding=explanation.finding,
                        action=explanation.action,
                        policy_id=explanation.policy_id,
                        reason=explanation.reason,
                    )
                )
            elif explanation.failure is not None:
                failures.append(
                    EvaluatedFailure(
                        failure=explanation.failure,
                        action=explanation.action,
                        reason=explanation.reason,
                    )
                )
        return cls(
            commit_sha=detection.commit_sha,
            short_sha=short_sha,
            action=decision.action,
            findings=tuple(findings),
            failures=tuple(failures),
            detectors_run=detection.detectors_run,
            detectors_skipped=detection.detectors_skipped,
        )


class ScanReport(BaseModel):
    """Result of analysing one or more commits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = REPORT_SCHEMA_VERSION
    tool_version: str
    generated_at: datetime
    repository: str | None
    target: str
    trigger: str
    config_sources: tuple[str, ...]
    action: Action
    commits: tuple[CommitReport, ...]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def summary(self) -> dict[str, int]:
        counts = {action.value: 0 for action in Action}
        for commit in self.commits:
            actions = [f.action for f in commit.findings] + [f.action for f in commit.failures]
            for action in actions:
                counts[action.value] += 1
        return {"commits": len(self.commits), **counts}
