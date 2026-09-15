"""Fixtures for tests that run real Git operations through installed hooks."""

import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from commitguard.git.hooks import install_hooks
from commitguard.git.repository import Repository

CLEAN = "feat: implement authentication\n\nCo-authored-by: John Doe <john@example.com>\n"
AI = "feat: add payment service\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"


@pytest.fixture
def hooked_repo(git_repo, bare_remote: Path):  # type: ignore[no-untyped-def]
    """A repository with CommitGuard hooks installed and an empty bare ``origin``."""
    git_repo.git("remote", "add", "origin", str(bare_remote))
    install_hooks(Repository.discover(git_repo.path))
    return git_repo


@pytest.fixture
def commit_with_hooks(hooked_repo):  # type: ignore[no-untyped-def]
    """Run ``git commit`` with hooks enabled; returns the completed process."""

    def commit(message: str, *extra: str, env: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
        return hooked_repo.run("commit", "--allow-empty", "-m", message, *extra, env=env)

    return commit


def path_without_commitguard() -> str:
    """PATH with every directory containing a `commitguard` executable removed."""
    entries = os.environ.get("PATH", "").split(os.pathsep)
    names = ("commitguard", "commitguard.exe")
    return os.pathsep.join(e for e in entries if not any((Path(e) / n).exists() for n in names))


@pytest.fixture
def no_commitguard_path() -> Callable[[], str]:
    assert shutil.which("git") is not None
    return path_without_commitguard
