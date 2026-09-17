"""Security and benchmark reports assembled from recorded evidence.

Nothing here computes a result: every number is read from a recorded benchmark
result (``benchmarks/results/raw/``) or from evidence written by the test suites
(``COMMITGUARD_EVIDENCE_DIR``). Anything that has no evidence is reported as
``Not tested`` rather than left out, so the report cannot quietly overstate what
is known.

Every statement carries one label:

``Measured``   a number from a recorded benchmark run on a stated machine;
``Tested``     an automated test asserts it (test suites, experiments);
``Observed``   an experiment recorded what actually happened, including failures
               and documented bypasses;
``Expected``   design intent that no test covers yet;
``Not tested`` no evidence.
"""

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from commitguard import __version__
from commitguard.research.results import latest, load_results

LABELS = ("Measured", "Tested", "Observed", "Expected", "Not tested")
BENCHMARKS = ("detection", "performance", "hooks", "repository", "platform")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return entries
    for line in text.splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    return entries


def _environment(manifest: dict[str, Any]) -> str:
    return (
        f"{manifest.get('operating_system')} {manifest.get('os_release')} "
        f"({manifest.get('machine')}), {manifest.get('cpu_model')}, "
        f"{manifest.get('cpu_count')} CPUs, Python {manifest.get('python_version')}, "
        f"Git {manifest.get('git_version')}"
    )


def collect(results_dir: Path, evidence_dir: Path | None) -> dict[str, Any]:
    """Gather every recorded result and piece of evidence into one document."""
    documents = {name: latest(results_dir, name) for name in BENCHMARKS}
    history = [
        {
            "benchmark": document.get("benchmark"),
            "file": path.relative_to(results_dir).as_posix(),
            "timestamp": document.get("manifest", {}).get("timestamp"),
            "commitguard_version": document.get("manifest", {}).get("commitguard_version"),
            "dataset_version": document.get("manifest", {}).get("dataset_version"),
            "source_revision": document.get("manifest", {}).get("source_revision"),
        }
        for path, document in load_results(results_dir)
    ]
    experiments = _read_jsonl(evidence_dir / "experiments.jsonl") if evidence_dir else []
    fuzzing = _read_json(evidence_dir / "fuzzing.json") if evidence_dir else None
    redos = _read_json(evidence_dir / "redos.json") if evidence_dir else None
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "commitguard_version": __version__,
        "results_directory": str(results_dir),
        "evidence_directory": str(evidence_dir) if evidence_dir else None,
        "latest": {
            name: (
                {"file": path.relative_to(results_dir).as_posix(), **document}
                if (pair := documents[name]) and (path := pair[0]) and (document := pair[1])
                else None
            )
            for name in BENCHMARKS
        },
        "history": history,
        "experiments": experiments,
        "fuzzing": fuzzing,
        "redos": redos,
    }


def _detection_section(document: dict[str, Any] | None) -> list[str]:
    if document is None:
        return ["| Detection accuracy | Not tested | no recorded detection run |"]
    result, manifest = document["result"], document["manifest"]
    decision = result["decision"]
    rates = result["decision_rates"]

    def percent(key: str) -> str:
        value = rates.get(key)
        return "n/a" if value is None else f"{value * 100:.3f}%"

    return [
        f"| False negatives | Measured | {decision['false_negative']} of {result['positives']} "
        f"cases that must not be allowed (dataset {manifest.get('dataset_version')}) |",
        f"| False positives | Measured | {decision['false_positive']} of {result['negatives']} "
        f"clean cases |",
        f"| Precision / recall | Measured | {percent('precision')} / {percent('recall')} |",
        f"| False positive rate / false negative rate | Measured | {percent('false_positive_rate')}"
        f" / {percent('false_negative_rate')} |",
        f"| Detection latency per commit | Measured | p50 {result['latency']['p50_ms']:.4f} ms, "
        f"p95 {result['latency']['p95_ms']:.4f} ms, p99 {result['latency']['p99_ms']:.4f} ms |",
    ]


