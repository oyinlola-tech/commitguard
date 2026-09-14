"""Terminal output helpers and exit codes.

All text derived from commits, configuration files or Git error output passes
through :func:`~commitguard.security.sanitization.sanitize_for_terminal` here.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from enum import IntEnum
from typing import NoReturn

import typer

from commitguard.exceptions.base import CommitGuardError
from commitguard.exceptions.configuration import ConfigurationError
from commitguard.exceptions.git import GitError
from commitguard.security.sanitization import sanitize_for_terminal


class ExitCode(IntEnum):
    """Process exit codes. Hooks treat any non-zero code as "do not proceed"."""

    OK = 0
    BLOCKED = 1  # policy decision was BLOCK, or a doctor check failed
    USAGE_ERROR = 2  # reserved by Click/Typer for invalid arguments
    CONFIG_ERROR = 3
    GIT_ERROR = 4
    NOT_IMPLEMENTED = 5
    INTERNAL_ERROR = 70


def info(message: str) -> None:
    typer.echo(message)


def error(message: str) -> None:
    typer.echo(
        "commitguard: error: " + sanitize_for_terminal(message, max_length=4000, keep_newlines=True),
        err=True,
    )


def fail(message: str, code: ExitCode) -> NoReturn:
    error(message)
    raise typer.Exit(code=int(code))


def not_implemented(feature: str, phase: str) -> NoReturn:
    """Exit clearly (and non-zero) for commands whose logic does not exist yet."""
    typer.echo(
        f"commitguard: {feature} is not implemented yet (planned for {phase}).",
        err=True,
    )
    raise typer.Exit(code=int(ExitCode.NOT_IMPLEMENTED))


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Render a plain-text table. Cell values are sanitised."""
    clean_rows = [[sanitize_for_terminal(cell, max_length=200) for cell in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in clean_rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = ["  ".join(h.upper().ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    for row in clean_rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(lines)


@contextmanager
def handled_errors() -> Iterator[None]:
    """Map expected CommitGuard errors to clean messages and exit codes."""
    try:
        yield
    except ConfigurationError as exc:
        fail(str(exc), ExitCode.CONFIG_ERROR)
    except GitError as exc:
        fail(str(exc), ExitCode.GIT_ERROR)
    except CommitGuardError as exc:
        fail(str(exc), ExitCode.INTERNAL_ERROR)
