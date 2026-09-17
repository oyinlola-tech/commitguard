"""``commitguard benchmark``: reproducible measurements of CommitGuard itself.

Benchmarks never need credentials and never contact the network. Results are
printed as text or JSON; ``--output`` writes the JSON document to a file and
``--record`` adds it to a results directory as a new, immutable result.

The research package is imported inside each command so that the everyday CLI
(and the Git hooks, which run it) does not load benchmark code.

Exit codes: 0 when the benchmark ran and every correctness check held, 1 when
it ran but a correctness check failed (a detection mismatch, a hook that did not
enforce, a platform check that failed), 2 on errors.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from commitguard.cli.output import ExitCode, fail, handled_errors, info

if TYPE_CHECKING:
    from commitguard.research.environment import BenchmarkManifest

benchmark_app = typer.Typer(
    help="Run reproducible benchmarks: detection, performance, hooks, repository, platform.",
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
        help="Add the result to this results directory (e.g. benchmarks/results).",
    ),
]
VerboseOption = Annotated[bool, typer.Option("--verbose", help="Show every mismatch or check.")]
DatasetVersionOption = Annotated[
    str | None,
    typer.Option("--dataset-version", help="Build this dataset version (default: the latest)."),
]
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
    ok: bool = True,
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
    else:
        for line in text:
            info(line)
        info("")
        info(
            f"CommitGuard {manifest.commitguard_version} · {manifest.operating_system} "
            f"{manifest.os_release} · Python {manifest.python_version} · "
            f"Git {manifest.git_version or 'unknown'}"
        )
        if recorded is not None:
            info(f"Recorded: {recorded}")
    if not ok:
        raise typer.Exit(ExitCode.BLOCKED)


def _dataset_file_version(directory: Path) -> str:
    manifest = directory / "manifest.json"
    try:
        return f"{json.loads(manifest.read_text(encoding='utf-8'))['version']} (file)"
    except (OSError, ValueError, KeyError):
        return f"file:{directory}"


def _pct(value: float | None) -> str:
    return "n/a (no cases)" if value is None else f"{value * 100:.3f}%"


def _mib(value: int | None) -> str:
    return "not available on this platform" if value is None else f"{value / 1_048_576:.1f} MiB"


@benchmark_app.command("dataset")
def dataset_command(
    write: Annotated[Path, typer.Option("--write", help="Directory to write the dataset to.")],
    dataset_version: DatasetVersionOption = None,
) -> None:
    """Write the labelled detection dataset (JSONL per class and a manifest)."""
    from commitguard.research.datasets import DATASET_VERSION, build_dataset, write_dataset

    version = dataset_version or DATASET_VERSION
    with handled_errors():
        manifest = write_dataset(build_dataset(version=version), write, version=version)
    info(json.dumps(manifest, indent=2, sort_keys=True))


@benchmark_app.command("detection")
def detection_command(
    as_json: JsonOption = False,
    output: OutputOption = None,
    record: RecordOption = None,
    verbose: VerboseOption = False,
    dataset: DatasetOption = None,
    dataset_version: DatasetVersionOption = None,
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
        if dataset is not None:
            # This load_dataset reads local JSONL files written by
            # `commitguard benchmark dataset`; nothing is downloaded.
            cases = load_dataset(dataset)  # nosec B615
            version = _dataset_file_version(dataset)
        else:
            version = dataset_version or DATASET_VERSION
            cases = build_dataset(version=version)
        result = run_detection(cases)
        manifest = collect_manifest(dataset_version=version, dataset_fingerprint=fingerprint(cases))
    d, rates, latency = result.decision, result.decision_rates, result.latency
    text = [
        "CommitGuard detection benchmark",
        "",
        f"Dataset               {version} ({result.cases} cases: {result.positives} must not "
        f"be allowed, {result.negatives} must be allowed)",
        f"True positives        {d.true_positive}",
        f"False negatives       {d.false_negative}   (violations allowed)",
        f"True negatives        {d.true_negative}",
        f"False positives       {d.false_positive}   (clean commits not allowed)",
        f"Precision             {_pct(rates['precision'])}",
        f"Recall                {_pct(rates['recall'])}",
        f"False positive rate   {_pct(rates['false_positive_rate'])}",
        f"False negative rate   {_pct(rates['false_negative_rate'])}",
        f"Exact decision        {result.exact_decision_matches}/{result.cases}",
        f"Latency per commit    p50 {latency.p50_ms} ms · p95 {latency.p95_ms} ms · "
        f"p99 {latency.p99_ms} ms",
        "",
        "By class:",
    ]
    for name, counts in result.by_class.items():
        text.append(
            f"  {name:<12} {counts.get('cases', 0):>6} cases · exact "
            f"{counts.get('exact_decision', 0)} · FN {counts.get('false_negative', 0)} · "
            f"FP {counts.get('false_positive', 0)}"
        )
    if result.mismatches:
        text += ["", f"Mismatches ({len(result.mismatches)}):"]
        for m in result.mismatches if verbose else result.mismatches[:20]:
            line = f"  {m.id}: expected {m.expected_decision}, got {m.decision}"
            if m.missing_rules:
                line += f"; missing {', '.join(m.missing_rules)}"
            if m.unexpected_rules:
                line += f"; unexpected {', '.join(m.unexpected_rules)}"
            text.append(line)
    emit(
        "detection",
        manifest,
        result,
        as_json=as_json,
        output=output,
        record=record,
        text=text,
        ok=not result.mismatches,
    )


@benchmark_app.command("performance")
def performance_command(
    as_json: JsonOption = False,
    output: OutputOption = None,
    record: RecordOption = None,
    quick: Annotated[
        bool, typer.Option("--quick", help="Smaller batches and messages (smoke test).")
    ] = False,
) -> None:
    """Detection latency (p50/p95/p99), message-size scaling, CPU time and memory."""
    from commitguard.research.environment import collect_manifest
    from commitguard.research.performance import run_performance

    with handled_errors():
        if quick:
            result = run_performance(batch_sizes=(1, 10, 100), message_sizes=(1_024, 102_400))
        else:
            result = run_performance()
        manifest = collect_manifest()
    text = ["CommitGuard detection performance", "", f"Rule loading    {result.startup_ms} ms", ""]
    text.append("Commits   wall ms    CPU ms   commits/s   p50 ms   p95 ms   p99 ms   alloc peak")
    for b in result.batches:
        text.append(
            f"{b.commits:>7} {b.wall_ms:>9} {b.cpu_ms:>9} {b.commits_per_second:>11} "
            f"{b.latency.p50_ms:>8} {b.latency.p95_ms:>8} {b.latency.p99_ms:>8} "
            f"{b.python_peak_allocated_bytes / 1024:>9.0f} KiB"
        )
    text += ["", "Message size    runs   p50 ms    p99 ms   CPU ms/commit   alloc peak   decision"]
    for s in result.message_sizes:
        text.append(
            f"{s.message_bytes:>12} {s.repetitions:>7} {s.latency.p50_ms:>8} "
            f"{s.latency.p99_ms:>9} {s.cpu_ms_per_commit:>15} "
            f"{s.python_peak_allocated_bytes / 1_048_576:>9.1f} MiB   {s.decision}"
        )
    text += ["", f"Peak RSS (whole run)   {_mib(result.peak_rss_bytes)}"]
    decisions_ok = all(s.decision == "block" for s in result.message_sizes)
    emit(
        "performance",
        manifest,
        result,
        as_json=as_json,
        output=output,
        record=record,
        text=text,
        ok=decisions_ok,
    )


@benchmark_app.command("hooks")
def hooks_command(
    as_json: JsonOption = False,
    output: OutputOption = None,
    record: RecordOption = None,
    repetitions: Annotated[int, typer.Option("--repetitions", min=1, max=500)] = 20,
) -> None:
    """Git commit and push with and without CommitGuard hooks, including --no-verify."""
    from commitguard.research.environment import collect_manifest
    from commitguard.research.hooks import run_hooks

    with handled_errors():
        result = run_hooks(repetitions)
        manifest = collect_manifest()
    text = [
        "CommitGuard hook overhead",
        "",
        "Scenario                    p50 ms    p95 ms   exit codes",
    ]
    for s in result.scenarios:
        codes = ", ".join(f"{code}x{count}" for code, count in sorted(s.exit_codes.items()))
        text.append(f"{s.name:<25} {s.latency.p50_ms:>9} {s.latency.p95_ms:>9}   {codes}")
    text += ["", "Overhead (median):"]
    text += [
        f"  {o.operation:<30} +{o.overhead_p50_ms} ms ({o.without_hooks_p50_ms} -> "
        f"{o.with_hooks_p50_ms} ms)"
        for o in result.overhead
    ]
    text += ["", "Observed behaviour:"]
    text += [
        f"  [{'OK' if o.matches else 'MISMATCH'}] {o.check}: {o.observed}"
        for o in result.observations
    ]
    emit(
        "hooks",
        manifest,
        result,
        as_json=as_json,
        output=output,
        record=record,
        text=text,
        ok=all(o.matches for o in result.observations),
    )


@benchmark_app.command("repository")
def repository_command(
    as_json: JsonOption = False,
    output: OutputOption = None,
    record: RecordOption = None,
    sizes: Annotated[
        str, typer.Option("--sizes", help="Comma-separated history sizes.")
    ] = "100,1000,10000,100000",
) -> None:
    """Scan real repository histories of increasing size (list, read, analyse)."""
    from commitguard.research.environment import collect_manifest
    from commitguard.research.repository import run_repository

    with handled_errors():
        try:
            parsed = tuple(int(part) for part in sizes.split(",") if part.strip())
        except ValueError:
            parsed = ()
        if not parsed or any(size < 2 or size > 1_000_000 for size in parsed):
            raise typer.BadParameter("sizes must be integers between 2 and 1000000")
        result = run_repository(parsed)
        manifest = collect_manifest()
    text = [
        "CommitGuard repository history scan",
        "",
        "Commits   create ms   list ms   read ms   analyse ms   total ms   commits/s   violations",
    ]
    for h in result.histories:
        text.append(
            f"{h.commits:>7} {h.create_ms:>11} {h.list_ms:>9} {h.read_ms:>9} {h.analyze_ms:>12} "
            f"{h.scan_total_ms:>10} {h.commits_per_second:>11}   {h.blocked}/{h.seeded_violations}"
        )
    emit(
        "repository",
        manifest,
        result,
        as_json=as_json,
        output=output,
        record=record,
        text=text,
        ok=all(h.all_seeded_violations_found for h in result.histories),
    )


@benchmark_app.command("platform")
def platform_command(
    as_json: JsonOption = False,
    output: OutputOption = None,
    record: RecordOption = None,
) -> None:
    """Validate the local enforcement path on this operating system."""
    from commitguard.research.environment import collect_manifest
    from commitguard.research.platform import run_platform

    with handled_errors():
        result = run_platform()
        manifest = collect_manifest()
    text = [f"CommitGuard platform validation: {result.system}", ""]
    text += [f"  {c.status:<7} {c.area:<24} {c.name}: {c.observed}" for c in result.checks]
    text += ["", f"PASS {result.passed} · FAIL {result.failed} · SKIPPED {result.skipped}"]
    emit(
        "platform",
        manifest,
        result,
        as_json=as_json,
        output=output,
        record=record,
        text=text,
        ok=result.failed == 0,
    )


@benchmark_app.command("compare")
def compare_command(
    results: Annotated[
        Path, typer.Option("--results", help="Results directory (e.g. benchmarks/results).")
    ] = Path("benchmarks/results"),
    benchmark: Annotated[
        str, typer.Option("--benchmark", help="Which benchmark to compare.")
    ] = "detection",
    baseline: Annotated[
        Path | None,
        typer.Option("--baseline", help="Baseline result file (default: the previous run)."),
    ] = None,
    current: Annotated[
        Path | None,
        typer.Option("--current", help="Result file to judge (default: the latest run)."),
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Compare a benchmark result with an earlier one, using documented thresholds.

    Exit codes: 0 when nothing regressed, 1 when something did, 2 on errors. A
    correctness regression counts at any size; a performance regression only
    beyond the threshold for that measure.
    """
    from commitguard.research.compare import THRESHOLDS, compare
    from commitguard.research.results import load_results

    with handled_errors():
        recorded = [
            (path, document)
            for path, document in load_results(results)
            if document.get("benchmark") == benchmark
        ]
        if baseline is None or current is None:
            if len(recorded) < 2:
                fail(
                    f"need two recorded {benchmark} results to compare; "
                    f"{len(recorded)} found in {results}"
                )
            first, second = recorded[-2], recorded[-1]
        else:
            first = (baseline, json.loads(baseline.read_text(encoding="utf-8")))
            second = (current, json.loads(current.read_text(encoding="utf-8")))
        before = {**first[1], "_file": str(first[0])}
        after = {**second[1], "_file": str(second[0])}
        comparison = compare(before, after, benchmark=benchmark)

    if as_json:
        info(json.dumps(comparison.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        info(f"CommitGuard benchmark comparison: {benchmark}")
        info("")
        info(f"  baseline  {comparison.baseline_file}")
        info(f"            {comparison.baseline_environment}")
        info(f"  current   {comparison.current_file}")
        info(f"            {comparison.current_environment}")
        if not comparison.same_environment:
            info("  NOTE: different CPUs; performance numbers are not comparable.")
        if not comparison.same_dataset and comparison.baseline_dataset:
            info(
                f"  NOTE: different datasets ({comparison.baseline_dataset} then "
                f"{comparison.current_dataset}); accuracy counts are not comparable."
            )
        info("")
        info(f"{'Metric':<40}{'baseline':>14}{'current':>14}{'change':>12}   verdict")
        for metric in comparison.metrics:
            change = "-" if metric.change_ratio is None else f"{metric.change_ratio * 100:+.1f}%"
            base = "-" if metric.baseline is None else f"{metric.baseline:,.4g}"
            now = "-" if metric.current is None else f"{metric.current:,.4g}"
            info(f"{metric.name:<40}{base:>14}{now:>14}{change:>12}   {metric.verdict}")
        info("")
        thresholds = ", ".join(f"{k} {v * 100:.0f}%" for k, v in sorted(THRESHOLDS.items()))
        info(f"Thresholds: {thresholds}")
        if comparison.regressions:
            info(f"REGRESSED: {', '.join(comparison.regressions)}")
        else:
            info("No regression.")
        if comparison.improvements:
            info(f"Improved: {', '.join(comparison.improvements)}")
    if not comparison.ok:
        raise typer.Exit(ExitCode.BLOCKED)
