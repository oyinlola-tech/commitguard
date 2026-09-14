"""Hook installation and hook-driven enforcement: Phase 3 specification (strict xfail)."""

import pytest

from commitguard.git.hooks import HOOK_MARKER, HookType, install_hooks, uninstall_hooks
from commitguard.git.repository import Repository

pytestmark = [
    pytest.mark.phase3,
    pytest.mark.xfail(raises=NotImplementedError, strict=True, reason="Phase 3"),
]


def test_install_writes_executable_marked_hooks(git_repo) -> None:  # type: ignore[no-untyped-def]
    repo = Repository.discover(git_repo.path)
    install_hooks(repo)
    for hook in HookType:
        path = repo.hooks_dir() / hook.value
        assert HOOK_MARKER in path.read_text()


def test_install_refuses_to_overwrite_foreign_hook(git_repo) -> None:  # type: ignore[no-untyped-def]
    repo = Repository.discover(git_repo.path)
    foreign = repo.hooks_dir() / "pre-commit"
    foreign.write_text("#!/bin/sh\necho mine\n")
    with pytest.raises(FileExistsError):
        install_hooks(repo, (HookType.PRE_COMMIT,))
    assert foreign.read_text() == "#!/bin/sh\necho mine\n"


def test_uninstall_only_removes_commitguard_hooks(git_repo) -> None:  # type: ignore[no-untyped-def]
    repo = Repository.discover(git_repo.path)
    foreign = repo.hooks_dir() / "pre-push"
    foreign.write_text("#!/bin/sh\necho mine\n")
    uninstall_hooks(repo)
    assert foreign.exists()
