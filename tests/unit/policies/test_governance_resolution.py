"""The pure governed policy resolver: precedence, strength, conflicts, exceptions, monitor mode."""

from datetime import UTC, datetime

import pytest

from commitguard.config.schema import CommitGuardConfig, PolicyOverride
from commitguard.core.decision import Action
from commitguard.policies.governance import (
    Enforcement,
    ExceptionGrant,
    ExceptionScope,
    GovernanceInputs,
    PolicyLayer,
    PolicyLevel,
    RepositoryMode,
    RuleRequirement,
    resolve_policy,
)

BLOCK, WARN, ALLOW = Action.BLOCK, Action.WARN, Action.ALLOW


def mandatory(action: Action) -> RuleRequirement:
    return RuleRequirement(action=action)


def default(action: Action) -> RuleRequirement:
    return RuleRequirement(action=action, enforcement=Enforcement.DEFAULT)


def layer(
    level: PolicyLevel, label: str, source: str = "", **rules: RuleRequirement
) -> PolicyLayer:
    return PolicyLayer(level=level, source_id=source, label=label, version=1, rules=rules)


def repo_config(**overrides: PolicyOverride) -> list[CommitGuardConfig]:
    return [CommitGuardConfig(version=1), CommitGuardConfig(version=1, policies=overrides)]


def rule(effective, policy_id):  # type: ignore[no-untyped-def]
    return next(r for r in effective.rules if r.policy_id == policy_id)


def test_no_governance_is_the_repository_configuration() -> None:
    effective = resolve_policy(
        GovernanceInputs(), repo_config(bot_identity=PolicyOverride(action=BLOCK))
    )
    assert rule(effective, "bot_identity").action is BLOCK
    assert rule(effective, "bot_identity").source is PolicyLevel.REPOSITORY_CONFIGURATION
    assert rule(effective, "ai_coauthor").source is PolicyLevel.BUILT_IN
    assert effective.conflicts == ()


def test_mandatory_organization_policy_cannot_be_weakened_by_the_repository() -> None:
    inputs = GovernanceInputs(
        layers=(
            layer(PolicyLevel.ORGANIZATION, "organization policy v8", ai_coauthor=mandatory(BLOCK)),
        )
    )
    effective = resolve_policy(inputs, repo_config(ai_coauthor=PolicyOverride(action=ALLOW)))
    provenance = rule(effective, "ai_coauthor")
    assert provenance.action is BLOCK
    assert provenance.source is PolicyLevel.ORGANIZATION
    assert provenance.enforcement is Enforcement.MANDATORY
    conflict = provenance.conflict
    assert conflict is not None
    assert (conflict.requested_action, conflict.required_action, conflict.effective_action) == (
        ALLOW,
        BLOCK,
        BLOCK,
    )
    assert conflict.requested_by is PolicyLevel.REPOSITORY_CONFIGURATION
    assert "mandatory" in conflict.reason


def test_disabling_a_mandatory_rule_is_a_conflict_and_the_rule_stays_enabled() -> None:
    inputs = GovernanceInputs(
        layers=(layer(PolicyLevel.ORGANIZATION, "org", ai_coauthor=mandatory(WARN)),)
    )
    effective = resolve_policy(inputs, repo_config(ai_coauthor=PolicyOverride(enabled=False)))
    provenance = rule(effective, "ai_coauthor")
    assert provenance.enabled is True
    assert provenance.action is WARN  # disabled by the repository: evaluated at the floor
    assert provenance.conflict is not None
    assert provenance.conflict.requested_enabled is False
    assert provenance.conflict.requested_action is ALLOW


def test_repository_may_strengthen_a_mandatory_floor() -> None:
    inputs = GovernanceInputs(
        layers=(layer(PolicyLevel.ORGANIZATION, "org", bot_identity=mandatory(WARN)),)
    )
    effective = resolve_policy(inputs, repo_config(bot_identity=PolicyOverride(action=BLOCK)))
    assert rule(effective, "bot_identity").action is BLOCK
    assert rule(effective, "bot_identity").conflict is None


