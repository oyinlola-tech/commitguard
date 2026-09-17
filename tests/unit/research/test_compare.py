"""Benchmark comparison: correctness regressions at any size, performance by threshold."""

from typing import Any

import pytest

from commitguard.research.compare import THRESHOLDS, compare


def detection_document(
    *, false_negative: int = 0, false_positive: int = 0, p50: float = 0.2, dataset: str = "1.3.0"
) -> dict[str, Any]:
    return {
        "benchmark": "detection",
        "manifest": {
            "cpu_model": "Test CPU",
            "dataset_version": dataset,
            "dataset_fingerprint": f"fingerprint-{dataset}",
            "commitguard_version": "0.1.0.dev0",
        },
        "result": {
            "decision": {"false_negative": false_negative, "false_positive": false_positive},
            "exact_decision_matches": 9174 - false_negative - false_positive,
            "latency": {"p50_ms": p50, "p95_ms": p50 * 2},
        },
    }


def test_a_new_false_negative_is_a_regression_however_small() -> None:
    result = compare(
        detection_document(), detection_document(false_negative=1), benchmark="detection"
    )
    assert not result.ok
    assert "false negatives" in result.regressions


def test_a_fixed_false_negative_is_an_improvement() -> None:
    result = compare(
        detection_document(false_negative=15), detection_document(), benchmark="detection"
    )
    assert result.ok
    assert "false negatives" in result.improvements


def test_identical_results_are_unchanged() -> None:
    result = compare(detection_document(), detection_document(), benchmark="detection")
    assert result.ok
    assert result.improvements == ()
    assert all(metric.verdict == "unchanged" for metric in result.metrics)


@pytest.mark.parametrize(
    ("factor", "expected"),
    [(1.1, "unchanged"), (1.5, "regressed"), (0.5, "improved")],
)
def test_latency_uses_a_threshold(factor: float, expected: str) -> None:
    result = compare(
        detection_document(p50=0.2), detection_document(p50=0.2 * factor), benchmark="detection"
    )
    latency = next(m for m in result.metrics if m.name == "latency p50 (ms)")
    assert latency.verdict == expected
    assert THRESHOLDS["latency"] == 0.20


def test_different_datasets_and_machines_are_reported() -> None:
    baseline = detection_document(dataset="1.2.0")
    current = detection_document(dataset="1.3.0")
    current["manifest"]["cpu_model"] = "Another CPU"
    result = compare(baseline, current, benchmark="detection")
    assert not result.same_dataset
    assert not result.same_environment
    assert (result.baseline_dataset, result.current_dataset) == ("1.2.0", "1.3.0")


def test_platform_and_repository_correctness_metrics() -> None:
    def platform(passed: int, failed: int) -> dict[str, Any]:
        return {"manifest": {}, "result": {"passed": passed, "failed": failed, "skipped": 0}}

    result = compare(platform(15, 0), platform(14, 1), benchmark="platform")
    assert set(result.regressions) == {"checks passed", "checks failed"}

    def repository(found: int) -> dict[str, Any]:
        return {
            "manifest": {},
            "result": {
                "histories": [
                    {"commits": 100, "commits_per_second": 3000.0, "blocked": found},
                ]
            },
        }

    missed = compare(repository(5), repository(4), benchmark="repository")
    assert "100 commits: violations found" in missed.regressions


def test_an_unknown_benchmark_is_an_error() -> None:
    with pytest.raises(ValueError, match="no comparison defined"):
        compare({}, {}, benchmark="nonexistent")
