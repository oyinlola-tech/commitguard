from pathlib import Path

import pytest

from commitguard.config.defaults import DEFAULT_CONFIG_TEMPLATE, MAX_CONFIG_BYTES
from commitguard.config.loader import find_config, load_config, load_repository_config, parse_config
from commitguard.core.decision import Action
from commitguard.exceptions.configuration import ConfigurationError

TESTS_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = TESTS_DIR.parent
POLICY_FIXTURES = TESTS_DIR / "fixtures" / "policies"


def test_minimal_config() -> None:
    config = load_config(POLICY_FIXTURES / "valid-minimal.yaml")
    assert config.version == 1
    assert config.policies == {}


def test_init_template_is_valid() -> None:
    config = parse_config(DEFAULT_CONFIG_TEMPLATE)
    assert config.policies["ai_coauthor"].action is Action.BLOCK


@pytest.mark.parametrize(
    "path",
    sorted((PROJECT_ROOT / "config").rglob("*.yaml")),
    ids=lambda p: str(p.relative_to(PROJECT_ROOT)),
)
def test_shipped_profiles_and_examples_are_valid(path: Path) -> None:
    load_config(path)


def test_shipped_default_profile_matches_init_template() -> None:
    assert (PROJECT_ROOT / "config" / "default.yaml").read_text() == DEFAULT_CONFIG_TEMPLATE


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        ("duplicate-keys.yaml", "duplicate key"),
        ("unknown-policy.yaml", "unknown policy id"),
        ("unknown-key.yaml", "acton"),
        ("string-boolean.yaml", "enabled"),
        ("python-tag.yaml", "invalid YAML"),
        ("alias.yaml", "aliases are not allowed"),
        ("null-action.yaml", "must not be null"),
    ],
)
def test_insecure_or_ambiguous_configs_are_rejected(fixture: str, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        load_config(POLICY_FIXTURES / fixture)


@pytest.mark.parametrize(
    "text",
    [
        "",  # empty document
        "- version: 1\n",  # not a mapping
        "version: '1'\n",  # string version
        "version: 2\n",  # unsupported version
        "version: true\n",  # bool is not an int here
        "policies: {}\n",  # missing version
        "version: 1\nextra: true\n",  # unknown top-level key
        "version: 1\npolicies:\n  ai_coauthor:\n    action: BLOCK\n",  # wrong case
        "version: 1\npolicies:\n  ai_coauthor:\n    action: deny\n",  # unknown action
        "version: 1\npolicies:\n  ai_coauthor:\n    enabled: 1\n",  # int is not bool
        "version: 1\npolicies:\n  ai_coauthor: block\n",  # shorthand not supported
        "version: 1\npolicies: [ai_coauthor]\n",  # wrong container
    ],
)
def test_invalid_configs_are_rejected(text: str) -> None:
    with pytest.raises(ConfigurationError):
        parse_config(text)


def test_oversized_config_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / ".commitguard.yaml"
    path.write_text("version: 1\n" + "#" * MAX_CONFIG_BYTES)
    with pytest.raises(ConfigurationError, match="larger than"):
        load_config(path)


def test_directory_is_not_a_config(tmp_path: Path) -> None:
    path = tmp_path / ".commitguard.yaml"
    path.mkdir()
    with pytest.raises(ConfigurationError, match="not a regular file"):
        load_config(path)


def test_missing_config_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_repository_without_config_uses_defaults(tmp_path: Path) -> None:
    config, source = load_repository_config(tmp_path)
    assert source is None
    assert config.policies == {}


def test_both_config_filenames_is_ambiguous(tmp_path: Path) -> None:
    (tmp_path / ".commitguard.yaml").write_text("version: 1\n")
    (tmp_path / ".commitguard.yml").write_text("version: 1\n")
    with pytest.raises(ConfigurationError, match="multiple"):
        find_config(tmp_path)


def test_config_is_not_read_from_parent_directories(tmp_path: Path) -> None:
    (tmp_path / ".commitguard.yaml").write_text("version: 1\n")
    child = tmp_path / "child"
    child.mkdir()
    assert find_config(child) is None


def test_error_messages_do_not_echo_input_values() -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        parse_config("version: 1\nsecret_token: ghp_supersecretvalue\n")
    assert "ghp_supersecretvalue" not in str(excinfo.value)


def test_disabled_policy_config() -> None:
    config = parse_config("version: 1\npolicies:\n  ai_coauthor:\n    enabled: false\n")
    assert config.policies["ai_coauthor"].enabled is False


def test_unknown_action_message_is_clear() -> None:
    with pytest.raises(ConfigurationError, match=r"policies\.ai_coauthor\.action"):
        parse_config("version: 1\npolicies:\n  ai_coauthor:\n    action: deny\n")


def test_default_profile_equals_builtin_defaults() -> None:
    from commitguard.policies.defaults import DEFAULT_POLICIES
    from commitguard.policies.loader import build_policy_set

    config = load_config(PROJECT_ROOT / "config" / "default.yaml")
    assert dict(build_policy_set(config)) == dict(DEFAULT_POLICIES)


class TestLayering:
    def test_precedence_builtin_global_repository_explicit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from commitguard.config.loader import ConfigLayer, global_config_path, load_effective_config

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        global_path = global_config_path()
        global_path.parent.mkdir(parents=True)
        global_path.write_text("version: 1\n")
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".commitguard.yaml").write_text("version: 1\n")
        explicit = tmp_path / "explicit.yaml"
        explicit.write_text("version: 1\n")

        loaded = load_effective_config(repo, explicit_path=explicit)
        assert [s.layer for s in loaded.sources] == [
            ConfigLayer.BUILTIN,
            ConfigLayer.GLOBAL,
            ConfigLayer.REPOSITORY,
            ConfigLayer.EXPLICIT,
        ]
        assert global_path == tmp_path / "xdg" / "commitguard" / "config.yaml"

    def test_missing_optional_layers_are_skipped(self, tmp_path: Path) -> None:
        from commitguard.config.loader import ConfigLayer, load_effective_config

        loaded = load_effective_config(tmp_path)
        assert [s.layer for s in loaded.sources] == [ConfigLayer.BUILTIN]

    def test_invalid_global_config_is_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from commitguard.config.loader import global_config_path, load_effective_config

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        path = global_config_path()
        path.parent.mkdir(parents=True)
        path.write_text("version: 1\npolicies:\n  nope: {}\n")
        with pytest.raises(ConfigurationError, match="unknown policy"):
            load_effective_config(None)

    def test_missing_explicit_config_is_an_error(self, tmp_path: Path) -> None:
        from commitguard.config.loader import load_effective_config

        with pytest.raises(ConfigurationError, match="not found"):
            load_effective_config(None, explicit_path=tmp_path / "missing.yaml")