def test_defaults_narrow_from_organization_to_group_to_repository_policy() -> None:
    inputs = GovernanceInputs(
        layers=(
            layer(PolicyLevel.ORGANIZATION, "org", bot_identity=default(BLOCK)),
            layer(PolicyLevel.GROUP, "group Backend", "g1", bot_identity=default(WARN)),
            layer(
                PolicyLevel.REPOSITORY_POLICY, "repository policy", "5", bot_identity=default(ALLOW)
            ),
        )
    )
    effective = resolve_policy(inputs, repo_config())
    provenance = rule(effective, "bot_identity")
    assert provenance.action is ALLOW
    assert provenance.source is PolicyLevel.REPOSITORY_POLICY
    assert provenance.conflict is None  # defaults are customizable in both directions


def test_repository_configuration_overrides_defaults() -> None:
    inputs = GovernanceInputs(
        layers=(layer(PolicyLevel.GROUP, "group Backend", "g1", bot_identity=default(WARN)),)
    )
    effective = resolve_policy(inputs, repo_config(bot_identity=PolicyOverride(action=ALLOW)))
    assert rule(effective, "bot_identity").action is ALLOW
    assert rule(effective, "bot_identity").source is PolicyLevel.REPOSITORY_CONFIGURATION


def test_several_groups_most_restrictive_default_and_strongest_floor() -> None:
    inputs = GovernanceInputs(
        layers=(
            layer(
                PolicyLevel.GROUP,
                "group A",
                "a",
                bot_identity=default(ALLOW),
                ai_trailer=mandatory(WARN),
            ),
            layer(
                PolicyLevel.GROUP,
                "group B",
                "b",
                bot_identity=default(WARN),
                ai_trailer=mandatory(BLOCK),
            ),
        )
    )
    effective = resolve_policy(inputs, repo_config(ai_trailer=PolicyOverride(action=ALLOW)))
    assert rule(effective, "bot_identity").action is WARN
    assert rule(effective, "bot_identity").source_label == "group B"
    assert rule(effective, "ai_trailer").action is BLOCK
    assert rule(effective, "ai_trailer").required_label == "group B"


def test_a_group_mandatory_requirement_beats_an_organization_default() -> None:
    inputs = GovernanceInputs(
        layers=(
            layer(PolicyLevel.ORGANIZATION, "org", bot_identity=default(ALLOW)),
            layer(PolicyLevel.GROUP, "group Production", "p", bot_identity=mandatory(BLOCK)),
        )
    )
    effective = resolve_policy(inputs, repo_config())
    assert rule(effective, "bot_identity").action is BLOCK
    assert rule(effective, "bot_identity").conflict is not None  # the org default asked for allow


def test_repository_dashboard_policy_cannot_weaken_organization_mandatory() -> None:
    inputs = GovernanceInputs(
        layers=(
            layer(PolicyLevel.ORGANIZATION, "org", ai_coauthor=mandatory(BLOCK)),
            layer(
                PolicyLevel.REPOSITORY_POLICY, "repository policy", "5", ai_coauthor=default(ALLOW)
            ),
        )
    )
    provenance = rule(resolve_policy(inputs, repo_config()), "ai_coauthor")
    assert provenance.action is BLOCK
    assert provenance.conflict is not None
    assert provenance.conflict.requested_by is PolicyLevel.REPOSITORY_POLICY


def test_exception_is_the_only_way_below_a_floor_and_the_most_specific_scope_wins() -> None:
    expires = datetime(2026, 10, 15, tzinfo=UTC)
    organization_wide = ExceptionGrant(
        exception_id="org",
        rule_id="ai_coauthor",
        action=ALLOW,
        scope=ExceptionScope.ORGANIZATION,
        scope_label="organization",
        expires_at=expires,
    )
    repository = ExceptionGrant(
        exception_id="repo",
        rule_id="ai_coauthor",
        action=WARN,
        scope=ExceptionScope.REPOSITORY,
        scope_label="repository",
        expires_at=expires,
    )
    inputs = GovernanceInputs(
        layers=(layer(PolicyLevel.ORGANIZATION, "org", ai_coauthor=mandatory(BLOCK)),),
        exceptions=(organization_wide, repository),
    )
    provenance = rule(resolve_policy(inputs, repo_config()), "ai_coauthor")
    assert provenance.action is WARN  # the repository exception, not the broader allow
    assert provenance.exception_id == "repo"
    assert provenance.source is PolicyLevel.EXCEPTION
    assert provenance.action_before_exception is BLOCK
    assert provenance.exception_expires_at == expires


