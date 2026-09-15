"""init --github, github setup and doctor's GitHub enforcement section."""

from pathlib import Path

from typer.testing import CliRunner

from commitguard.cli.app import app
from commitguard.cli.output import ExitCode
from commitguard.github.workflow import WORKFLOW_FILE

runner = CliRunner()
SHA = "0123456789abcdef0123456789abcdef01234567"
ACTION_ARGS = ["--github", "--action-repository", "octo-org/commitguard", "--action-ref", SHA]


def test_init_github_creates_config_and_workflow(git_repo) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, ["init", *ACTION_ARGS])
    assert result.exit_code == 0, result.output
    workflow = git_repo.path / WORKFLOW_FILE
    assert f"uses: octo-org/commitguard@{SHA}" in workflow.read_text()
    assert (git_repo.path / ".commitguard.yaml").exists()
    assert 'require the "commitguard" status check' in result.stdout


def test_init_github_keeps_existing_config(git_repo) -> None:  # type: ignore[no-untyped-def]
    config = git_repo.path / ".commitguard.yaml"
    config.write_text("version: 1\n# mine\n")
    result = runner.invoke(app, ["init", *ACTION_ARGS])
    assert result.exit_code == 0, result.output
    assert config.read_text() == "version: 1\n# mine\n"
    assert "Kept existing configuration" in result.stdout


def test_init_github_never_overwrites_existing_workflow(git_repo) -> None:  # type: ignore[no-untyped-def]
    workflow = git_repo.path / WORKFLOW_FILE
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: mine\n")
    result = runner.invoke(app, ["init", *ACTION_ARGS])
    assert result.exit_code == ExitCode.ERROR
    assert "already exists" in result.output
    assert workflow.read_text() == "name: mine\n"
    assert not (git_repo.path / ".commitguard.yaml").exists()  # nothing half-written


def test_init_github_requires_pinned_action(git_repo) -> None:  # type: ignore[no-untyped-def]
    assert runner.invoke(app, ["init", "--github"]).exit_code == ExitCode.ERROR
    unpinned = ["init", "--github", "--action-repository", "o/r", "--action-ref", "v1"]
    result = runner.invoke(app, unpinned)
    assert result.exit_code == ExitCode.ERROR
    assert "full 40-character commit SHA" in result.output
    assert not (git_repo.path / WORKFLOW_FILE).exists()


def test_github_setup_without_workflow(git_repo) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, ["github", "setup"])
    assert result.exit_code == ExitCode.ERROR
    assert "No workflow runs CommitGuard" in result.stdout


def test_github_setup_with_workflow(git_repo) -> None:  # type: ignore[no-untyped-def]
    runner.invoke(app, ["init", *ACTION_ARGS])
    result = runner.invoke(app, ["github", "setup"])
    assert result.exit_code == 0, result.output
    assert "Required check name: commitguard" in result.stdout
    assert "Require a pull request before merging" in result.stdout
    assert "CommitGuard cannot verify these settings locally." in result.stdout
    assert "configured" not in result.stdout.lower().replace("be configured", "")


def test_doctor_reports_local_and_github_readiness_honestly(git_repo) -> None:  # type: ignore[no-untyped-def]
    assert runner.invoke(app, ["init", *ACTION_ARGS]).exit_code == 0
    assert runner.invoke(app, ["install"]).exit_code == 0
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "GitHub enforcement" in out
    assert "workflow exists: .github/workflows/commitguard.yml (check: commitguard)" in out
    assert "branch protection cannot be verified locally" in out
    assert "bundled rules available" in out
    assert "Enforcement: LOCAL + GITHUB ENFORCEMENT READY (branch protection not verified)" in out
    assert "Status: HEALTHY" in out


def test_doctor_local_only(git_repo) -> None:  # type: ignore[no-untyped-def]
    runner.invoke(app, ["init"])
    runner.invoke(app, ["install"])
    result = runner.invoke(app, ["doctor"])
    assert "Enforcement: LOCAL ENFORCEMENT ONLY" in result.stdout
    assert "no GitHub workflow runs CommitGuard" in result.stdout
    assert "Status: HEALTHY" in result.stdout


def test_doctor_flags_unsafe_workflow(git_repo) -> None:  # type: ignore[no-untyped-def]
    workflow = git_repo.path / WORKFLOW_FILE
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "name: CG\non: pull_request_target\npermissions: write-all\njobs:\n  cg:\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n"
        "      - run: commitguard ci github\n"
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == ExitCode.ERROR
    assert "pull_request_target" in result.stdout
    assert "not pinned" in result.stdout


def test_doctor_notes_unused_repository_rules(git_repo) -> None:  # type: ignore[no-untyped-def]
    rules = git_repo.path / "rules"
    rules.mkdir()
    (rules / "ai-identities.yaml").write_text("schema_version: 1\n")
    result = runner.invoke(app, ["doctor"])
    assert "rules/ directory is not used" in result.stdout


def test_ci_command_outside_actions_prints_plain_text(git_repo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    sha = git_repo.commit("clean\n")
    event = tmp_path / "event.json"
    event.write_text(
        '{"pull_request": {"number": 1, "base": {"sha": "%s"}, "head": {"sha": "%s"}}}' % (sha, sha)
    )
    result = runner.invoke(
        app, ["ci", "github", "--event-name", "pull_request", "--event-path", str(event)],
        env={"GITHUB_ACTIONS": ""},
    )
    assert result.exit_code == 0, result.output
    assert "::" not in result.stdout
    assert "Result: PASS" in result.stdout
