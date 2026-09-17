"""Measurement arithmetic: confusion matrices and latency statistics.

CommitGuard's detection is deterministic and rule-based. Precision, recall and
F1 are reported because they are the standard way to state how often a
classifier is right on a labelled set - not to suggest a statistical model.
Undefined ratios (a zero denominator) are ``None``, never a made-up 0 or 1.
"""

import math
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict


class Confusion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int

    @property
    def total(self) -> int:
        return self.true_positive + self.false_positive + self.true_negative + self.false_negative

    def rates(self) -> dict[str, float | None]:
        tp, fp, tn, fn = (
            self.true_positive,
            self.false_positive,
            self.true_negative,
            self.false_negative,
        )
        precision = _ratio(tp, tp + fp)
        recall = _ratio(tp, tp + fn)
        f1 = (
            None
            if precision is None or recall is None or precision + recall == 0
            else 2 * precision * recall / (precision + recall)
        )
        return {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "accuracy": _ratio(tp + tn, self.total),
            "false_positive_rate": _ratio(fp, fp + tn),
            "false_negative_rate": _ratio(fn, fn + tp),
        }


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile of an ascending sequence (no interpolation)."""
    if not sorted_values:
        raise ValueError("no values")
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[rank - 1]


class LatencySummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    samples: int
    min_ms: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float

    @classmethod
    def from_seconds(cls, durations: Sequence[float]) -> "LatencySummary":
        values = sorted(d * 1000 for d in durations)
        return cls(
            samples=len(values),
            min_ms=round(values[0], 4),
            mean_ms=round(sum(values) / len(values), 4),
            p50_ms=round(percentile(values, 0.50), 4),
            p95_ms=round(percentile(values, 0.95), 4),
            p99_ms=round(percentile(values, 0.99), 4),
            max_ms=round(values[-1], 4),
        )
