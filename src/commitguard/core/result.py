"""Findings and detection results.

A :class:`Finding` is a detector's structured, explainable claim that commit
*metadata* matched a rule. It never contains a decision (that is the policy's
job) and never claims that code was written by an AI - only that the commit
contains an identity or attribution associated with one.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from commitguard.security.hashing import fingerprint
from commitguard.security.validation import validate_git_sha, validate_identifier

MAX_EVIDENCE_CHARS = 512
_TRUNCATION_MARKER = "...[truncated]"


class Severity(StrEnum):
    """How serious a finding is, independent of the action a policy takes."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return list(Severity).index(self)


class Confidence(StrEnum):
    """How strong the evidence behind a finding is."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return list(Confidence).index(self)


class MatchKind(StrEnum):
    """Which piece of an identity matched a rule."""

    NAME = "name"  # full name equals a configured alias
    NAME_PREFIX = "name_prefix"  # name starts with a configured distinctive prefix
    AMBIGUOUS_NAME = "ambiguous_name"  # alias that is also a common human name
    EMAIL = "email"  # exact configured address
    GITHUB_LOGIN = "github_login"  # login from a GitHub noreply address or name
    AUTOMATION_EMAIL = "automation_email"  # automation local part at a vendor domain
    VENDOR_DOMAIN = "vendor_domain"  # vendor domain with a non-automation local part
    TRAILER_KEY = "trailer_key"  # trailer key configured as attribution
    MESSAGE_MARKER = "message_marker"  # exact attribution line inserted by a tool


class MatchReason(BaseModel):
    """One reason a rule matched, with a reference to the rule that matched."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: MatchKind
    value: str = Field(description="The normalised value that matched")
    rule: str = Field(description="Rule reference, e.g. 'ai-identities.yaml#claude'")


class EvidenceSource(StrEnum):
    COAUTHOR_TRAILER = "coauthor_trailer"
    TRAILER = "trailer"
    AUTHOR = "author"
    COMMITTER = "committer"
    MESSAGE = "message"

    @property
    def label(self) -> str:
        return _SOURCE_LABELS[self]


_SOURCE_LABELS = {
    EvidenceSource.COAUTHOR_TRAILER: "Co-authored-by trailer",
    EvidenceSource.TRAILER: "commit trailer",
    EvidenceSource.AUTHOR: "commit author",
    EvidenceSource.COMMITTER: "commit committer",
    EvidenceSource.MESSAGE: "commit message line",
}


class Evidence(BaseModel):
    """The concise metadata that triggered a finding (never file contents).

    ``value`` is untrusted raw data, truncated to :data:`MAX_EVIDENCE_CHARS`;
    it must be sanitised before display.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: EvidenceSource
    value: str
    line_number: int | None = Field(default=None, ge=1)
    matched: tuple[MatchReason, ...] = ()
    notes: tuple[str, ...] = Field(default=(), description="e.g. malformed-trailer issues")

    @field_validator("value")
    @classmethod
    def _truncate(cls, value: str) -> str:
        if len(value) > MAX_EVIDENCE_CHARS:
            return value[: MAX_EVIDENCE_CHARS - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
        return value


class Finding(BaseModel):
    """A structured security finding produced by a detector."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    detector: str
    rule_id: str
    severity: Severity
    confidence: Confidence
    title: str = Field(min_length=1)
    message: str = Field(min_length=1)
    evidence: tuple[Evidence, ...] = Field(min_length=1)
    commit_sha: str | None = None
    remediation: str = Field(min_length=1)

    @field_validator("detector")
    @classmethod
    def _validate_detector(cls, value: str) -> str:
        return validate_identifier(value, kind="detector name")

    @field_validator("rule_id")
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
        parts = [self.detector, self.rule_id, self.commit_sha or ""]
        for item in self.evidence:
            parts.extend([item.source.value, item.value, str(item.line_number or "")])
        return fingerprint(parts)


class DetectorFailure(BaseModel):
    """A detector that raised or misbehaved. Evaluated fail-closed by policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    detector: str
    error_type: str
    message: str


class DetectionResult(BaseModel):
    """Everything the engine produced for one commit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    commit_sha: str | None
    detectors_run: tuple[str, ...]
    detectors_skipped: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()
    failures: tuple[DetectorFailure, ...] = ()

    @property
    def complete(self) -> bool:
        """True if every detector that ran completed successfully."""
        return not self.failures
