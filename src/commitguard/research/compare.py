"""Comparing a benchmark result with an earlier one.

Results are immutable, so comparing versions means comparing two recorded files.
This module extracts the comparable numbers from each benchmark's result document
and applies documented thresholds.

Two kinds of change are distinguished, because they deserve different responses:

* a **correctness** change - a new false negative or false positive, a platform
  check that no longer passes, a seeded violation that was not found - is a
  regression at any size;
* a **performance** change is a regression only beyond the threshold for that
  measure, and a documented performance regression does not by itself reject a
  release (see docs/maintainers/benchmarking.md).

Comparisons are only meaningful between runs of the same benchmark on comparable
machines; the environment of both runs is reported so that a reader can judge.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Verdict = Literal["improved", "unchanged", "regressed", "new", "missing"]

#: Relative change (of the worse direction) tolerated before a performance metric
#: counts as a regression. Correctness metrics use a threshold of 0.
THRESHOLDS = {
    "latency": 0.20,
    "throughput": 0.20,
    "memory": 0.25,
    "hook_overhead": 0.25,
    "startup": 0.25,
    "correctness": 0.0,
}


class Metric(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    kind: str
    baseline: float | None
    current: float | None
    lower_is_better: bool
    change_ratio: float | None
    verdict: Verdict

    @property
    def correctness(self) -> bool:
        return self.kind == "correctness"


class Comparison(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    benchmark: str
    baseline_file: str
    current_file: str
    baseline_environment: str
    current_environment: str
    same_environment: bool
    same_dataset: bool
    baseline_dataset: str | None
    current_dataset: str | None
    metrics: tuple[Metric, ...]
    regressions: tuple[str, ...]
    improvements: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.regressions


def _environment(document: dict[str, Any]) -> str:
    manifest = document.get("manifest", {})
    return (
        f"{manifest.get('commitguard_version')} on {manifest.get('operating_system')} "
        f"{manifest.get('os_release')} ({manifest.get('cpu_model')}), "
        f"Python {manifest.get('python_version')}"
    )


def _detection_metrics(result: dict[str, Any]) -> dict[str, tuple[float, str, bool]]:
    decision = result.get("decision", {})
    latency = result.get("latency", {})
    return {
        "false negatives": (decision.get("false_negative", 0), "correctness", True),
        "false positives": (decision.get("false_positive", 0), "correctness", True),
        "exact decisions": (result.get("exact_decision_matches", 0), "correctness", False),
        "latency p50 (ms)": (latency.get("p50_ms", 0.0), "latency", True),
        "latency p95 (ms)": (latency.get("p95_ms", 0.0), "latency", True),
    }


def _performance_metrics(result: dict[str, Any]) -> dict[str, tuple[float, str, bool]]:
    metrics: dict[str, tuple[float, str, bool]] = {
        "rule loading (ms)": (result.get("startup_ms", 0.0), "startup", True)
    }
    for batch in result.get("batches", []):
        commits = batch["commits"]
        metrics[f"{commits} commits: p50 (ms)"] = (batch["latency"]["p50_ms"], "latency", True)
        metrics[f"{commits} commits: commits/s"] = (
            batch["commits_per_second"],
            "throughput",
            False,
        )
    for message in result.get("message_sizes", []):
        size = message["message_bytes"]
        metrics[f"{size} byte message: p50 (ms)"] = (message["latency"]["p50_ms"], "latency", True)
    peak = result.get("peak_rss_bytes")
    if peak:
        metrics["peak RSS (MiB)"] = (peak / 1_048_576, "memory", True)
    return metrics


def _hooks_metrics(result: dict[str, Any]) -> dict[str, tuple[float, str, bool]]:
    metrics: dict[str, tuple[float, str, bool]] = {}
    for overhead in result.get("overhead", []):
        metrics[f"{overhead['operation']}: overhead p50 (ms)"] = (
            overhead["overhead_p50_ms"],
            "hook_overhead",
            True,
        )
    for observation in result.get("observations", []):
        metrics[f"observation: {observation['check']}"] = (
            1.0 if observation.get("matches") else 0.0,
            "correctness",
            False,
        )
    return metrics


def _repository_metrics(result: dict[str, Any]) -> dict[str, tuple[float, str, bool]]:
    metrics: dict[str, tuple[float, str, bool]] = {}
    for history in result.get("histories", []):
        commits = history["commits"]
        metrics[f"{commits} commits: commits/s"] = (
            history["commits_per_second"],
            "throughput",
            False,
        )
        metrics[f"{commits} commits: violations found"] = (
            float(history["blocked"]),
            "correctness",
            False,
        )
    return metrics


def _platform_metrics(result: dict[str, Any]) -> dict[str, tuple[float, str, bool]]:
    return {
        "checks passed": (float(result.get("passed", 0)), "correctness", False),
        "checks failed": (float(result.get("failed", 0)), "correctness", True),
        "checks skipped": (float(result.get("skipped", 0)), "correctness", True),
    }


EXTRACTORS = {
    "detection": _detection_metrics,
    "performance": _performance_metrics,
    "hooks": _hooks_metrics,
    "repository": _repository_metrics,
    "platform": _platform_metrics,
}


def _verdict(
    baseline: float | None, current: float | None, kind: str, lower_is_better: bool
) -> tuple[float | None, Verdict]:
    if baseline is None:
        return None, "new"
    if current is None:
        return None, "missing"
    if baseline == current:
        return 0.0, "unchanged"
    if baseline == 0:
        # No ratio is meaningful; any movement away from zero is a change.
        worse = current > 0 if lower_is_better else current < 0
        return None, "regressed" if worse else "improved"
    ratio = (current - baseline) / abs(baseline)
    worse_by = ratio if lower_is_better else -ratio
    if worse_by > THRESHOLDS.get(kind, 0.0):
        return ratio, "regressed"
    if worse_by < 0:
        return ratio, "improved"
    return ratio, "unchanged"


def compare(baseline: dict[str, Any], current: dict[str, Any], *, benchmark: str) -> Comparison:
    """Compare two recorded result documents of the same benchmark."""
    extract = EXTRACTORS.get(benchmark)
    if extract is None:
        raise ValueError(f"no comparison defined for benchmark {benchmark!r}")
    before, after = extract(baseline.get("result", {})), extract(current.get("result", {}))
    metrics: list[Metric] = []
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name), after.get(name)
        kind = (new or old or (0.0, "correctness", True))[1]
        lower_is_better = (new or old or (0.0, "correctness", True))[2]
        old_value = old[0] if old else None
        new_value = new[0] if new else None
        ratio, verdict = _verdict(old_value, new_value, kind, lower_is_better)
        metrics.append(
            Metric(
                name=name,
                kind=kind,
                baseline=old_value,
                current=new_value,
                lower_is_better=lower_is_better,
                change_ratio=ratio,
                verdict=verdict,
            )
        )
    baseline_environment, current_environment = _environment(baseline), _environment(current)
    return Comparison(
        benchmark=benchmark,
        baseline_file=str(baseline.get("_file", "")),
        current_file=str(current.get("_file", "")),
        baseline_environment=baseline_environment,
        current_environment=current_environment,
        same_environment=baseline.get("manifest", {}).get("cpu_model")
        == current.get("manifest", {}).get("cpu_model"),
        same_dataset=baseline.get("manifest", {}).get("dataset_fingerprint")
        == current.get("manifest", {}).get("dataset_fingerprint"),
        baseline_dataset=baseline.get("manifest", {}).get("dataset_version"),
        current_dataset=current.get("manifest", {}).get("dataset_version"),
        metrics=tuple(metrics),
        regressions=tuple(m.name for m in metrics if m.verdict == "regressed"),
        improvements=tuple(m.name for m in metrics if m.verdict == "improved"),
    )
