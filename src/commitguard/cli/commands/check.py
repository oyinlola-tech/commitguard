"""``commitguard check``: machine-friendly pass/fail analysis.

Same pipeline as ``scan``; the difference is the output contract:

* text output is one tab-separated line per finding
  (``ACTION  sha  detector  rule  evidence``) followed by
  ``result=ALLOW|WARN|BLOCK commits=N block=N warn=N allow=N``;
* ``--quiet`` prints nothing - only the exit code matters;
* ``--message-file`` checks a commit that does not exist yet, using the
  author/committer Git would use (``git var``). Phase 3 hooks will call this.

Exit codes: 0 allowed (including warnings), 1 blocked, 2 error.
"""

from pathlib import Path
from typing import Annotated

import typer

from commitguard.cli.common import (
    DEFAULT_MAX_COMMITS,
    ConfigOption,
    FormatOption,
    MaxCommitsOption,
    RevisionArgument,
)
from commitguard.cli.output import ExitCode, OutputFormat, fail, handled_errors, info
from commitguard.cli.render import render_check_text, render_json
from commitguard.core.context import ScanTrigger
from commitguard.core.decision import Action
from commitguard.exceptions.base import UnsafeInputError
from commitguard.git.repository import Repository
from commitguard.services.analysis import (
    analyze_message_file,
    analyze_revisions,
    build_report,
    load_analyzer,
)


def check_command(
    revision_range: RevisionArgument = "HEAD",
    message_file: Annotated[
        Path | None,
        typer.Option(
            "--message-file",
            help="Check a pending commit message file instead of existing commits.",
            dir_okay=False,
        ),
    ] = None,
    config: ConfigOption = None,
    output_format: FormatOption = OutputFormat.TEXT,
    max_commits: MaxCommitsOption = DEFAULT_MAX_COMMITS,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Print nothing; use the exit code.")
    ] = False,
) -> None:
    """Check commits (or a pending message) and exit 0 (pass), 1 (blocked) or 2 (error)."""
    with handled_errors():
        repository = Repository.discover()
        analyzer, loaded = load_analyzer(repository, config_path=config)
        if message_file is not None:
            try:
                reports = [analyze_message_file(repository, analyzer, message_file)]
            except FileNotFoundError:
                fail(f"message file not found: {message_file}")
            except UnsafeInputError as exc:
                fail(f"cannot read message file: {exc}")
            target = f"message-file:{message_file}"
        else:
            reports = analyze_revisions(
                repository,
                analyzer,
                revision_range,
                max_commits=max_commits,
                trigger=ScanTrigger.CHECK,
            )
            target = revision_range
        report = build_report(
            reports,
            repository=repository,
            target=target,
            trigger=ScanTrigger.CHECK,
            config=loaded,
        )
        rendered = (
            render_json(report) if output_format is OutputFormat.JSON else render_check_text(report)
        )

    if not quiet:
        info(rendered)
    if report.action is Action.BLOCK:
        raise typer.Exit(code=int(ExitCode.BLOCKED))
