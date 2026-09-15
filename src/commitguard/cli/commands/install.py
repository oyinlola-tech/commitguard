"""``commitguard install`` / ``commitguard uninstall``: manage Git hooks.

Existing hooks are never overwritten: they are preserved as
``<hook>.pre-commitguard`` and run after CommitGuard. ``uninstall`` removes
only CommitGuard's managed block and restores preserved hooks.
"""

from typing import Annotated

import typer

from commitguard.cli.output import handled_errors, info, supports_unicode
from commitguard.config.enforcement import build_enforcement
from commitguard.config.loader import load_effective_config
from commitguard.exceptions.base import CommitGuardError
from commitguard.git.hooks import (
    HookType,
    InstallAction,
    InstallResult,
    UninstallAction,
    UninstallResult,
    default_python,
    install_global,
    install_hooks,
    uninstall_global,
    uninstall_hooks,
)
from commitguard.git.repository import Repository
from commitguard.security.sanitization import sanitize_for_terminal

HookOption = Annotated[
    list[HookType] | None,
    typer.Option("--hook", help="Limit to this hook (repeatable). Default: all three."),
]
GlobalOption = Annotated[
    bool,
    typer.Option(
        "--global",
        help="Use a Git template directory so NEW repositories (git init/clone) get the hooks.",
    ),
]
SharedOption = Annotated[
    bool,
    typer.Option(
        "--allow-shared-hooks-path",
        help="Allow a core.hooksPath outside this repository's Git directory.",
    ),
]

BYPASS_NOTE = (
    "Note: local hooks can be bypassed (git commit/push --no-verify) by anyone who controls "
    "this clone. Server-side enforcement (Phase 4) is required for authoritative protection."
)


def _mark() -> str:
    return "✓" if supports_unicode() else "OK"


def _describe_install(result: InstallResult) -> str:
    name = result.hook.value
    text = {
        InstallAction.INSTALLED: f"Installed {name} hook",
        InstallAction.CHAINED: f"Installed {name} hook",
        InstallAction.UPDATED: f"Updated {name} hook",
        InstallAction.UNCHANGED: f"{name} hook already installed and up to date",
    }[result.action]
    if result.chained is not None:
        text += f" (existing hook preserved as {result.chained.name}; it runs after CommitGuard)"
    return f"{_mark()} {text}"


def _describe_uninstall(result: UninstallResult) -> str:
    name = result.hook.value
    text = {
        UninstallAction.REMOVED: f"{_mark()} Removed CommitGuard {name} hook",
        UninstallAction.RESTORED: f"{_mark()} Removed CommitGuard {name} hook and restored the "
        "hook that existed before installation",
        UninstallAction.BLOCK_REMOVED: f"{_mark()} Removed CommitGuard block from {name} hook "
        "(other content kept)",
        UninstallAction.NOT_INSTALLED: f"- {name}: CommitGuard hook not installed",
        UninstallAction.FOREIGN: f"- {name}: not a CommitGuard hook; left untouched",
    }[result.action]
    return text + (f" ({result.note})" if result.note else "")


def install_command(
    hooks: HookOption = None,
    global_: GlobalOption = False,
    allow_shared_hooks_path: SharedOption = False,
) -> None:
    """Install CommitGuard Git hooks (pre-commit, commit-msg, pre-push)."""
    selected = tuple(hooks) if hooks else None
    with handled_errors():
        info("CommitGuard")
        if global_:
            template, results, configured = install_global(selected)
            info(f"Template directory: {sanitize_for_terminal(str(template))}")
            for result in results:
                info(_describe_install(result))
            if configured:
                info(f"{_mark()} Set global git config init.templateDir")
            info("New repositories created with git init / git clone will include the hooks.")
            info("Existing repositories are unchanged: run `commitguard install` inside them.")
            info(BYPASS_NOTE)
            return

        repository = Repository.discover()
        results = install_hooks(
            repository, selected, allow_shared_hooks_path=allow_shared_hooks_path
        )
        info(f"Hooks directory: {sanitize_for_terminal(str(results[0].path.parent))}")
        for result in results:
            info(_describe_install(result))
        info(f"Hooks run: {sanitize_for_terminal(default_python())} -P -m commitguard hook <name>")
        info("  (falls back to `commitguard` on PATH if that interpreter is removed)")
        try:
            enforcement = build_enforcement(*load_effective_config(repository.root).configs)
        except CommitGuardError as exc:
            info(f"! Configuration is invalid; hooks will block until it is fixed: {exc}")
        else:
            for name in ("pre_commit", "commit_msg", "pre_push"):
                if not enforcement.enabled(name):
                    info(f"! {name.replace('_', '-')} enforcement is disabled in configuration")
        info(BYPASS_NOTE)


def uninstall_command(
    hooks: HookOption = None,
    global_: GlobalOption = False,
    allow_shared_hooks_path: SharedOption = False,
) -> None:
    """Remove CommitGuard hooks, preserving and restoring any other hooks."""
    selected = tuple(hooks) if hooks else None
    with handled_errors():
        info("CommitGuard")
        if global_:
            _, uninstall_results, unset = uninstall_global(selected)
            for result in uninstall_results:
                info(_describe_uninstall(result))
            if unset:
                info(f"{_mark()} Unset global git config init.templateDir")
            info("Repositories created earlier keep their copied hooks; uninstall there too.")
            return
        repository = Repository.discover()
        uninstall_results = uninstall_hooks(
            repository, selected, allow_shared_hooks_path=allow_shared_hooks_path
        )
        for result in uninstall_results:
            info(_describe_uninstall(result))
        info("Existing Git hooks were preserved.")
