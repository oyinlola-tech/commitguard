"""Hook template execution tests (the templates exist today)."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[3] / "hooks"
HOOK_NAMES = ["commit-msg", "pre-commit", "pre-push"]


@pytest.mark.parametrize("hook", HOOK_NAMES)
def test_hook_template_is_executable_valid_sh_with_marker(hook: str) -> None:
    path = HOOKS_DIR / hook
    assert path.stat().st_mode & stat.S_IXUSR
    content = path.read_text()
    assert content.startswith("#!/bin/sh\n")
    assert "# commitguard-managed-hook" in content
    subprocess.run(["sh", "-n", str(path)], check=True)


@pytest.mark.parametrize("hook", HOOK_NAMES)
def test_hook_fails_closed_when_commitguard_is_missing(hook: str, tmp_path: Path) -> None:
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    sh = shutil.which("sh")
    assert sh is not None
    result = subprocess.run(
        [sh, str(HOOKS_DIR / hook), str(tmp_path / "COMMIT_EDITMSG")],
        env={**os.environ, "PATH": str(empty_bin)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "fail closed" in result.stderr


def test_commit_msg_hook_blocks_real_commit_when_commitguard_missing(git_repo) -> None:  # type: ignore[no-untyped-def]
    shutil.copy2(HOOKS_DIR / "commit-msg", git_repo.path / ".git" / "hooks" / "commit-msg")
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    path = os.pathsep.join(e for e in path_entries if not (Path(e) / "commitguard").exists())

    result = subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "feat: x"],
        cwd=git_repo.path,
        env={**os.environ, "PATH": path},
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "fail closed" in result.stderr
    head = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "HEAD"], cwd=git_repo.path, capture_output=True
    )
    assert head.returncode != 0  # no commit was created
