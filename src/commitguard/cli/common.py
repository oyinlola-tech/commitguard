"""Option definitions shared by several commands."""

from pathlib import Path
from typing import Annotated

import typer

from commitguard.cli.output import OutputFormat
from commitguard.services.analysis import DEFAULT_MAX_COMMITS

ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        "-c",
        help="Extra configuration file, applied after global and repository configuration.",
        dir_okay=False,
    ),
]

FormatOption = Annotated[
    OutputFormat,
    typer.Option("--format", "-f", help="Output format.", case_sensitive=False),
]

RevisionArgument = Annotated[
    str,
    typer.Argument(
        help="Commit (HEAD, SHA, branch) or range containing '..' (e.g. origin/main..HEAD).",
    ),
]

MaxCommitsOption = Annotated[
    int,
    typer.Option("--max-commits", min=1, help="Refuse ranges selecting more commits than this."),
]

__all__ = [
    "DEFAULT_MAX_COMMITS",
    "ConfigOption",
    "FormatOption",
    "MaxCommitsOption",
    "RevisionArgument",
]
