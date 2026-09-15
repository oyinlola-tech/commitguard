"""CLI commands exercised against real, isolated repositories."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from commitguard import __version__
from commitguard.cli.app import app
from commitguard.cli.output import ExitCode
from commitguard.config.defaults import DEFAULT_CONFIG_TEMPLATE
from commitguard.provenance.author import Identity

runner = CliRunner()

CLEAN = "feat: implement authentication\n\nCo-authored-by: John Doe <john@example.com>\n"
AI = (
    "feat: add payment service\n\n"
    "Co-authored-by: John Doe <john@example.com>\n"
    "Co-authored-by: Claude <noreply@anthropic.com>\n"
)
BOT = Identity(name="dependabot[bot]", email="49699333+dependabot[bot]@users.noreply.github.com")

RLO = chr(0x202E)


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"commitguard {__version__}"


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "install", "uninstall", "scan", "check", "policy", "doctor"):
        assert command in result.stdout


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #
def test_scan_clean_commit(git_repo) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit(CLEAN)
    result = runner.invoke(app, ["scan"])
    assert result.exit_code == ExitCode.OK, result.output
    assert "No policy violations detected" in result.stdout
    assert "Result: ALLOW" in result.stdout


def test_scan_blocks_ai_coauthor(git_repo) -> None:  # type: ignore[no-untyped-def]
    sha = git_repo.commit(AI)
    result = runner.invoke(app, ["scan"])
    assert result.exit_code == ExitCode.BLOCKED, result.output
    out = result.stdout
    assert "BLOCKED" in out
    assert "AI coauthor detected" in out
    assert f"Commit:      {sha[:7]}" in out
    assert "Rule:        ai_coauthor" in out
    assert "Severity:    high" in out
    assert "Evidence:    Claude <noreply@anthropic.com>" in out
    assert "Remediation:" in out
    assert "Result: BLOCK" in out
    assert "John Doe" not in out  # the human co-author is not reported


def test_scan_range_reports_only_violating_commits(git_repo) -> None:  # type: ignore[no-untyped-def]
    base = git_repo.commit("base\n")
    git_repo.commit(CLEAN)
    bad = git_repo.commit(AI)
    git_repo.commit(CLEAN)
    result = runner.invoke(app, ["scan", f"{base}..HEAD"])
    assert result.exit_code == ExitCode.BLOCKED
    assert "(3 commits)" in result.stdout
    assert result.stdout.count("AI coauthor detected") == 1
    assert bad[:7] in result.stdout


def test_scan_warning_only_exits_zero(git_repo) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit("build(deps): bump\n", author=BOT)
    result = runner.invoke(app, ["scan"])
    assert result.exit_code == ExitCode.OK, result.output
    assert "WARNING" in result.stdout
    assert "Result: WARN" in result.stdout


def test_scan_respects_repository_policy(git_repo) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit(AI)
    (git_repo.path / ".commitguard.yaml").write_text(
        "version: 1\npolicies:\n  ai_coauthor:\n    action: warn\n"
    )
    result = runner.invoke(app, ["scan"])
    assert result.exit_code == ExitCode.OK, result.output
    assert "Result: WARN" in result.stdout


def test_explicit_config_overrides_repository_config(git_repo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit(AI)
    (git_repo.path / ".commitguard.yaml").write_text(
        "version: 1\npolicies:\n  ai_coauthor:\n    action: allow\n"
    )
    strict = tmp_path / "strict.yaml"
    strict.write_text("version: 1\npolicies:\n  ai_coauthor:\n    action: block\n")
    assert runner.invoke(app, ["scan"]).exit_code == ExitCode.OK
    assert runner.invoke(app, ["scan", "--config", str(strict)]).exit_code == ExitCode.BLOCKED


def test_global_config_is_applied(
    git_repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit(AI)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    path = tmp_path / "xdg" / "commitguard" / "config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("version: 1\npolicies:\n  ai_coauthor:\n    action: warn\n")
    result = runner.invoke(app, ["scan"])
    assert result.exit_code == ExitCode.OK
    assert "global:" in result.stdout


def test_scan_json_output(git_repo) -> None:  # type: ignore[no-untyped-def]
    sha = git_repo.commit(AI)
    result = runner.invoke(app, ["scan", "--format", "json"])
    assert result.exit_code == ExitCode.BLOCKED
    data = json.loads(result.stdout)
    assert data["schema_version"] == 1
    assert data["action"] == "block"
    assert data["summary"] == {"commits": 1, "allow": 0, "warn": 0, "block": 1}
    (commit,) = data["commits"]
    assert commit["commit_sha"] == sha
    (item,) = commit["findings"]
    assert item["action"] == "block"
    assert item["finding"]["rule_id"] == "ai_coauthor"
    assert item["fingerprint"]
    assert result.stdout.isascii()


def test_scan_output_is_sanitised(git_repo) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit(f"x\n\nCo-authored-by: Claude <noreply@anthropic.com>\x1b[1A\x1b[2K{RLO}\n")
    for fmt in ("text", "json"):
        result = runner.invoke(app, ["scan", "--format", fmt])
        assert result.exit_code == ExitCode.BLOCKED
        assert "\x1b" not in result.stdout
        assert RLO not in result.stdout


@pytest.mark.parametrize(
    "args", [["scan", "does-not-exist"], ["scan", "--output=/tmp/x"], ["check", "nope..HEAD"]]
)
def test_invalid_revisions_exit_2(git_repo, args: list[str]) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit(CLEAN)
    assert runner.invoke(app, args).exit_code == ExitCode.ERROR


def test_invalid_config_exits_2(git_repo) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit(AI)
    (git_repo.path / ".commitguard.yaml").write_text("version: 1\npolicies:\n  ai_coauthors: {}\n")
    result = runner.invoke(app, ["scan"])
    assert result.exit_code == ExitCode.ERROR
    assert "unknown policy id" in result.output


def test_scan_outside_repository_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["scan"]).exit_code == ExitCode.ERROR


def test_scan_empty_repository_exits_2(git_repo) -> None:  # type: ignore[no-untyped-def]
    assert runner.invoke(app, ["scan"]).exit_code == ExitCode.ERROR


def test_scan_does_not_modify_the_repository(git_repo) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit(AI)
    before = git_repo.git("for-each-ref", "--format=%(objectname) %(refname)") + git_repo.git(
        "status", "--porcelain"
    )
    runner.invoke(app, ["scan"])
    runner.invoke(app, ["check"])
    after = git_repo.git("for-each-ref", "--format=%(objectname) %(refname)") + git_repo.git(
        "status", "--porcelain"
    )
    assert before == after


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #
def test_check_clean(git_repo) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit(CLEAN)
    result = runner.invoke(app, ["check"])
    assert result.exit_code == ExitCode.OK
    assert result.stdout.strip() == "result=ALLOW commits=1 block=0 warn=0 allow=0"


def test_check_blocked_machine_output(git_repo) -> None:  # type: ignore[no-untyped-def]
    sha = git_repo.commit(AI)
    result = runner.invoke(app, ["check"])
    assert result.exit_code == ExitCode.BLOCKED
    lines = result.stdout.strip().splitlines()
    assert lines[0].split("\t") == [
        "BLOCK",
        sha[:7],
        "coauthor",
        "ai_coauthor",
        "Claude <noreply@anthropic.com>",
    ]
    assert lines[-1] == "result=BLOCK commits=1 block=1 warn=0 allow=0"


def test_check_quiet(git_repo) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit(AI)
    result = runner.invoke(app, ["check", "--quiet"])
    assert result.exit_code == ExitCode.BLOCKED
    assert result.stdout == ""


def test_check_message_file_pending_commit(git_repo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text(AI)
    result = runner.invoke(app, ["check", "--message-file", str(message)])
    assert result.exit_code == ExitCode.BLOCKED, result.output
    assert result.stdout.startswith("BLOCK\tpending\tcoauthor\tai_coauthor")

    message.write_text(CLEAN)
    assert runner.invoke(app, ["check", "--message-file", str(message)]).exit_code == ExitCode.OK


def test_check_message_file_uses_pending_author(
    git_repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Claude")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "noreply@anthropic.com")
    message = tmp_path / "MSG"
    message.write_text("feat: x\n")
    result = runner.invoke(app, ["check", "--message-file", str(message)])
    assert result.exit_code == ExitCode.BLOCKED
    assert "ai_identity" in result.stdout


def test_check_missing_message_file(git_repo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, ["check", "--message-file", str(tmp_path / "missing")])
    assert result.exit_code == ExitCode.ERROR


def test_check_json(git_repo) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit(CLEAN)
    result = runner.invoke(app, ["check", "--format", "json"])
    assert result.exit_code == ExitCode.OK
    assert json.loads(result.stdout)["action"] == "allow"


# --------------------------------------------------------------------------- #
# init / policy / doctor / unimplemented
# --------------------------------------------------------------------------- #
def test_init_creates_config_and_never_overwrites(git_repo) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    config = git_repo.path / ".commitguard.yaml"
    assert config.read_text() == DEFAULT_CONFIG_TEMPLATE

    config.write_text("version: 1\n# customised\n")
    result = runner.invoke(app, ["init"])
    assert result.exit_code == ExitCode.ERROR
    assert "already exists" in result.output
    assert config.read_text() == "version: 1\n# customised\n"


def test_init_outside_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == ExitCode.ERROR
    assert not (tmp_path / ".commitguard.yaml").exists()


def test_policy_list_defaults(git_repo) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, ["policy", "list"])
    assert result.exit_code == 0, result.output
    assert "- builtin" in result.stdout
    assert "ai_coauthor" in result.stdout


def test_policy_list_reflects_config(git_repo) -> None:  # type: ignore[no-untyped-def]
    (git_repo.path / ".commitguard.yaml").write_text(
        "version: 1\npolicies:\n  ai_coauthor:\n    action: warn\n"
    )
    result = runner.invoke(app, ["policy", "list"])
    assert result.exit_code == 0, result.output
    assert "repository:" in result.stdout
    line = next(line for line in result.stdout.splitlines() if line.startswith("ai_coauthor"))
    assert line.split()[:3] == ["ai_coauthor", "yes", "warn"]


def test_policy_list_invalid_config_fails(git_repo) -> None:  # type: ignore[no-untyped-def]
    (git_repo.path / ".commitguard.yaml").write_text("version: 1\n\x1b[2Kbogus: [\n")
    result = runner.invoke(app, ["policy", "list"])
    assert result.exit_code == ExitCode.ERROR
    assert "\x1b" not in result.output


def test_doctor_without_hooks_reports_incomplete_enforcement(git_repo) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == ExitCode.ERROR
    assert "Git 2." in result.stdout
    assert "Detection engine" in result.stdout
    assert "pre-push hook missing" in result.stdout
    assert "Fix: commitguard install" in result.stdout
    assert "Security enforcement is incomplete." in result.stdout
    assert "Status: UNHEALTHY" in result.stdout


def test_doctor_healthy_after_init_and_install(git_repo) -> None:  # type: ignore[no-untyped-def]
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["install"]).exit_code == 0
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "Status: HEALTHY" in result.stdout


def test_doctor_reports_disabled_enforcement(git_repo) -> None:  # type: ignore[no-untyped-def]
    (git_repo.path / ".commitguard.yaml").write_text(
        "version: 1\nenforcement:\n  pre_push: false\n"
    )
    assert runner.invoke(app, ["install"]).exit_code == 0
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "pre-push enforcement disabled in configuration" in result.stdout
    assert "Security enforcement is incomplete." in result.stdout
    assert "Status: DEGRADED" in result.stdout


def test_doctor_reports_invalid_config(git_repo) -> None:  # type: ignore[no-untyped-def]
    (git_repo.path / ".commitguard.yaml").write_text("version: 2\n")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == ExitCode.ERROR
    assert "Configuration" in result.stdout
    assert "hooks block until then" in result.stdout


def test_doctor_reports_hooks_path_pointing_elsewhere(git_repo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    git_repo.git("config", "core.hooksPath", str(tmp_path / "elsewhere"))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == ExitCode.ERROR
    assert "core.hooksPath points outside" in result.stdout


def test_check_verbose(git_repo) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit(AI)
    result = runner.invoke(app, ["check", "--verbose"])
    assert result.exit_code == ExitCode.BLOCKED
    assert "Remediation:" in result.stdout
    assert "Result: BLOCK" in result.stdout
