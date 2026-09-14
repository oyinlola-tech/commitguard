import shutil
from pathlib import Path

import pytest

from commitguard.exceptions.configuration import RulesError
from commitguard.rules.loader import builtin_rules_dir, load_rules


@pytest.fixture
def rules_copy(tmp_path: Path) -> Path:
    target = tmp_path / "rules"
    shutil.copytree(builtin_rules_dir(), target)
    return target


def test_builtin_rules_load(rules) -> None:  # type: ignore[no-untyped-def]
    assert rules.rules.ai_identities.agents
    assert rules.rules.patterns.coauthor_trailer_keys[0] == "co-authored-by"


def _append(path: Path, text: str) -> None:
    path.write_text(path.read_text() + text)


@pytest.mark.parametrize(
    ("filename", "text", "message"),
    [
        ("ai-identities.yaml", "\nextra: 1\n", "extra"),
        ("ai-identities.yaml", "\nschema_version: 1\n", "duplicate key"),
        (
            "ai-identities.yaml",
            "  - id: claude\n    display_name: Dup\n    names: [Dup]\n",
            "duplicate agent id",
        ),
        (
            "ai-identities.yaml",
            "  - id: other\n    display_name: X\n    names: [claude]\n",
            "alias",
        ),
        ("ai-identities.yaml", "  - id: empty\n    display_name: X\n", "needs names"),
        ("ai-domains.yaml", "  - domain: example.com\n    agent: nobody\n", "unknown agent"),
        ("ai-domains.yaml", "  - domain: Example.COM\n    agent: claude\n", "lowercase"),
        ("patterns.yaml", "\nevil: !!python/object/apply:os.system [id]\n", "invalid YAML"),
    ],
)
def test_invalid_rules_are_rejected(
    rules_copy: Path, filename: str, text: str, message: str
) -> None:
    _append(rules_copy / filename, text)
    with pytest.raises(RulesError, match=message):
        load_rules(rules_copy)


def test_missing_rule_file(rules_copy: Path) -> None:
    (rules_copy / "patterns.yaml").unlink()
    with pytest.raises(RulesError, match="not found"):
        load_rules(rules_copy)


def test_coauthor_keys_cannot_be_attribution_trailers(rules_copy: Path) -> None:
    path = rules_copy / "patterns.yaml"
    path.write_text(
        path.read_text().replace("keys: [generated-by,", "keys: [co-authored-by, generated-by,")
    )
    with pytest.raises(RulesError, match="coauthor detector"):
        load_rules(rules_copy)


def test_non_canonical_keys_are_rejected(rules_copy: Path) -> None:
    path = rules_copy / "patterns.yaml"
    path.write_text(path.read_text().replace("  - co-authored-by\n", "  - Co-Authored-By\n", 1))
    with pytest.raises(RulesError, match="canonical"):
        load_rules(rules_copy)
