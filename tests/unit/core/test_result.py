import pytest
from pydantic import ValidationError

from commitguard.core.result import Evidence, Finding, Severity

SHA = "a" * 40


def make_finding(**overrides: object) -> Finding:
    data: dict[str, object] = {
        "detector": "coauthor",
        "rule": "ai_coauthor",
        "severity": Severity.HIGH,
        "message": "AI agent attribution detected in commit metadata.",
        "evidence": (
            Evidence(
                source="trailer:co-authored-by",
                value="Claude <noreply@anthropic.com>",
                line_number=3,
            ),
        ),
        "commit_sha": SHA,
        "remediation": "Remove the AI coauthor attribution before pushing.",
    }
    data.update(overrides)
    return Finding.model_validate(data)


def test_finding_matches_documented_shape() -> None:
    finding = make_finding()
    assert finding.detector == "coauthor"
    assert finding.rule == "ai_coauthor"
    assert finding.severity is Severity.HIGH
    assert finding.evidence[0].value == "Claude <noreply@anthropic.com>"


def test_finding_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        make_finding(evidence=())


@pytest.mark.parametrize("field", ["message", "remediation"])
def test_finding_requires_explanation_text(field: str) -> None:
    with pytest.raises(ValidationError):
        make_finding(**{field: ""})


@pytest.mark.parametrize("rule", ["AI_COAUTHOR", "ai-coauthor", "", "1rule", "x" * 65])
def test_finding_rejects_invalid_rule_ids(rule: str) -> None:
    with pytest.raises(ValidationError):
        make_finding(rule=rule)


def test_finding_rejects_invalid_sha() -> None:
    with pytest.raises(ValidationError):
        make_finding(commit_sha="HEAD")


def test_finding_is_immutable() -> None:
    finding = make_finding()
    with pytest.raises(ValidationError):
        finding.rule = "bot_identity"  # type: ignore[misc]


def test_fingerprint_is_stable_and_evidence_sensitive() -> None:
    assert make_finding().fingerprint == make_finding().fingerprint
    other = make_finding(evidence=(Evidence(source="trailer:co-authored-by", value="x"),))
    assert other.fingerprint != make_finding().fingerprint
    assert make_finding(commit_sha="b" * 40).fingerprint != make_finding().fingerprint


def test_severity_ordering() -> None:
    ranks = [s.rank for s in (Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH)]
    assert ranks == sorted(ranks)
    assert Severity.CRITICAL.rank > Severity.HIGH.rank