def _performance_section(document: dict[str, Any] | None) -> list[str]:
    if document is None:
        return ["| Performance | Not tested | no recorded performance run |"]
    result = document["result"]
    batches = result.get("batches") or []
    messages = result.get("message_sizes") or []
    lines = []
    if batches:
        biggest = batches[-1]
        lines.append(
            f"| Throughput | Measured | {biggest['commits_per_second']:.1f} commits/s on "
            f"{biggest['commits']} commits "
            f"(p50 {biggest['latency']['p50_ms']:.4f} ms per commit) |"
        )
    if messages:
        biggest = messages[-1]
        lines.append(
            f"| Large commit messages | Measured | {biggest['message_bytes']:,} byte message: "
            f"p50 {biggest['latency']['p50_ms']:.3f} ms |"
        )
    peak = result.get("peak_rss_bytes")
    if peak:
        lines.append(f"| Peak memory | Measured | {peak / 1048576:.1f} MiB for the whole run |")
    return lines or ["| Performance | Not tested | recorded run has no measurements |"]


def _hooks_section(document: dict[str, Any] | None) -> list[str]:
    if document is None:
        return ["| Hook overhead | Not tested | no recorded hooks run |"]
    result = document["result"]
    lines = []
    for overhead in result.get("overhead", []):
        lines.append(
            f"| Hook overhead: {overhead['operation']} | Measured | "
            f"+{overhead['overhead_p50_ms']:.0f} ms "
            f"({overhead['without_hooks_p50_ms']:.1f} -> {overhead['with_hooks_p50_ms']:.1f} ms, "
            f"median of {result.get('repetitions', '?')} runs) |"
        )
    for observation in result.get("observations", []):
        lines.append(
            f"| {observation['check']} | Observed | {observation['observed']} "
            f"(expected {observation['expected']}) |"
        )
    return lines or ["| Hook overhead | Not tested | recorded run has no measurements |"]


def _platform_section(document: dict[str, Any] | None) -> list[str]:
    if document is None:
        return ["| Cross-platform validation | Not tested | no recorded platform run |"]
    result = document["result"]
    return [
        f"| Local enforcement on {result['system']} | Tested | {result['passed']} passed, "
        f"{result['failed']} failed, {result['skipped']} skipped |"
    ]


def _repository_section(document: dict[str, Any] | None) -> list[str]:
    if document is None:
        return ["| Repository history scanning | Not tested | no recorded repository run |"]
    histories = document["result"].get("histories") or []
    if not histories:
        return ["| Repository history scanning | Not tested | recorded run has no measurements |"]
    biggest = histories[-1]
    return [
        f"| Repository history scanning | Measured | {biggest['commits']} commits in "
        f"{biggest['scan_total_ms'] / 1000:.1f} s ({biggest['commits_per_second']:.0f} commits/s); "
        f"{biggest['blocked']} of {biggest['seeded_violations']} seeded violations found |"
    ]


def _experiment_rows(experiments: Iterable[dict[str, Any]]) -> list[str]:
    rows = []
    for entry in sorted(
        experiments, key=lambda item: (item.get("area", ""), item.get("attack", ""))
    ):
        rows.append(
            f"| {entry.get('area', '')} | {entry.get('attack', '')} | "
            f"**{entry.get('outcome', '')}** | {entry.get('observed', '')} | "
            f"{entry.get('mitigation', '')} |"
        )
    return rows


