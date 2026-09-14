"""CLI commands exercised against real, isolated repositories."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from commitguard import __version__
from commitguard.cli.app import app
from commitguard.cli.output import ExitCode
from commitguard.config.defaults import DEFAULT_CONFIG_TEMPLATE

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"commitguard {__version__}"


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "install", "uninstall", "scan", "check", "policy", "doctor"):
        assert command in result.stdout


def test_init_creates_config_and_never_overwrites(git_repo) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    config = git_repo.path / ".commitguard.yaml"
    assert config.read_text() == DEFAULT_CONFIG_TEMPLATE

    config.write_text("version: 1\n# customised\n")
    result = runner.invoke(app, ["init"])
    assert result.exit_code == ExitCode.CONFIG_ERROR
    assert "already exists" in result.output
    assert config.read_text() == "version: 1\n# customised\n"


def test_init_outside_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == ExitCode.GIT_ERROR
    assert not (tmp_path / ".commitguard.yaml").exists()


def test_policy_list_defaults(git_repo) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, ["policy", "list"])
    assert result.exit_code == 0, result.output
    assert "built-in defaults" in result.stdout
    assert "ai_coauthor" in result.stdout


def test_policy_list_reflects_config(git_repo) -> None:  # type: ignore[no-untyped-def]
    (git_repo.path / ".commitguard.yaml").write_text(
        "version: 1\npolicies:\n  ai_coauthor:\n    action: warn\n"
    )
    result = runner.invoke(app, ["policy", "list"])
    assert result.exit_code == 0, result.output
    line = next(line for line in result.stdout.splitlines() if line.startswith("ai_coauthor"))
    assert line.split()[:3] == ["ai_coauthor", "yes", "warn"]


def test_policy_list_invalid_config_fails(git_repo) -> None:  # type: ignore[no-untyped-def]
    (git_repo.path / ".commitguard.yaml").write_text("version: 1\n\x1b[2Kbogus: [\n")
    result = runner.invoke(app, ["policy", "list"])
    assert result.exit_code == ExitCode.CONFIG_ERROR
    assert "\x1b" not in result.output


def test_doctor_in_repository(git_repo) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "[  ok] git" in result.stdout
    assert "[todo] hooks" in result.stdout


def test_doctor_reports_invalid_config(git_repo) -> None:  # type: ignore[no-untyped-def]
    (git_repo.path / ".commitguard.yaml").write_text("version: 2\n")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == ExitCode.BLOCKED
    assert "[fail] configuration" in result.stdout


@pytest.mark.parametrize(
    "args",
    [["scan"], ["check"], ["install"], ["uninstall"], ["check", "--message-file", "MSG"]],
)
def test_unimplemented_commands_say_so_and_exit_non_zero(git_repo, args: list[str]) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, args)
    assert result.exit_code == ExitCode.NOT_IMPLEMENTED
    assert "not implemented yet" in result.output
