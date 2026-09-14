"""``commitguard install`` / ``commitguard uninstall``: manage Git hooks.

TODO(phase-3): call :func:`commitguard.git.hooks.install_hooks` /
:func:`commitguard.git.hooks.uninstall_hooks`.
"""

from typing import Annotated

import typer

from commitguard.cli.output import not_implemented
from commitguard.git.hooks import HookType


def install_command(
    hooks: Annotated[
        list[HookType] | None,
        typer.Option("--hook", help="Hook to install (repeatable). Default: all."),
    ] = None,
) -> None:
    """Install CommitGuard Git hooks into the current repository."""
    not_implemented("hook installation", "Phase 3")


def uninstall_command(
    hooks: Annotated[
        list[HookType] | None,
        typer.Option("--hook", help="Hook to remove (repeatable). Default: all."),
    ] = None,
) -> None:
    """Remove CommitGuard-managed Git hooks from the current repository."""
    not_implemented("hook removal", "Phase 3")
