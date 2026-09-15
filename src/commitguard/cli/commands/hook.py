"""``commitguard hook <name>``: the stable interface called by installed Git hooks.

The hook scripts in ``.git/hooks`` only locate CommitGuard and call these
commands; all analysis happens in :mod:`commitguard.services.hooks`.

Exit codes: 0 allow (including warnings), 1 block, 2 error. Any error blocks
the Git operation (fail closed) with instructions to run ``commitguard doctor``.
All output goes to stderr, as Git expects from hooks.
"""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from commitguard.cli.common import ConfigOption
from commitguard.cli.output import ExitCode
from commitguard.cli.render import render_commit_hook_text, render_push_text
from commitguard.core.decision import Action
from commitguard.git.repository import Repository
from commitguard.security.sanitization import sanitize_for_terminal
from commitguard.services.hooks import (
    HookRun,
    run_commit_msg,
    run_pre_commit,
    run_pre_push,
)

MAX_PRE_PUSH_STDIN_BYTES = 8 * 1024 * 1024

hook_app = typer.Typer(
    help="Entry points used by installed Git hooks (stable interface).",
    no_args_is_help=True,
)

VerboseOption = Annotated[
    bool, typer.Option("--verbose", "-v", help="Show full evidence for every finding.")
]


def _err(text: str) -> None:
    if text:
        typer.echo(text, err=True)


@contextmanager
def _fail_closed(operation: str) -> Iterator[None]:
    """Any failure blocks the Git operation with an actionable message (exit 2)."""
    try:
        yield
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 - every failure must block, never allow
        reason = sanitize_for_terminal(str(exc) or type(exc).__name__, max_length=2000)
        _err(
            "CommitGuard could not verify repository policy.\n"
            f"Reason: {reason}\n"
            f"{operation} blocked because the security check could not be completed.\n"
            "Run: commitguard doctor"
        )
        raise typer.Exit(code=int(ExitCode.ERROR)) from None


def _finish(run: HookRun, text: str) -> None:
    if not run.enabled:
        _err(
            f"CommitGuard: {run.hook.value} enforcement is disabled by configuration; "
            "no checks were run (see `commitguard doctor`)."
        )
        return
    _err(text)
    if run.report is not None and run.report.action is Action.BLOCK:
        raise typer.Exit(code=int(ExitCode.BLOCKED))


@hook_app.command("pre-commit")
def pre_commit_command(config: ConfigOption = None, verbose: VerboseOption = False) -> None:
    """Check the pending commit's author/committer identity."""
    with _fail_closed("Commit"):
        run = run_pre_commit(Repository.discover(), config_path=config)
        text = (
            render_commit_hook_text(run.report, stage="pre-commit", verbose=verbose)
            if run.report
            else ""
        )
    _finish(run, text)


@hook_app.command("commit-msg")
def commit_msg_command(
    message_file: Annotated[Path, typer.Argument(help="Message file passed by Git.")],
    config: ConfigOption = None,
    verbose: VerboseOption = False,
) -> None:
    """Check the commit message (after Git's default cleanup) and pending identity."""
    with _fail_closed("Commit"):
        run = run_commit_msg(Repository.discover(), message_file, config_path=config)
        text = (
            render_commit_hook_text(run.report, stage="commit-msg", verbose=verbose)
            if run.report
            else ""
        )
    _finish(run, text)


@hook_app.command("pre-push")
def pre_push_command(
    remote: Annotated[str, typer.Argument(help="Remote name passed by Git.")] = "",
    url: Annotated[str, typer.Argument(help="Remote URL passed by Git.")] = "",
    config: ConfigOption = None,
    verbose: VerboseOption = False,
) -> None:
    """Check every commit a push would introduce (ref updates are read from stdin)."""
    with _fail_closed("Push"):
        data = sys.stdin.buffer.read(MAX_PRE_PUSH_STDIN_BYTES + 1)
        if len(data) > MAX_PRE_PUSH_STDIN_BYTES:
            raise ValueError("pre-push input is too large")
        run = run_pre_push(
            Repository.discover(),
            remote or url,
            data.decode("utf-8", errors="replace"),
            config_path=config,
        )
        text = (
            render_push_text(run.report, remote=remote or url, verbose=verbose)
            if run.report
            else ""
        )
    _finish(run, text)