def test_exception_never_raises_enforcement() -> None:
    grant = ExceptionGrant(
        exception_id="x",
        rule_id="bot_identity",
        action=WARN,
        scope=ExceptionScope.REPOSITORY,
        scope_label="repository",
        expires_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    effective = resolve_policy(
        GovernanceInputs(exceptions=(grant,)),
        repo_config(bot_identity=PolicyOverride(action=ALLOW)),
    )
    assert rule(effective, "bot_identity").action is ALLOW
    assert rule(effective, "bot_identity").exception_id is None
    with pytest.raises(ValueError, match="lowers enforcement"):
        ExceptionGrant(
            exception_id="y",
            rule_id="bot_identity",
            action=BLOCK,
            scope=ExceptionScope.REPOSITORY,
            scope_label="repository",
        )


def test_monitor_mode_reports_block_as_warn_without_changing_detection() -> None:
    inputs = GovernanceInputs(
        layers=(layer(PolicyLevel.ORGANIZATION, "org", ai_coauthor=mandatory(BLOCK)),),
        mode=RepositoryMode.MONITOR,
    )
    effective = resolve_policy(inputs, repo_config())
    assert rule(effective, "ai_coauthor").action is WARN
    assert rule(effective, "ai_coauthor").monitor_mode is True
    assert all(p.enabled for p in effective.policies if p.id != "bot_identity" or p.enabled)
    assert "monitor mode" in effective.description


def test_resolution_is_deterministic_and_layer_order_independent() -> None:
    a = layer(PolicyLevel.GROUP, "group A", "a", bot_identity=default(WARN))
    b = layer(PolicyLevel.GROUP, "group B", "b", bot_identity=default(BLOCK))
    org = layer(PolicyLevel.ORGANIZATION, "org", ai_coauthor=mandatory(BLOCK))
    first = resolve_policy(GovernanceInputs(layers=(org, a, b)), repo_config())
    second = resolve_policy(GovernanceInputs(layers=(b, org, a)), repo_config())
    assert first.policies == second.policies
    assert first.fingerprint == second.fingerprint
    assert first.inputs_fingerprint == second.inputs_fingerprint


def test_unknown_repository_configuration_is_marked() -> None:
    effective = resolve_policy(GovernanceInputs(), None)
    assert all(not r.repository_configuration_known for r in effective.rules)


def test_invalid_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="mandatory requirement must be warn or block"):
        RuleRequirement(action=ALLOW)
    with pytest.raises(ValueError, match="unknown policy id"):
        PolicyLayer(
            level=PolicyLevel.ORGANIZATION, label="x", rules={"not_a_rule": mandatory(BLOCK)}
        )
    with pytest.raises(ValueError, match="not a policy layer"):
        PolicyLayer(level=PolicyLevel.EXCEPTION, label="x")
    with pytest.raises(ValueError, match="more than"):
        GovernanceInputs(layers=tuple(layer(PolicyLevel.GROUP, f"g{i}", str(i)) for i in range(65)))


def test_a_mandatory_rule_stricter_than_the_built_in_default_is_not_a_conflict() -> None:
    inputs = GovernanceInputs(
        layers=(layer(PolicyLevel.ORGANIZATION, "org", bot_identity=mandatory(BLOCK)),)
    )
    effective = resolve_policy(inputs, repo_config())
    provenance = rule(effective, "bot_identity")
    assert provenance.action is BLOCK
    assert provenance.conflict is None  # nobody asked for less than the requirement
    assert effective.conflicts == ()
    assert provenance.source is PolicyLevel.ORGANIZATION
    assert provenance.enforcement is Enforcement.MANDATORY
