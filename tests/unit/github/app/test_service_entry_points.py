"""The two ways of starting the service must be configured identically.

Regression (Phase 10): only the WSGI factory loaded the notification settings,
so ``commitguard github serve`` accepted a full notification configuration and
then delivered nothing - a silent failure of a security notification path.
"""

import inspect
from pathlib import Path
from typing import Any

import pytest

from commitguard.cli.commands import github as github_cli
from commitguard.github import app as app_module
from commitguard.notifications.settings import NotificationMode


@pytest.fixture
def app_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = tmp_path / "key.pem"
    key.write_text("-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----\n")
    key.chmod(0o600)
    for name, value in {
        "COMMITGUARD_GITHUB_APP_ID": "12345",
        "COMMITGUARD_GITHUB_PRIVATE_KEY_FILE": str(key),
        "COMMITGUARD_GITHUB_WEBHOOK_SECRET": "s" * 32,
        "COMMITGUARD_APP_DATA_DIR": str(tmp_path / "data"),
        "COMMITGUARD_NOTIFICATIONS_MODE": "test",
    }.items():
        monkeypatch.setenv(name, value)


def test_service_from_environment_loads_notification_settings(
    app_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_from_settings(settings: Any, **overrides: Any) -> str:
        captured.update(overrides)
        return "service"

    monkeypatch.setattr(app_module.GitHubAppService, "from_settings", fake_from_settings)
    assert app_module.service_from_environment() == "service"
    assert captured["notification_settings"].mode is NotificationMode.TEST


@pytest.mark.parametrize(
    "function", [github_cli.serve_command, app_module.wsgi_app_from_environment]
)
def test_entry_points_build_the_service_through_the_shared_factory(function: Any) -> None:
    source = inspect.getsource(function)
    assert "service_from_environment()" in source
    assert "GitHubAppService.from_settings" not in source
