"""``commitguard scan``: analyse commits and explain every finding.

Exit codes: 0 allowed (including warnings), 1 blocked, 2 error.
"""

import typer

from commitguard.cli.common import (
    DEFAULT_MAX_COMMITS,
    ConfigOption,
    FormatOption,
    MaxCommitsOption,
    RevisionArgument,
)
from commitguard.cli.output import ExitCode, OutputFormat, handled_errors, info
from commitguard.cli.render import render_json, render_scan_text
from commitguard.core.context import ScanTrigger
from commitguard.core.decision import Action
from commitguard.git.repository import Repository
from commitguard.services.analysis import analyze_revisions, build_report, load_analyzer


def scan_command(
    revision_range: RevisionArgument = "HEAD",
    config: ConfigOption = None,
    output_format: FormatOption = OutputFormat.TEXT,
    max_commits: MaxCommitsOption = DEFAULT_MAX_COMMITS,
) -> None:
    """Scan commits for AI attribution and other policy violations."""
    with handled_errors():
        repository = Repository.discover()
        analyzer, loaded = load_analyzer(repository, config_path=config)
        reports = analyze_revisions(repository, analyzer, revision_range, max_commits=max_commits)
        report = build_report(
            reports,
            repository=repository,
            target=revision_range,
            trigger=ScanTrigger.MANUAL,
            config=loaded,
        )
        rendered = (
            render_json(report) if output_format is OutputFormat.JSON else render_scan_text(report)
        )

    info(rendered)
    if report.action is Action.BLOCK:
        raise typer.Exit(code=int(ExitCode.BLOCKED))
