"""``commitguard benchmark``: reproducible measurements of CommitGuard itself.

Benchmarks never need credentials and never contact the network. Results are
printed as text or JSON; ``--output`` writes the JSON document to a file and
``--record`` adds it to a results directory as a new, immutable result.

The research package is imported inside each command so that the everyday CLI
(and the Git hooks, which run it) does not load benchmark code.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from commitguard.cli.output import handled_errors, info

if TYPE_CHECKING:
    from commitguard.research.environment import BenchmarkManifest

benchmark_app = typer.Typer(
    help="Run reproducible benchmarks (detection, performance, hooks, repository, platform).",
    no_args_is_help=True,
)

JsonOption = Annotated[bool, typer.Option("--json", help="Print the result document as JSON.")]
OutputOption = Annotated[
    Path | None, typer.Option("--output", help="Also write the JSON document to this file.")
]
RecordOption = Annotated[
    Path | None,
    typer.Option(
        "--record",
        help="Add the result to this results directory (e.g. benchmarks/results). Never overwrites.",
    ),
]
VerboseOption = Annotated[bool, typer.Option("--verbose", help="Show every mismatch or sample.")]
DatasetOption = Annotated[
    Path | None,
    typer.Option("--dataset", help="Load the dataset from this directory instead of building it."),
]


def emit(
    benchmark: str,
    manifest: "BenchmarkManifest",
    result: Any,
    *,
    as_json: bool,
    output: Path | None,
    record: Path | None,
    text: list[str],
) -> None:
    from commitguard.research.results import result_document, write_result

    document = result_document(benchmark, manifest, result)
    rendered = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    recorded = write_result(record, document) if record is not None else None
    if as_json:
        info(rendered)
        return
    for line in text:
        info(line)
    info("")
    info(f"CommitGuard {manifest.commitguard_version} · {manifest.operating_system} {manifest.os_release} · Python {manifest.python_version} · Git {manifest.git_version or 'unknown'}")
    if recorded is not None:
        info(f"Recorded: {recorded}")


def _pct(value: float | None) -> str:
    return "n/a (no cases)" if value is None else f"{value * 100:.3f}%"


@benchmark_app.command("dataset")
def dataset_command(
    write: Annotated[Path, typer.Option("--write", help="Directory to write the dataset to.")],
) -> None:
    """Write the labelled detection dataset (JSONL per class and a manifest)."""
    from commitguard.research.datasets import build_dataset, write_dataset

    with handled_errors():
        manifest = write_dataset(build_dataset(), write)
    info(json.dumps(manifest, indent=2, sort_keys=True))


@benchmark_app.command("detection")
def detection_command(
    as_json: JsonOption = False,
    output: OutputOption = None,
    record: RecordOption = None,
    verbose: VerboseOption = False,
    dataset: DatasetOption = None,
) -> None:
    """Detection accuracy on the labelled dataset (TP, FP, TN, FN, precision, recall)."""
    from commitguard.research.datasets import (
        DATASET_VERSION,
        build_dataset,
        fingerprint,
        load_dataset,
    )
    from commitguard.research.detection import run_detection
    from commitguard.research.environment import collect_manifest

    with handled_errors():
        cases = load_dataset(dataset) if dataset else build_dataset()
        result = run_detection(cases)
        manifest = collect_manifest(
            dataset_version=DATASET_VERSION if dataset is None else f"file:{dataset}",
            dataset_fingerprint=fingerprint(cases),
        )
    d = result.decision
    rates = result.decision_rates
    text = [
        "CommitGuard detection benchmark",
        "",
        f"Cases                 {result.cases} ({result.positives} must not be allowed, {result.negatives} must be allowed)",
        f"True positives        {d.true_positive}",
        f"False negatives       {d.false_negative}   (violations allowed)",
        f"True negatives        {d.true_negative}",
        f"False positives       {d.false_positive}   (clean commits not allowed)",
        f"Precision             {_pct(rates['precision'])}",
        f"Recall                {_pct(rates['recall'])}",
        f"False positive rate   {_pct(rates['false_positive_rate'])}",
        f"False negative rate   {_pct(rates['false_negative_rate'])}",
        f"Exact decision        {result.exact_decision_matches}/{result.cases}",
        f"Latency per commit    p50 {result.latency.p50_ms} ms · p95 {result.latency.p95_ms} ms · p99 {result.latency.p99_ms} ms",
        "",
        "By class:",
        *(
            f"  {name:<12} {counts.get('cases', 0):>6} cases · exact {counts.get('exact_decision', 0)}"
            f" · FN {counts.get('false_negative', 0)} · FP {counts.get('false_positive', 0)}"
            for name, counts in result.by_class.items()
        ),
    ]
    shown = result.mismatches if verbose else result.mismatches[:20]
    if result.mismatches:
        text += ["", f"Mismatches ({len(result.mismatches)}):"]
        text += [
            f"  {m.id}: expected {m.expected_decision}, got {m.decision}"
            + (f"; missing {', '.join(m.missing_rules)}" if m.missing_rules else "")
            + (f"; unexpected {', '.join(m.unexpected_rules)}" if m.unexpected_rules else "")
            for m in shown
        ]
    emit("detection", manifest, result, as_json=as_json, output=output, record=record, text=text)
