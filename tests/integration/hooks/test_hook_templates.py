"""The reference hook copies in hooks/ and fail-closed behaviour of installed wrappers."""

import sys
from pathlib import Path

import pytest

from commitguard.git.hooks import HookType, install_hooks, render_hook_file
from commitguard.git.repository import Repository

HOOKS_DIR = Path(__file__).resolve().parents[3] / "hooks"


@pytest.mark.parametrize("hook", list(HookType))
def test_reference_copies_match_the_generator(hook: HookType) -> None:
    assert (HOOKS_DIR / hook.value).read_text(encoding="utf-8") == render_hook_file(hook, "")


def test_missing_commitguard_blocks_commit(git_repo, no_commitguard_path) -> None:  # type: ignore[no-untyped-def]
    install_hooks(Repository.discover(git_repo.path), python="/nonexistent/python")
    result = git_repo.run(
        "commit", "--allow-empty", "-m", "clean commit", env={"PATH": no_commitguard_path()}
    )
    assert result.returncode != 0
    assert "CommitGuard is not available." in result.stderr
    assert "Operation blocked" in result.stderr
    assert "commitguard doctor" in result.stderr
    assert git_repo.run("rev-parse", "--verify", "--quiet", "HEAD").returncode != 0


def test_missing_commitguard_blocks_push(
    git_repo, bare_remote, no_commitguard_path, get_remote_refs
) -> None:  # type: ignore[no-untyped-def]
    git_repo.git("remote", "add", "origin", str(bare_remote))
    git_repo.commit("clean\n")
    install_hooks(Repository.discover(git_repo.path), python="/nonexistent/python")
    result = git_repo.run("push", "origin", "main", env={"PATH": no_commitguard_path()})
    assert result.returncode != 0
    assert "CommitGuard is not available." in result.stderr
    assert get_remote_refs(bare_remote) == {}


def test_falls_back_to_commitguard_on_path(git_repo) -> None:  # type: ignore[no-untyped-def]
    # The embedded interpreter is gone but `commitguard` is on PATH (the test venv).
    bin_dir = Path(sys.executable).parent
    if not any((bin_dir / name).exists() for name in ("commitguard", "commitguard.exe")):
        pytest.skip("commitguard console script not next to the interpreter")
    install_hooks(Repository.discover(git_repo.path), python="/nonexistent/python")
    import os

    path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    blocked = git_repo.run(
        "commit",
        "--allow-empty",
        "-m",
        "x\n\nCo-authored-by: Claude <noreply@anthropic.com>",
        env={"PATH": path},
    )
    assert blocked.returncode == 1
    assert "COMMIT BLOCKED" in blocked.stderr
