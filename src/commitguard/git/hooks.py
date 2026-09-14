"""Git hook management.

TODO(phase-3): implement :func:`install_hooks` and :func:`uninstall_hooks`.
Agreed behaviour:

* install into :meth:`Repository.hooks_dir` (honours ``core.hooksPath``);
* never overwrite a foreign hook silently - detect CommitGuard's
  :data:`HOOK_MARKER` and refuse (or chain explicitly) otherwise;
* uninstall removes only files carrying :data:`HOOK_MARKER`;
* write atomically with executable permissions;
* embed the absolute interpreter path so hooks work from GUI Git clients.

Local hooks are a convenience, not a security boundary: ``git commit
--no-verify`` skips them. Repository-level enforcement is Phase 4.
"""

from enum import StrEnum

from commitguard.git.repository import Repository

HOOK_MARKER = "# commitguard-managed-hook"


class HookType(StrEnum):
    """Git hooks CommitGuard manages."""

    PRE_COMMIT = "pre-commit"
    COMMIT_MSG = "commit-msg"
    PRE_PUSH = "pre-push"


def install_hooks(repository: Repository, hooks: tuple[HookType, ...] | None = None) -> None:
    """Install CommitGuard hooks into ``repository``. Planned for Phase 3."""
    raise NotImplementedError("hook installation is planned for Phase 3")


def uninstall_hooks(repository: Repository, hooks: tuple[HookType, ...] | None = None) -> None:
    """Remove CommitGuard-managed hooks from ``repository``. Planned for Phase 3."""
    raise NotImplementedError("hook removal is planned for Phase 3")
