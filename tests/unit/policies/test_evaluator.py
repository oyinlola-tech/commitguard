import pytest

from commitguard.core.decision import Action
from commitguard.core.result import (
    Confidence,
    DetectionResult,
    DetectorFailure,
    Evidence,
    EvidenceSource,
    Finding,
    Severity,
)
from commitguard.policies.defaults import default_policy_set
from commitguard.policies.evaluator import PolicyEvaluator
from commitguard.policies.model import Policy, PolicySet


def _finding(rule: str, value: str = "Claude <noreply@anthropic.com>") -> Finding:
    return Finding(
        detector="coauthor",
        rule_id=rule,
        severity=Severity.HIGH,
        confidence=Confidence.HIGH,
        title="t",
        message="AI agent attribution detected in commit metadata.",
        evidence=(Evidence(source=EvidenceSource.COAUTHOR_TRAILER, value=value),),
        remediation="Remove the AI coauthor attribution before pushing.",
    )


def _result(*findings: Finding, failures: tuple[DetectorFailure, ...] = ()) -> DetectionResult:
    return DetectionResult(
        commit_sha=None, detectors_run=("coauthor",), findings=findings, failures=failures
    )


def _policies(**actions: tuple[bool, Action]) -> PolicySet:
    return PolicySet(
        {
            rule: Policy(id=rule, enabled=enabled, action=action, description="d")
            for rule, (enabled, action) in actions.items()
        }
    )


POLICIES = _policies(
    ai_coauthor=(True, Action.BLOCK),
    ai_identity=(True, Action.BLOCK),
    bot_identity=(True, Action.WARN),
    malformed_trailer=(True, Action.WARN),
    ai_trailer=(True, Action.ALLOW),
)


@pytest.mark.parametrize(
    ("rules", "expected"),
    [
        ((), Action.ALLOW),  # no findings
        (("bot_identity",), Action.WARN),  # warning only
        (("bot_identity", "malformed_trailer"), Action.WARN),
        (("ai_coauthor",), Action.BLOCK),  # block finding
        (("bot_identity", "ai_coauthor"), Action.BLOCK),  # warning + block
        (("ai_coauthor", "bot_identity"), Action.BLOCK),  # order independent
        (("ai_coauthor", "ai_identity"), Action.BLOCK),  # multiple blocks
        (("ai_trailer",), Action.ALLOW),  # allowed finding
        (("ai_trailer", "bot_identity"), Action.WARN),
    ],
)
def test_precedence_block_over_warn_over_allow(rules: tuple[str, ...], expected: Action) -> None:
    result = _result(*(_finding(rule) for rule in rules))
    assert PolicyEvaluator(POLICIES).evaluate(result).action is expected


def test_every_finding_is_explained() -> None:
    result = _result(_finding("ai_coauthor"), _finding("bot_identity"))
    decision = PolicyEvaluator(POLICIES).evaluate(result)
    assert [(e.policy_id, e.action) for e in decision.explanations] == [
        ("ai_coauthor", Action.BLOCK),
        ("bot_identity", Action.WARN),
    ]
    assert all(e.finding is not None for e in decision.explanations)


def test_default_policy_blocks_ai_coauthor() -> None:
    decision = PolicyEvaluator(default_policy_set()).evaluate(_result(_finding("ai_coauthor")))
    assert decision.blocked


def test_disabled_policy_allows_but_is_still_explained() -> None:
    policies = _policies(ai_coauthor=(False, Action.BLOCK))
    decision = PolicyEvaluator(policies).evaluate(_result(_finding("ai_coauthor")))
    assert decision.action is Action.ALLOW
    assert "disabled" in decision.explanations[0].reason


def test_finding_without_policy_fails_closed() -> None:
    policies = _policies(ai_coauthor=(True, Action.ALLOW))
    decision = PolicyEvaluator(policies).evaluate(_result(_finding("unknown_rule")))
    assert decision.blocked
    assert "failing closed" in decision.explanations[0].reason


def test_detector_failure_fails_closed() -> None:
    failure = DetectorFailure(detector="coauthor", error_type="ValueError", message="boom")
    decision = PolicyEvaluator(POLICIES).evaluate(_result(failures=(failure,)))
    assert decision.blocked
    assert decision.explanations[0].failure == failure


def test_policy_set_rejects_mismatched_keys() -> None:
    policy = Policy(id="ai_coauthor", action=Action.BLOCK, description="d")
    with pytest.raises(ValueError, match="does not match"):
        PolicySet({"bot_identity": policy})
