import pytest

from commitguard.core.decision import Action
from commitguard.core.result import DetectorFailure, Evidence, Finding, ScanResult, Severity
from commitguard.policies.defaults import default_policy_set
from commitguard.policies.evaluator import PolicyEvaluator
from commitguard.policies.model import Policy, PolicySet


def _finding(rule: str) -> Finding:
    return Finding(
        detector="coauthor",
        rule=rule,
        severity=Severity.HIGH,
        message="AI agent attribution detected in commit metadata.",
        evidence=(
            Evidence(source="trailer:co-authored-by", value="Claude <noreply@anthropic.com>"),
        ),
        remediation="Remove the AI coauthor attribution before pushing.",
    )


def _result(*findings: Finding, failures: tuple[DetectorFailure, ...] = ()) -> ScanResult:
    return ScanResult(
        commit_sha=None, detectors_run=("coauthor",), findings=findings, failures=failures
    )


def _policies(**actions: tuple[bool, Action]) -> PolicySet:
    return PolicySet(
        {
            rule: Policy(id=rule, enabled=enabled, action=action, description="d")
            for rule, (enabled, action) in actions.items()
        }
    )


def test_no_findings_allows() -> None:
    decision = PolicyEvaluator(default_policy_set()).evaluate(_result())
    assert decision.action is Action.ALLOW
    assert decision.explanations == ()


@pytest.mark.parametrize("action", list(Action))
def test_enabled_policy_action_is_applied(action: Action) -> None:
    policies = _policies(ai_coauthor=(True, action))
    decision = PolicyEvaluator(policies).evaluate(_result(_finding("ai_coauthor")))
    assert decision.action is action
    (explanation,) = decision.explanations
    assert explanation.policy_id == "ai_coauthor"
    assert explanation.finding is not None


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
    policies = _policies(ai_coauthor=(True, Action.ALLOW))
    decision = PolicyEvaluator(policies).evaluate(_result(failures=(failure,)))
    assert decision.blocked
    assert decision.explanations[0].failure == failure


def test_most_restrictive_action_wins() -> None:
    policies = _policies(ai_coauthor=(True, Action.WARN), bot_identity=(True, Action.BLOCK))
    result = _result(_finding("ai_coauthor"), _finding("bot_identity"))
    decision = PolicyEvaluator(policies).evaluate(result)
    assert decision.action is Action.BLOCK
    assert [e.action for e in decision.explanations] == [Action.WARN, Action.BLOCK]


def test_policy_set_rejects_mismatched_keys() -> None:
    policy = Policy(id="ai_coauthor", action=Action.BLOCK, description="d")
    with pytest.raises(ValueError, match="does not match"):
        PolicySet({"bot_identity": policy})
