import pytest
from pydantic import ValidationError

from commitguard.core.result import (
    MAX_EVIDENCE_CHARS,
    Confidence,
    Evidence,
    EvidenceSource,
    Finding,
    Severity,
)

SHA = "a" * 40


def make_finding(**overrides: object) -> Finding:
    data: dict[str, object] = {
        "detector": "coauthor",
        "rule_id": "ai_coauthor",
        "severity": Severity.HIGH,
        "confidence": Confidence.HIGH,
        "title": "AI coauthor detected",
        "message": "An AI agent was identified as a commit coauthor.",
        "evidence": (
            Evidence(
                source=EvidenceSource.COAUTHOR_TRAILER,
                value="Claude <noreply@anthropic.com>",
                line_number=3,
            ),
        ),
        "commit_sha": SHA,
        "remediation": "Remove the AI attribution before pushing this commit.",
    }
    data.update(overrides)
    return Finding.model_validate(data)


def test_finding_matches_documented_shape() -> None:
    finding = make_finding()
    assert finding.detector == "coauthor"
    assert finding.rule_id == "ai_coauthor"
    assert finding.severity is Severity.HIGH
    assert finding.title == "AI coauthor detected"
    assert finding.evidence[0].value == "Claude <noreply@anthropic.com>"


def test_finding_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        make_finding(evidence=())


@pytest.mark.parametrize("field", ["title", "message", "remediation"])
def test_finding_requires_explanation_text(field: str) -> None:
    with pytest.raises(ValidationError):
        make_finding(**{field: ""})


@pytest.mark.parametrize("rule", ["AI_COAUTHOR", "ai-coauthor", "", "1rule", "x" * 65])
def test_finding_rejects_invalid_rule_ids(rule: str) -> None:
    with pytest.raises(ValidationError):
        make_finding(rule_id=rule)


def test_finding_rejects_invalid_sha() -> None:
    with pytest.raises(ValidationError):
        make_finding(commit_sha="HEAD")


def test_finding_is_immutable() -> None:
    finding = make_finding()
    with pytest.raises(ValidationError):
        finding.rule_id = "bot_identity"  # type: ignore[misc]


def test_evidence_is_truncated() -> None:
    evidence = Evidence(source=EvidenceSource.MESSAGE, value="A" * 10_000)
    assert len(evidence.value) == MAX_EVIDENCE_CHARS
    assert evidence.value.endswith("[truncated]")


def test_fingerprint_is_stable_and_evidence_sensitive() -> None:
    assert make_finding().fingerprint == make_finding().fingerprint
    other = make_finding(evidence=(Evidence(source=EvidenceSource.AUTHOR, value="x"),))
    assert other.fingerprint != make_finding().fingerprint
    assert make_finding(commit_sha="b" * 40).fingerprint != make_finding().fingerprint


def test_orderings() -> None:
    assert Severity.CRITICAL.rank > Severity.HIGH.rank > Severity.LOW.rank
    assert Confidence.HIGH.rank > Confidence.MEDIUM.rank > Confidence.LOW.rank
