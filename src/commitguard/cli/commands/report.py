"""``commitguard report``: assemble reports from recorded results and evidence.

The report only restates recorded evidence; it never measures anything itself,
and anything without evidence is reported as "Not tested".
"""

from pathlib import Path
from typing import Annotated

import typer

from commitguard.cli.output import handled_errors, info

report_app = typer.Typer(
    help="Generate reports from recorded benchmark results.", no_args_is_help=True
)


@report_app.command("security")
def security_report_command(
    results: Annotated[
        Path, typer.Option("--results", help="Results directory (e.g. benchmarks/results).")
    ] = Path("benchmarks/results"),
    evidence: Annotated[
        Path | None,
        typer.Option("--evidence", help="Evidence directory written by the security test suites."),
    ] = None,
    output: Annotated[
        Path, typer.Option("--output", help="Directory to write the reports to.")
    ] = Path("reports"),
) -> None:
    """Write reports/security-report.{json,md} and reports/benchmark-report.md."""
    from commitguard.research.report import collect, write_reports

    with handled_errors():
        data = collect(results, evidence)
        written = write_reports(data, output)
    for path in written:
        info(f"Wrote {path}")
