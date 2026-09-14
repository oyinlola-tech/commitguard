"""``commitguard scan``: scan existing commits.

TODO(phase-2): resolve the revision range via :class:`Repository`, build a
:class:`ScanContext` per commit, run :class:`DetectionEngine` with the built-in
registry, evaluate with :class:`PolicyEvaluator`, render explanations and exit
with :attr:`ExitCode.BLOCKED` on BLOCK.
TODO(phase-3): ``--hook pre-push`` reads ``<local ref> <local sha> <remote ref>
<remote sha>`` lines from stdin to determine the commits being pushed.
"""

from typing import Annotated

import typer

from commitguard.cli.output import not_implemented


def scan_command(
    revision_range: Annotated[
        str,
        typer.Argument(help="Commit or range to scan, e.g. HEAD or origin/main..HEAD."),
    ] = "HEAD",
    hook: Annotated[
        str | None,
        typer.Option("--hook", help="Run in Git hook mode (pre-push).", hidden=True),
    ] = None,
) -> None:
    """Scan commits for policy violations."""
    not_implemented("commit scanning", "Phase 2")
