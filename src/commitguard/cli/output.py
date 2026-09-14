"""Terminal output helpers and exit codes.

All text derived from commits, configuration files or Git error output passes
through :func:`~commitguard.security.sanitization.sanitize_for_terminal` here
or in :mod:`commitguard.cli.render`.
"""

import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from enum import IntEnum, StrEnum
from typing import NoReturn

import typer

from commitguard.exceptions.base import CommitGuardError
from commitguard.security.sanitization import sanitize_for_terminal


class ExitCode(IntEnum):
    """Process exit codes (stable contract for hooks and CI).

    0  allowed - no findings, or only findings whose policy is allow/warn
    1  blocked - at least one finding or detector failure evaluated to BLOCK
    2  error   - invalid configuration or rules, Git failure, bad arguments,
               unimplemented command, or any unexpected runtime error
    """

    OK = 0
    BLOCKED = 1
    ERROR = 2


class OutputFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


def info(message: str) -> None:
    typer.echo(message)


def error(message: str) -> None:
    typer.echo(
        "commitguard: error: "
        + sanitize_for_terminal(message, max_length=4000, keep_newlines=True),
        err=True,
    )


def fail(message: str, code: ExitCode = ExitCode.ERROR) -> NoReturn:
    error(message)
    raise typer.Exit(code=int(code))


def not_implemented(feature: str, phase: str) -> NoReturn:
    """Exit clearly (and with the error code) for commands that do not exist yet."""
    typer.echo(
        f"commitguard: {feature} is not implemented yet (planned for {phase}).",
        err=True,
    )
    raise typer.Exit(code=int(ExitCode.ERROR))


def supports_unicode() -> bool:
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    return encoding.startswith("utf")


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
    """Map every failure to a clean message and exit code 2.

    Unexpected exceptions must not surface as exit code 1 (which means
    "blocked by policy") or print tracebacks that could include local data.
    """
    try:
        yield
    except typer.Exit:
        raise
    except CommitGuardError as exc:
        fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - last-resort mapping to the error exit code
        fail(f"internal error ({type(exc).__name__}): {exc}")
