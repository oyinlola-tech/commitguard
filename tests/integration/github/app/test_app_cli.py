"""``commitguard github validate`` and ``commitguard github webhook-test``."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from commitguard.cli.app import app as cli
from commitguard.github.client import HttpResponse
from commitguard.github.webhooks import compute_signature

runner = CliRunner()
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "webhooks"


@pytest.fixture
def app_env(app, monkeypatch, tmp_path, payloads):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("COMMITGUARD_GITHUB_APP_ID", str(payloads.APP_ID))
    monkeypatch.setenv("COMMITGUARD_GITHUB_PRIVATE_KEY", app.private_key_pem.reveal())
    monkeypatch.setenv("COMMITGUARD_GITHUB_WEBHOOK_SECRET", payloads.WEBHOOK_SECRET.reveal())
    monkeypatch.setenv("COMMITGUARD_APP_DATA_DIR", str(tmp_path / "cli-data"))
    monkeypatch.setattr("commitguard.cli.commands.github.transport_factory", lambda: app.github)
    app.github.add_installation()
    return app


def _no_secrets(output: str, app, payloads) -> None:  # type: ignore[no-untyped-def]
    assert payloads.WEBHOOK_SECRET.reveal() not in output
    for line in app.private_key_pem.reveal().splitlines()[1:3]:
        assert line not in output
    for token in app.github.tokens:
        assert token not in output


def test_validate_ready(app_env, payloads) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(cli, ["github", "validate", "--installation-id", "42"])
    assert result.exit_code == 0, result.output
    out = result.stdout
    for line in (
        "App ID",
        "Private key",
        "Webhook secret",
        "GitHub authentication",
        "Required permissions",
        "Installation access",
    ):
        assert f"✓ {line}" in out or f"OK {line}" in out
    assert "1 repository(ies) accessible" in out
    assert out.rstrip().endswith("READY")
    _no_secrets(out, app_env, payloads)
    assert app_env.github.tokens  # a token was minted to prove access...
    assert any(p.endswith("/installation/repositories") for _, p, _ in app_env.github.requests)


def test_validate_not_ready_on_authentication_failure(app_env, payloads) -> None:  # type: ignore[no-untyped-def]
    app_env.github.fail(
        "GET",
        r"^/app$",
        HttpResponse(401, {}, b'{"message":"A JSON web token could not be decoded"}'),
    )
    result = runner.invoke(cli, ["github", "validate"])
    assert result.exit_code == 2
    assert "GitHub authentication" in result.stdout
    assert "HTTP 401" in result.stdout
    assert result.stdout.rstrip().endswith("NOT READY")
    _no_secrets(result.stdout, app_env, payloads)


def test_validate_reports_missing_and_excessive_permissions(app_env) -> None:  # type: ignore[no-untyped-def]
    app_env.github.app_permissions = {
        "contents": "write",
        "metadata": "read",
        "checks": "read",
        "administration": "write",
    }
    app_env.github.app_events = ["push"]
    result = runner.invoke(cli, ["github", "validate"])
    assert result.exit_code == 2
    out = result.stdout
    assert "Required permissions: missing checks: write, pull_requests: read" in out
    assert (
        "Least privilege: not needed by CommitGuard: administration: write, contents: write" in out
    )
    assert "Webhook events: not subscribed: pull_request" in out


def test_validate_offline_and_bad_configuration(app_env, monkeypatch, payloads) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(cli, ["github", "validate", "--offline"])
    assert result.exit_code == 0, result.output
    assert "CONFIGURATION VALID (GitHub not contacted)" in result.stdout
    assert app_env.github.requests == []

    monkeypatch.setenv(
        "COMMITGUARD_GITHUB_PRIVATE_KEY",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIsecretish\n-----END RSA PRIVATE KEY-----",
    )
    monkeypatch.setenv("COMMITGUARD_GITHUB_WEBHOOK_SECRET", "short")
    result = runner.invoke(cli, ["github", "validate"])
    assert result.exit_code == 2
    assert "Private key: GitHub App private key is malformed" in result.stdout
    assert "Webhook secret: the webhook secret must be at least 16 characters" in result.stdout
    assert "MIIsecretish" not in result.stdout
    assert "not attempted" in result.stdout


def test_webhook_test_command(app_env, payloads, tmp_path) -> None:  # type: ignore[no-untyped-def]
    payload = FIXTURES / "pull_request.synchronize.json"
    signature = compute_signature(payloads.WEBHOOK_SECRET, payload.read_bytes())
    result = runner.invoke(
        cli,
        [
            "github",
            "webhook-test",
            str(payload),
            "--event",
            "pull_request",
            "--signature",
            signature,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Signature" in result.stdout
    assert "Pull request: #7 (synchronize)" in result.stdout
    assert "Result: scan" in result.stdout
    assert app_env.github.requests == []  # no network

    result = runner.invoke(
        cli,
        [
            "github",
            "webhook-test",
            str(payload),
            "--event",
            "pull_request",
            "--signature",
            "sha256=" + "0" * 64,
        ],
    )
    assert result.exit_code == 2
    assert "invalid webhook signature" in result.stdout

    broken = tmp_path / "broken.json"
    broken.write_text(
        json.dumps({"action": "opened", "repository": {"id": 1, "full_name": "$(id)/x"}})
    )
    result = runner.invoke(cli, ["github", "webhook-test", str(broken), "--event", "pull_request"])
    assert result.exit_code == 2
    assert "Payload" in result.stdout

    push = FIXTURES / "push.json"
    result = runner.invoke(cli, ["github", "webhook-test", str(push), "--event", "push"])
    assert result.exit_code == 0
    assert "Ref: refs/heads/main" in result.stdout


def test_serve_fails_closed_without_configuration(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    for name in ("COMMITGUARD_GITHUB_APP_ID", "COMMITGUARD_APP_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    result = runner.invoke(cli, ["github", "serve", "--port", "18080"])
    assert result.exit_code == 2
    assert "COMMITGUARD_APP_DATA_DIR is not set" in result.output