def render_security_report(data: dict[str, Any]) -> str:
    latest_results = data["latest"]
    detection = latest_results.get("detection")
    lines = [
        "# CommitGuard security report",
        "",
        f"Generated {data['generated_at']} for CommitGuard {data['commitguard_version']}.",
        "",
        "Every row is labelled: **Measured** (a number from a recorded benchmark run),",
        "**Tested** (an automated test asserts it), **Observed** (an experiment recorded what",
        "happened, including bypasses), **Expected** (design intent, not yet covered by a test)",
        "or **Not tested**. Nothing in this report is estimated.",
        "",
    ]
    if detection:
        lines += [
            "## Environment of the latest runs",
            "",
            f"- Detection: {_environment(detection['manifest'])}",
            f"- Source revision: `{detection['manifest'].get('source_revision')}`"
            f" (dirty: {detection['manifest'].get('source_dirty')})",
            "",
        ]
    lines += [
        "## Detection",
        "",
        "| Statement | Label | Evidence |",
        "|---|---|---|",
        *_detection_section(detection),
        "",
        "## Performance",
        "",
        "| Statement | Label | Evidence |",
        "|---|---|---|",
        *_performance_section(latest_results.get("performance")),
        *_repository_section(latest_results.get("repository")),
        *_hooks_section(latest_results.get("hooks")),
        "",
        "## Cross-platform validation",
        "",
        "| Statement | Label | Evidence |",
        "|---|---|---|",
        *_platform_section(latest_results.get("platform")),
        "",
    ]
    experiments = data.get("experiments") or []
    lines += ["## Security experiments", ""]
    if experiments:
        outcomes: dict[str, int] = {}
        for entry in experiments:
            outcome = str(entry.get("outcome", "unknown"))
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        lines += [
            f"{len(experiments)} recorded experiments: "
            + ", ".join(f"{count} {name}" for name, count in sorted(outcomes.items()))
            + ".",
            "",
            "| Area | Attack | Outcome | Observed | Mitigation |",
            "|---|---|---|---|---|",
            *_experiment_rows(experiments),
            "",
        ]
    else:
        lines += [
            "Not tested here: no experiment evidence was found. Run",
            "`commitguard reproduce security --evidence-dir <dir>` first.",
            "",
        ]
    fuzzing, redos = data.get("fuzzing"), data.get("redos")
    lines += ["## Fuzzing and ReDoS", ""]
    if fuzzing:
        lines += [
            f"- **Tested**: {fuzzing['total_executed']} property-based examples across "
            f"{len(fuzzing['executed'])} properties "
            f"({'derandomized' if fuzzing.get('derandomized') else 'randomized'}).",
        ]
    else:
        lines.append("- **Not tested**: no fuzzing evidence found.")
    if redos:
        worst = max((item["worst_seconds"] for item in redos["patterns"].values()), default=0.0)
        lines += [
            f"- **Measured**: {len(redos['patterns'])} regular expressions run against "
            f"{redos['adversarial_inputs_per_pattern']} adversarial inputs of "
            f"{redos['input_characters']} characters; worst case {worst * 1000:.1f} ms "
            f"(budget {redos['budget_seconds'] * 1000:.0f} ms).",
        ]
    else:
        lines.append("- **Not tested**: no ReDoS evidence found.")
    lines += [
        "",
        "## Recorded runs",
        "",
        "Results are immutable: a new run is a new file, and earlier results are kept.",
        "",
        "| Benchmark | Runs |",
        "|---|---|",
    ]
    counts: dict[str, int] = {}
    for entry in data["history"]:
        name = str(entry.get("benchmark"))
        counts[name] = counts.get(name, 0) + 1
    for name in sorted(counts):
        lines.append(f"| {name} | {counts[name]} |")
    lines += [
        "",
        "## What this report does not say",
        "",
        "- It does not claim CommitGuard cannot be bypassed: local hooks are bypassable by the",
        "  person running them, and the experiments record exactly that.",
        "- Results labelled Measured come from the machine named above, not from a controlled",
        "  laboratory, and were not repeated across machines.",
        "- No external party has reproduced these results yet.",
        "",
    ]
    return "\n".join(lines) + "\n"


def render_benchmark_report(data: dict[str, Any]) -> str:
    lines = [
        "# CommitGuard benchmark report",
        "",
        f"Generated {data['generated_at']} for CommitGuard {data['commitguard_version']}.",
        "",
        "Every number below is read from a recorded result file; none is recomputed here.",
        "",
        "| Benchmark | Latest run | Version | Environment |",
        "|---|---|---|---|",
    ]
    for name in BENCHMARKS:
        document = data["latest"].get(name)
        if document is None:
            lines.append(f"| {name} | not recorded | - | - |")
            continue
        manifest = document["manifest"]
        lines.append(
            f"| {name} | `{document['file']}` | {manifest.get('commitguard_version')} | "
            f"{manifest.get('operating_system')} {manifest.get('os_release')}, "
            f"Python {manifest.get('python_version')} |"
        )
    lines += [
        "",
        "## All recorded runs",
        "",
        "| Benchmark | File | Timestamp | Dataset |",
        "|---|---|---|---|",
    ]
    for entry in data["history"]:
        lines.append(
            f"| {entry['benchmark']} | `{entry['file']}` | {entry['timestamp']} | "
            f"{entry.get('dataset_version') or '-'} |"
        )
    return "\n".join(lines) + "\n"


def write_reports(data: dict[str, Any], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in (
        ("security-report.json", json.dumps(data, indent=2, sort_keys=True) + "\n"),
        ("security-report.md", render_security_report(data)),
        ("benchmark-report.md", render_benchmark_report(data)),
    ):
        target = output_dir / name
        target.write_text(content, encoding="utf-8")
        written.append(target)
    return written
