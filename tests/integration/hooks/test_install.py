"""commitguard install / uninstall, chaining of existing hooks, integrity, global mode."""

import os
import stat
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from commitguard.cli.app import app
from commitguard.cli.output import ExitCode
from commitguard.exceptions.git import HookInstallError
from commitguard.git.hooks import (
    CHAINED_SUFFIX,
    HookState,
    HookType,
    InstallAction,
    UninstallAction,
    global_template_dir,
    hook_status,
    install_global,
    install_hooks,
    uninstall_global,
    uninstall_hooks,
)
from commitguard.git.repository import Repository
from commitguard.utils.platform import supports_executable_bit

runner = CliRunner()
USER_HOOK = (
    '#!/bin/sh\necho "user hook ran" >> "$(git rev-parse --git-dir)/user-hook.log"\nexit 0\n'
)
POSIX_ONLY = pytest.mark.skipif(not supports_executable_bit(), reason="POSIX permissions")


def hooks_dir(repo) -> Path:  # type: ignore[no-untyped-def]
    return repo.path / ".git" / "hooks"


def write_user_hook(repo, name: str, content: str = USER_HOOK) -> Path:  # type: ignore[no-untyped-def]
    path = hooks_dir(repo) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, newline="\n")
    path.chmod(0o755)
    return path


def test_install_creates_all_hooks(git_repo) -> None:  # type: ignore[no-untyped-def]
    results = install_hooks(Repository.discover(git_repo.path))
    assert [r.hook for r in results] == list(HookType)
    assert {r.action for r in results} == {InstallAction.INSTALLED}
    for hook in HookType:
        status = hook_status(hooks_dir(git_repo), hook)
        assert status.state is HookState.INSTALLED
        if supports_executable_bit():
            assert os.stat(status.path).st_mode & stat.S_IXUSR


def test_install_is_idempotent(git_repo) -> None:  # type: ignore[no-untyped-def]
    repo = Repository.discover(git_repo.path)
    install_hooks(repo)
    before = {h: (hooks_dir(git_repo) / h.value).read_bytes() for h in HookType}
    assert {r.action for r in install_hooks(repo)} == {InstallAction.UNCHANGED}
    assert before == {h: (hooks_dir(git_repo) / h.value).read_bytes() for h in HookType}


def test_existing_hook_is_preserved_chained_and_restored(git_repo) -> None:  # type: ignore[no-untyped-def]
    original = write_user_hook(git_repo, "pre-commit")
    original_bytes = original.read_bytes()
    repo = Repository.discover(git_repo.path)

    (result,) = install_hooks(repo, (HookType.PRE_COMMIT,))
    assert result.action is InstallAction.CHAINED
    chained = hooks_dir(git_repo) / f"pre-commit{CHAINED_SUFFIX}"
    assert chained.read_bytes() == original_bytes

    # Both run: CommitGuard first, then the user's hook.
    assert git_repo.run("commit", "--allow-empty", "-m", "clean").returncode == 0
    assert (git_repo.path / ".git" / "user-hook.log").read_text() == "user hook ran\n"

    (removed,) = uninstall_hooks(repo, (HookType.PRE_COMMIT,))
    assert removed.action is UninstallAction.RESTORED
    assert original.read_bytes() == original_bytes
    assert not chained.exists()


def test_commitguard_block_prevents_chained_hook_from_running(git_repo) -> None:  # type: ignore[no-untyped-def]
    write_user_hook(git_repo, "commit-msg")
    install_hooks(Repository.discover(git_repo.path))
    result = git_repo.run(
        "commit", "--allow-empty", "-m", "x\n\nCo-authored-by: Claude <noreply@anthropic.com>"
    )
    assert result.returncode == 1
    assert not (git_repo.path / ".git" / "user-hook.log").exists()


def test_failing_existing_hook_is_respected(git_repo) -> None:  # type: ignore[no-untyped-def]
    write_user_hook(git_repo, "pre-commit", '#!/bin/sh\necho "user says no" >&2\nexit 3\n')
    install_hooks(Repository.discover(git_repo.path))
    result = git_repo.run("commit", "--allow-empty", "-m", "clean")
    assert result.returncode != 0
    assert "user says no" in result.stderr


def test_existing_pre_push_hook_receives_stdin(git_repo, bare_remote) -> None:  # type: ignore[no-untyped-def]
    git_repo.git("remote", "add", "origin", str(bare_remote))
    write_user_hook(
        git_repo,
        "pre-push",
        '#!/bin/sh\ncat > "$(git rev-parse --git-dir)/pre-push-stdin.txt"\n',
    )
    install_hooks(Repository.discover(git_repo.path))
    sha = git_repo.commit("clean\n")
    assert git_repo.run("push", "origin", "main").returncode == 0
    received = (git_repo.path / ".git" / "pre-push-stdin.txt").read_text()
    assert received == f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n"


def test_install_refuses_when_preserved_name_is_taken(git_repo) -> None:  # type: ignore[no-untyped-def]
    write_user_hook(git_repo, "pre-push")
    taken = write_user_hook(git_repo, f"pre-push{CHAINED_SUFFIX}", "#!/bin/sh\necho other\n")
    with pytest.raises(HookInstallError, match="refusing to overwrite"):
        install_hooks(Repository.discover(git_repo.path), (HookType.PRE_PUSH,))
    assert (hooks_dir(git_repo) / "pre-push").read_text() == USER_HOOK
    assert taken.read_text() == "#!/bin/sh\necho other\n"


