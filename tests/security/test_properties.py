"""Security invariants of the detection and policy engines, checked over generated inputs.

These are the properties the whole design rests on: the same commit always gets
the same decision, a mandatory policy can only make enforcement stricter, and a
detector that fails blocks rather than passes.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

import pytest
from hypothesis import given
from hypothesis import strategies as st

from commitguard.config.schema import CommitGuardConfig
from commitguard.core.context import CommitContext
from commitguard.core.decision import Action
from commitguard.core.engine import DetectionEngine
from commitguard.core.result import Confidence, Evidence, EvidenceSource, Finding, Severity
from commitguard.detectors.base import Detector
from commitguard.detectors.registry import DetectorRegistry, builtin_registry
from commitguard.git.commit import Commit
from commitguard.policies.defaults import DEFAULT_POLICIES, default_policy_set
from commitguard.policies.evaluator import PolicyEvaluator
from commitguard.policies.loader import build_policy_set
from commitguard.policies.mandatory import apply_mandatory_policies
from commitguard.provenance.author import Identity
from commitguard.rules.loader import load_builtin_rules
from commitguard.services.analysis import Analyzer

RULES = load_builtin_rules()
POLICY_IDS = sorted(DEFAULT_POLICIES)
ACTIONS = [Action.ALLOW, Action.WARN, Action.BLOCK]
SEVERITY = {Action.ALLOW: 0, Action.WARN: 1, Action.BLOCK: 2}

IDENTITIES = [
    ("Ada Lovelace", "ada@example.com"),
    ("Claude", "noreply@anthropic.com"),
    ("Copilot", "198982749+Copilot@users.noreply.github.com"),
    ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com"),
    ("Claude Code", "dev@example.com"),
]
MESSAGES = [
    "feat: add session rotation\n",
    "feat: x\n\nCo-authored-by: Claude <noreply@anthropic.com>\n",
    "feat: x\n\nCo-authored-by: Ada Lovelace <ada@example.com>\n",
    "feat: x\n\nCo-authored-by Claude noreply@anthropic.com\n",
    "feat: x\n\n\U0001f916 Generated with [Claude Code](https://claude.com/claude-code)\n",
    "feat: x\n\nSigned-off-by: Ada Lovelace <ada@example.com>\n",
]

commits = st.builds(
    lambda message, author, committer: Commit(
        author=Identity(name=author[0], email=author[1]),
        committer=Identity(name=committer[0], email=committer[1]),
        message=message,
    ),
    st.sampled_from(MESSAGES),
    st.sampled_from(IDENTITIES),
    st.sampled_from(IDENTITIES),
)
policy_configs = st.dictionaries(
    st.sampled_from(POLICY_IDS),
    st.fixed_dictionaries(
        {},
        optional={"enabled": st.booleans(), "action": st.sampled_from([a.value for a in ACTIONS])},
    ),
    max_size=len(POLICY_IDS),
).map(lambda policies: CommitGuardConfig(version=1, policies=policies))
mandatory_configs = st.dictionaries(
    st.sampled_from(POLICY_IDS),
    st.fixed_dictionaries({"action": st.sampled_from([a.value for a in ACTIONS])}),
    max_size=len(POLICY_IDS),
).map(lambda policies: CommitGuardConfig(version=1, policies=policies))


def _finding(detector: str, rule: str, sha: str) -> Finding:
    return Finding(
        detector=detector,
        rule_id=rule,
        commit_sha=sha,
        severity=Severity.HIGH,
        confidence=Confidence.HIGH,
        summary="generated",
        evidence=(Evidence(source=EvidenceSource.TRAILER, value="x"),),
        remediation="none",
    )


class FailingDetector(Detector):
    name: ClassVar[str] = "failing"
    rules: ClassVar[frozenset[str]] = frozenset({"ai_coauthor"})
    description: ClassVar[str] = "Raises whatever it is told to raise."

    def __init__(self, error: BaseException) -> None:
        self._error = error

    def detect(self, context: CommitContext) -> Sequence[Finding]:
        raise self._error


@given(commits)
def test_detection_is_deterministic(evidence, commit: Commit) -> None:  # type: ignore[no-untyped-def]
    evidence.count("analyzer: determinism")
    first = Analyzer.create(default_policy_set(), RULES).analyze(commit)
    second = Analyzer.create(default_policy_set(), RULES).analyze(commit)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


@given(commits, st.randoms(use_true_random=False))
def test_decisions_do_not_depend_on_detector_order(evidence, commit: Commit, rng: Any) -> None:  # type: ignore[no-untyped-def]
    """Registration order must not change findings or the decision (the registry sorts)."""
    evidence.count("engine: detector order independence")
    policies = default_policy_set()
    evaluator = PolicyEvaluator(policies)
    enabled = frozenset(p.id for p in policies.values() if p.enabled)
    detectors = list(builtin_registry(RULES).all())
    rng.shuffle(detectors)
    shuffled = DetectorRegistry()
    for detector in detectors:
        shuffled.register(detector)
    context = CommitContext(commit=commit)
    ordered = DetectionEngine(builtin_registry(RULES)).run(context, enabled_rules=enabled)
    reordered = DetectionEngine(shuffled).run(context, enabled_rules=enabled)
    assert evaluator.evaluate(ordered).action is evaluator.evaluate(reordered).action
    assert {f.rule_id for f in ordered.findings} == {f.rule_id for f in reordered.findings}


@given(policy_configs, mandatory_configs)
def test_a_mandatory_policy_can_only_make_enforcement_stricter(  # type: ignore[no-untyped-def]
    evidence, repository: CommitGuardConfig, mandatory: CommitGuardConfig
) -> None:
    evidence.count("policies: mandatory floor")
    base = build_policy_set(repository)
    effective = apply_mandatory_policies(base, mandatory)
    for policy_id, policy in effective.items():
        before = base[policy_id]
        floor = mandatory.policies.get(policy_id)
        effective_before = before.action if before.enabled else Action.ALLOW
        assert SEVERITY[policy.action] >= SEVERITY[effective_before]
        assert policy.enabled or policy_id not in mandatory.policies
        if floor is not None:
            assert policy.enabled
            assert (
                SEVERITY[policy.action]
                >= SEVERITY[floor.action or DEFAULT_POLICIES[policy_id].action]
            )


@given(commits, policy_configs, mandatory_configs)
def test_a_mandatory_policy_never_turns_a_block_into_an_allow(  # type: ignore[no-untyped-def]
    evidence, commit: Commit, repository: CommitGuardConfig, mandatory: CommitGuardConfig
) -> None:
    evidence.count("analyzer: mandatory floor decisions")
    base = build_policy_set(repository)
    without = Analyzer.create(base, RULES).analyze(commit).action
    with_floor = Analyzer.create(apply_mandatory_policies(base, mandatory), RULES).analyze(commit)
    assert SEVERITY[with_floor.action] >= SEVERITY[without]


def test_a_mandatory_policy_cannot_disable_a_policy() -> None:
    disabling = CommitGuardConfig(version=1, policies={"ai_coauthor": {"enabled": False}})
    with pytest.raises(ValueError, match="can only enforce"):
        apply_mandatory_policies(default_policy_set(), disabling)


@given(
    commit=commits,
    error=st.sampled_from(
        [ValueError("boom"), KeyError("k"), RuntimeError("r"), MemoryError(), RecursionError()]
    ),
    action=st.sampled_from(ACTIONS),
)
def test_a_failing_detector_blocks(
    evidence, commit: Commit, error: BaseException, action: Action
) -> None:  # type: ignore[no-untyped-def]
    """Fail closed: whatever the policy says, a detector that does not complete blocks."""
    evidence.count("engine: fail closed")
    policies = build_policy_set(
        CommitGuardConfig(version=1, policies={"ai_coauthor": {"action": action.value}})
    )
    registry = DetectorRegistry()
    registry.register(FailingDetector(error))
    result = DetectionEngine(registry).run(
        CommitContext(commit=commit), enabled_rules=frozenset({"ai_coauthor"})
    )
    decision = PolicyEvaluator(policies).evaluate(result)
    assert decision.action is Action.BLOCK
    assert not result.complete


@given(commits)
def test_an_unknown_rule_blocks(evidence, commit: Commit) -> None:  # type: ignore[no-untyped-def]
    """Fail closed: a finding with no configured policy blocks rather than being ignored."""
    evidence.count("policies: unknown rule")

    class UnknownRuleDetector(Detector):
        name: ClassVar[str] = "unknown_rule"
        rules: ClassVar[frozenset[str]] = frozenset({"not_configured"})
        description: ClassVar[str] = "Reports a rule with no policy."

        def detect(self, context: CommitContext) -> Sequence[Finding]:
            return [_finding(self.name, "not_configured", context.commit.short_sha)]

    registry = DetectorRegistry()
    registry.register(UnknownRuleDetector())
    result = DetectionEngine(registry).run(
        CommitContext(commit=commit), enabled_rules=frozenset({"not_configured"})
    )
    assert PolicyEvaluator(default_policy_set()).evaluate(result).action is Action.BLOCK
