"""Findings and scan results.

A :class:`Finding` is a detector's structured, explainable claim that a commit
matched a rule. It never contains a decision - that is the policy's job.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from commitguard.security.hashing import fingerprint
from commitguard.security.validation import validate_git_sha, validate_identifier


class Severity(StrEnum):
    """How serious a finding is, independent of the action a policy takes."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {severity: index for index, severity in enumerate(Severity)}


class Evidence(BaseModel):
    """The exact data that triggered a finding, preserved for explanation/audit.

    ``value`` is untrusted raw data; it must be sanitised before display.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1, description="Where it was found, e.g. 'trailer:co-authored-by'")
    value: str
    line_number: int | None = Field(default=None, ge=1)


class Finding(BaseModel):
    """A structured security finding produced by a detector."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    detector: str
    rule: str
    severity: Severity
    message: str = Field(min_length=1)
    evidence: tuple[Evidence, ...] = Field(min_length=1)
    commit_sha: str | None = None
    remediation: str = Field(min_length=1)

    @field_validator("detector")
    @classmethod
    def _validate_detector(cls, value: str) -> str:
        return validate_identifier(value, kind="detector name")

    @field_validator("rule")
    @classmethod
    def _validate_rule(cls, value: str) -> str:
        return validate_identifier(value, kind="rule id")

    @field_validator("commit_sha")
    @classmethod
    def _validate_commit_sha(cls, value: str | None) -> str | None:
        return None if value is None else validate_git_sha(value)

    @property
    def fingerprint(self) -> str:
        """Stable ID: same detector, rule, commit and evidence => same fingerprint."""
        parts = [self.detector, self.rule, self.commit_sha or ""]
        for item in self.evidence:
            parts.extend([item.source, item.value, str(item.line_number or "")])
        return fingerprint(parts)


class DetectorFailure(BaseModel):
    """A detector that raised or misbehaved. Evaluated fail-closed by policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    detector: str
    error_type: str
    message: str


class ScanResult(BaseModel):
    """Everything the engine produced for one scan context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    commit_sha: str | None
    detectors_run: tuple[str, ...]
    findings: tuple[Finding, ...] = ()
    failures: tuple[DetectorFailure, ...] = ()

    @property
    def complete(self) -> bool:
        """True if every detector ran successfully."""
        return not self.failures