class TestEnforcement:
    def test_defaults_enforce_every_hook(self) -> None:
        from commitguard.config.enforcement import DEFAULT_MAX_PUSH_COMMITS, build_enforcement

        enforcement = build_enforcement(parse_config("version: 1\n"))
        assert enforcement.pre_commit
        assert enforcement.commit_msg
        assert enforcement.pre_push
        assert enforcement.complete
        assert enforcement.max_push_commits == DEFAULT_MAX_PUSH_COMMITS

    def test_layers_override_individual_hooks(self) -> None:
        from commitguard.config.enforcement import build_enforcement

        lower = parse_config("version: 1\nenforcement:\n  pre_push: false\n  commit_msg: false\n")
        upper = parse_config("version: 1\nenforcement:\n  commit_msg: true\n")
        enforcement = build_enforcement(lower, upper)
        assert not enforcement.pre_push
        assert enforcement.commit_msg
        assert not enforcement.complete
        assert enforcement.enabled("commit-msg")

    @pytest.mark.parametrize(
        "text",
        [
            "version: 1\nenforcement:\n  pre_push: 'false'\n",
            "version: 1\nenforcement:\n  pre_push:\n",
            "version: 1\nenforcement:\n  prepush: false\n",
            "version: 1\nenforcement:\n  max_push_commits: 0\n",
            "version: 1\nenforcement: false\n",
        ],
    )
    def test_invalid_enforcement_is_rejected(self, text: str) -> None:
        with pytest.raises(ConfigurationError):
            parse_config(text)


class TestRemediation:
    def test_auto_remove_is_off_by_default(self) -> None:
        """It edits what the developer wrote, so it is never on unless asked for."""
        from commitguard.config.enforcement import build_remediation

        assert not build_remediation(parse_config("version: 1\n")).auto_remove

    def test_layers_override_auto_remove(self) -> None:
        from commitguard.config.enforcement import build_remediation

        lower = parse_config("version: 1\nremediation:\n  auto_remove: true\n")
        upper = parse_config("version: 1\nremediation:\n  auto_remove: false\n")
        assert build_remediation(lower).auto_remove
        assert not build_remediation(lower, upper).auto_remove

    @pytest.mark.parametrize(
        "text",
        [
            "version: 1\nremediation:\n  auto_remove: 'true'\n",
            "version: 1\nremediation:\n  auto_remove:\n",
            "version: 1\nremediation:\n  autoremove: true\n",
            "version: 1\nremediation: true\n",
        ],
    )
    def test_invalid_remediation_is_rejected(self, text: str) -> None:
        with pytest.raises(ConfigurationError):
            parse_config(text)


class TestFixOnPush:
    def test_is_off_by_default(self) -> None:
        """It rewrites history, so it is never on unless asked for."""
        from commitguard.config.enforcement import build_remediation

        assert not build_remediation(parse_config("version: 1\n")).fix_on_push

    def test_is_independent_of_auto_remove(self) -> None:
        from commitguard.config.enforcement import build_remediation

        only_commit = build_remediation(
            parse_config("version: 1\nremediation:\n  auto_remove: true\n")
        )
        assert only_commit.auto_remove
        assert not only_commit.fix_on_push

    @pytest.mark.parametrize(
        "text",
        [
            "version: 1\nremediation:\n  fix_on_push: 'yes'\n",
            "version: 1\nremediation:\n  fix_on_push:\n",
            "version: 1\nremediation:\n  fixonpush: true\n",
        ],
    )
    def test_invalid_values_are_rejected(self, text: str) -> None:
        with pytest.raises(ConfigurationError):
            parse_config(text)
