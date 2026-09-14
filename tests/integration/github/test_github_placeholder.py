"""The GitHub layer must stay optional: importable offline, no credentials needed."""

import importlib
import subprocess
import sys

import pytest

GITHUB_MODULES = [
    "commitguard.github",
    "commitguard.github.client",
    "commitguard.github.checks",
    "commitguard.github.actions",
]


@pytest.mark.parametrize("module", GITHUB_MODULES)
def test_imports_without_token_or_network(module: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    importlib.import_module(module)


def test_core_cli_does_not_load_github_layer() -> None:
    script = (
        "import sys, commitguard.cli.app\n"
        "print(any(m.startswith('commitguard.github') for m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"