def test_uninstall_leaves_foreign_hooks_and_other_files(git_repo) -> None:  # type: ignore[no-untyped-def]
    repo = Repository.discover(git_repo.path)
    install_hooks(repo, (HookType.PRE_PUSH, HookType.COMMIT_MSG))
    foreign = write_user_hook(git_repo, "pre-commit")
    sample = hooks_dir(git_repo) / "pre-rebase.sample"
    sample.write_text("sample")
    results = {r.hook: r.action for r in uninstall_hooks(repo)}
    assert results == {
        HookType.PRE_COMMIT: UninstallAction.FOREIGN,
        HookType.COMMIT_MSG: UninstallAction.REMOVED,
        HookType.PRE_PUSH: UninstallAction.REMOVED,
    }
    assert foreign.read_text() == USER_HOOK
    assert sample.read_text() == "sample"
    assert not (hooks_dir(git_repo) / "pre-push").exists()


def test_uninstall_keeps_content_added_outside_the_block(git_repo) -> None:  # type: ignore[no-untyped-def]
    repo = Repository.discover(git_repo.path)
    install_hooks(repo, (HookType.PRE_PUSH,))
    path = hooks_dir(git_repo) / "pre-push"
    path.write_text(path.read_text() + 'echo "my own addition"\n', newline="\n")
    (result,) = uninstall_hooks(repo, (HookType.PRE_PUSH,))
    assert result.action is UninstallAction.BLOCK_REMOVED
    assert path.read_text() == '#!/bin/sh\necho "my own addition"\n'


def test_modified_block_is_detected_and_repaired(git_repo) -> None:  # type: ignore[no-untyped-def]
    repo = Repository.discover(git_repo.path)
    install_hooks(repo)
    path = hooks_dir(git_repo) / "pre-push"
    path.write_text(path.read_text().replace("|| exit $?", "|| true", 1), newline="\n")
    assert hook_status(hooks_dir(git_repo), HookType.PRE_PUSH).state is HookState.MODIFIED
    actions = {r.hook: r.action for r in install_hooks(repo)}
    assert actions[HookType.PRE_PUSH] is InstallAction.UPDATED
    assert hook_status(hooks_dir(git_repo), HookType.PRE_PUSH).state is HookState.INSTALLED


@POSIX_ONLY
def test_non_executable_hook_is_detected(git_repo) -> None:  # type: ignore[no-untyped-def]
    install_hooks(Repository.discover(git_repo.path))
    path = hooks_dir(git_repo) / "pre-push"
    path.chmod(0o644)
    assert hook_status(hooks_dir(git_repo), HookType.PRE_PUSH).state is HookState.NOT_EXECUTABLE


def test_shared_hooks_path_requires_explicit_permission(git_repo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    shared = tmp_path / "shared-hooks"
    git_repo.git("config", "core.hooksPath", str(shared))
    repo = Repository.discover(git_repo.path)
    with pytest.raises(HookInstallError, match="outside this repository"):
        install_hooks(repo)
    assert not shared.exists()
    install_hooks(repo, allow_shared_hooks_path=True)
    assert hook_status(shared, HookType.PRE_PUSH).state is HookState.INSTALLED


def test_install_and_uninstall_cli(git_repo) -> None:  # type: ignore[no-untyped-def]
    write_user_hook(git_repo, "pre-push")
    result = runner.invoke(app, ["install"])
    assert result.exit_code == 0, result.output
    assert "Installed pre-commit hook" in result.stdout
    assert f"preserved as pre-push{CHAINED_SUFFIX}" in result.stdout
    assert "--no-verify" in result.stdout

    result = runner.invoke(app, ["uninstall"])
    assert result.exit_code == 0, result.output
    assert "Removed CommitGuard pre-commit hook" in result.stdout
    assert "restored the hook that existed before installation" in result.stdout
    assert "Existing Git hooks were preserved." in result.stdout
    assert (hooks_dir(git_repo) / "pre-push").read_text() == USER_HOOK


def test_install_outside_repository_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["install"]).exit_code == ExitCode.ERROR


class TestGlobal:
    def test_template_install_applies_to_new_repositories(self, tmp_path: Path) -> None:
        template, results, configured = install_global()
        assert configured
        assert template == global_template_dir()
        assert len(results) == 3
        new_repo = tmp_path / "fresh"
        subprocess.run(["git", "init", "--quiet", str(new_repo)], check=True)
        assert (
            hook_status(new_repo / ".git" / "hooks", HookType.PRE_PUSH).state is HookState.INSTALLED
        )

        _, removed, unset = uninstall_global()
        assert unset
        assert {r.action for r in removed} == {UninstallAction.REMOVED}
        result = subprocess.run(
            ["git", "config", "--global", "--get", "init.templateDir"], capture_output=True
        )
        assert result.returncode == 1

    def test_existing_template_setting_is_never_replaced(self, tmp_path: Path) -> None:
        subprocess.run(
            ["git", "config", "--global", "init.templateDir", str(tmp_path / "mine")], check=True
        )
        with pytest.raises(HookInstallError, match="already set"):
            install_global()
        result = subprocess.run(
            ["git", "config", "--global", "--get", "init.templateDir"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == str(tmp_path / "mine")

    def test_global_cli(self) -> None:
        result = runner.invoke(app, ["install", "--global"])
        assert result.exit_code == 0, result.output
        assert "Existing repositories are unchanged" in result.stdout
        assert runner.invoke(app, ["uninstall", "--global"]).exit_code == 0
