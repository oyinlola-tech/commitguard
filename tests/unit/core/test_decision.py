from commitguard.core.decision import Action, Decision, Explanation


def test_most_restrictive_action() -> None:
    assert Action.most_restrictive([]) is Action.ALLOW
    assert Action.most_restrictive([Action.ALLOW, Action.WARN]) is Action.WARN
    assert Action.most_restrictive([Action.WARN, Action.BLOCK, Action.ALLOW]) is Action.BLOCK


def test_decision_helpers() -> None:
    decision = Decision(
        action=Action.BLOCK,
        explanations=(
            Explanation(action=Action.BLOCK, reason="r1"),
            Explanation(action=Action.WARN, reason="r2"),
        ),
    )
    assert decision.blocked
    assert [e.reason for e in decision.explanations_for(Action.WARN)] == ["r2"]
