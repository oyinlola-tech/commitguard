from commitguard.config.loader import parse_config
from commitguard.core.decision import Action
from commitguard.policies.defaults import DEFAULT_POLICIES
from commitguard.policies.loader import build_policy_set


def test_empty_config_yields_defaults() -> None:
    policies = build_policy_set(parse_config("version: 1\n"))
    assert dict(policies) == dict(DEFAULT_POLICIES)


def test_override_changes_only_specified_fields() -> None:
    config = parse_config("version: 1\npolicies:\n  ai_coauthor:\n    action: warn\n")
    policies = build_policy_set(config)
    assert policies["ai_coauthor"].action is Action.WARN
    assert policies["ai_coauthor"].enabled is True
    assert policies["ai_coauthor"].description == DEFAULT_POLICIES["ai_coauthor"].description


def test_omitted_policies_keep_secure_defaults() -> None:
    config = parse_config("version: 1\npolicies:\n  bot_identity:\n    enabled: false\n")
    policies = build_policy_set(config)
    assert policies["bot_identity"].enabled is False
    assert policies["ai_coauthor"].action is Action.BLOCK
    assert policies["ai_coauthor"].enabled is True


def test_defaults_block_all_ai_attribution() -> None:
    for policy_id in ("ai_coauthor", "ai_identity", "ai_trailer"):
        assert DEFAULT_POLICIES[policy_id].action is Action.BLOCK
        assert DEFAULT_POLICIES[policy_id].enabled
