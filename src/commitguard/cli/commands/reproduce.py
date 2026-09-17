"""``commitguard reproduce``: re-run the published evidence on this machine.

Exit codes: 0 when nothing failed (steps may be SKIPPED), 1 when a step failed,
2 on errors. A skipped step is never reported as a success.
"""

import json
from pathlib import Path
from typing import Annotated

import typer

from commitguard.cli.output import ExitCode, handled_errors, info

reproduce_app = typer.Typer(
    help="Reproduce the published security, benchmark and integration evidence.",
    no_args_is_help=True,
)

JsonOption = Annotated[bool, typer.Option("--json", help="Print the result document as JSON.")]
OutputOption = Annotated[
    Path | None, typer.Option("--output", help="Also write the JSON document to this file.")
]
EvidenceOption = Annotated[
    Path | None,
    typer.Option("--evidence-dir", help="Directory for evidence written by the test suites."),
]
ResultsOption = Annotated[
    Path | None,
    typer.Option(
        "--results", help="Results directory to compare against (e.g. benchmarks/results)."
    ),
]


def _emit(
    areas: list[str],
    as_json: bool,
    output: Path | None,
    evidence_dir: Path | None,
    results: Path | None,
) -> None:
    from commitguard.research.environment import collect_manifest
    from commitguard.research.reproduction import reproduce
    from commitguard.research.results import result_document

    with handled_errors():
        result = reproduce(areas, evidence_dir=evidence_dir, results_dir=results)
        manifest = collect_manifest(argv=["commitguard", "reproduce", *areas])
    document = result_document("reproduce", manifest, result)
    rendered = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    if as_json:
        info(rendered)
    else:
        info("CommitGuard reproduction")
        info("")
        for step in result.steps:
            if step.status == "NOT RUN":
                continue
            info(f"  {step.status:<8} {step.area:<12} {step.name}")
            info(f"           {step.detail}")
            if step.command:
                info(f"           $ {step.command}")
        info("")
        info(
            f"PASS {result.passed} · FAIL {result.failed} · SKIPPED {result.skipped} "
            f"· NOT RUN {result.not_run}"
        )
        info(
            f"CommitGuard {manifest.commitguard_version} · {manifest.operating_system} "
            f"{manifest.os_release} · Python {manifest.python_version} · "
            f"Git {manifest.git_version or 'unknown'}"
        )
        if result.skipped:
            info("Skipped steps were not run; they are not passes.")
    if not result.ok:
        raise typer.Exit(ExitCode.BLOCKED)


@reproduce_app.command("all")
def all_command(
    as_json: JsonOption = False,
    output: OutputOption = None,
    evidence_dir: EvidenceOption = None,
    results: ResultsOption = None,
) -> None:
    """Run every reproduction step (GitHub steps are skipped without credentials)."""
    _emit(
        ["security", "benchmark", "integration", "github"], as_json, output, evidence_dir, results
    )


@reproduce_app.command("security")
def security_command(
    as_json: JsonOption = False, output: OutputOption = None, evidence_dir: EvidenceOption = None
) -> None:
    """Run the security regression suite (pytest -m security)."""
    _emit(["security"], as_json, output, evidence_dir, None)


@reproduce_app.command("benchmark")
def benchmark_command(
    as_json: JsonOption = False, output: OutputOption = None, results: ResultsOption = None
) -> None:
    """Rebuild the detection dataset and re-measure detection against its labels."""
    _emit(["benchmark"], as_json, output, None, results)


@reproduce_app.command("integration")
def integration_command(
    as_json: JsonOption = False, output: OutputOption = None, evidence_dir: EvidenceOption = None
) -> None:
    """Run the integration suite (Git repositories, hooks, CI and the App service)."""
    _emit(["integration"], as_json, output, evidence_dir, None)


@reproduce_app.command("github")
def github_command(as_json: JsonOption = False, output: OutputOption = None) -> None:
    """Validate a real GitHub App installation (SKIPPED without credentials)."""
    _emit(["github"], as_json, output, None, None)
