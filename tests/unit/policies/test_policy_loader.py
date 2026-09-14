from commitguard.config.loader import parse_config
from commitguard.core.decision import Action
from commitguard.policies.defaults import DEFAULT_POLICIES
from commitguard.policies.loader import build_policy_set


def test_no_config_yields_defaults() -> None:
    assert dict(build_policy_set()) == dict(DEFAULT_POLICIES)
    assert dict(build_policy_set(parse_config("version: 1\n"))) == dict(DEFAULT_POLICIES)


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


def test_later_layers_override_earlier_ones_field_by_field() -> None:
    global_layer = parse_config(
        "version: 1\npolicies:\n"
        "  ai_coauthor:\n    action: warn\n"
        "  bot_identity:\n    enabled: false\n"
    )
    repo_layer = parse_config("version: 1\npolicies:\n  ai_coauthor:\n    action: block\n")
    explicit = parse_config("version: 1\npolicies:\n  bot_identity:\n    action: block\n")
    policies = build_policy_set(global_layer, repo_layer, explicit)
    assert policies["ai_coauthor"].action is Action.BLOCK
    assert policies["bot_identity"].enabled is False  # from global, not reset by later layers
    assert policies["bot_identity"].action is Action.BLOCK


def test_defaults_block_all_ai_attribution() -> None:
    for policy_id in ("ai_coauthor", "ai_identity", "ai_trailer"):
        assert DEFAULT_POLICIES[policy_id].action is Action.BLOCK
        assert DEFAULT_POLICIES[policy_id].enabled
