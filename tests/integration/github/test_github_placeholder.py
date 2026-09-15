"""The GitHub layer runs offline: no token, no network, no HTTP client."""

import importlib
import subprocess
import sys

import pytest

GITHUB_MODULES = [
    "commitguard.github",
    "commitguard.github.client",
    "commitguard.github.checks",
    "commitguard.github.actions",
    "commitguard.github.events",
    "commitguard.github.workflow",
    "commitguard.services.ci",
]
NETWORK_MODULES = ("http.client", "urllib.request", "ssl", "requests", "httpx", "urllib3", "aiohttp")


@pytest.mark.parametrize("module", GITHUB_MODULES)
def test_imports_without_token(module: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    importlib.import_module(module)


def test_cli_and_github_layer_load_no_network_modules() -> None:
    script = (
        "import sys, commitguard.cli.app, commitguard.services.ci\n"
        f"print(sorted(m for m in {NETWORK_MODULES!r} if m in sys.modules))"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"
