"""``commitguard check``: evaluate the pending commit against repository policy.

Intended for Git hooks: ``commit-msg`` passes the message file so the message
can be checked *before* the commit object exists.

TODO(phase-3): build a pending :class:`~commitguard.git.commit.Commit` from
the message file and ``git var GIT_AUTHOR_IDENT`` / ``GIT_COMMITTER_IDENT``,
then run the same engine + evaluator pipeline as ``scan``.
"""

from pathlib import Path
from typing import Annotated

import typer

from commitguard.cli.output import not_implemented


def check_command(
    message_file: Annotated[
        Path | None,
        typer.Option(
            "--message-file",
            help="Commit message file (as passed to the commit-msg hook).",
            dir_okay=False,
        ),
    ] = None,
    hook: Annotated[
        str | None,
        typer.Option("--hook", help="Run in Git hook mode (commit-msg, pre-commit).", hidden=True),
    ] = None,
) -> None:
    """Check the pending commit against the repository's policies."""
    not_implemented("pending commit checks", "Phase 3")
