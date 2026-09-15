"""Counters for the GitHub App service.

A deliberately small abstraction: services call ``metrics.increment(name)``
and a deployment can later bridge :class:`Metrics` to Prometheus, StatsD or
OpenTelemetry. The in-memory implementation is exposed to the readiness probe
and tests only; metric labels never contain secrets or commit data.
"""

import threading
from collections import Counter
from typing import Protocol

WEBHOOKS_RECEIVED = "webhooks_received"
WEBHOOKS_REJECTED = "webhooks_rejected"
WEBHOOKS_DUPLICATE = "webhooks_duplicate"
SCANS_QUEUED = "scans_queued"
SCANS_STARTED = "scans_started"
SCANS_COMPLETED = "scans_completed"
SCANS_FAILED = "scans_failed"
SCANS_CANCELLED = "scans_cancelled"
POLICY_VIOLATIONS = "policy_violations"
GITHUB_API_ERRORS = "github_api_errors"
GITHUB_RATE_LIMITS = "github_rate_limits"
GITHUB_EVENTS_RECEIVED = "github_events_received"
GITHUB_EVENTS_FAILED = "github_events_failed"
GITHUB_EVENTS_REPLAYED = "github_events_replayed"
MERGE_GROUPS_SCANNED = "merge_groups_scanned"
MERGE_GROUPS_FAILED = "merge_groups_failed"
CHECK_RERUNS = "check_reruns"
SCAN_RETRIES = "scan_retries"
POLICY_ROLLBACKS = "policy_rollbacks"
POLICY_ROLLBACK_FAILURES = "policy_rollback_failures"
NOTIFICATIONS_CREATED = "notifications_created"
NOTIFICATIONS_SENT = "notifications_sent"
NOTIFICATIONS_FAILED = "notifications_failed"
NOTIFICATION_RETRIES = "notification_retries"

KNOWN_METRICS = frozenset(
    {
        WEBHOOKS_RECEIVED,
        WEBHOOKS_REJECTED,
        WEBHOOKS_DUPLICATE,
        SCANS_QUEUED,
        SCANS_STARTED,
        SCANS_COMPLETED,
        SCANS_FAILED,
        SCANS_CANCELLED,
        POLICY_VIOLATIONS,
        GITHUB_API_ERRORS,
        GITHUB_RATE_LIMITS,
        GITHUB_EVENTS_RECEIVED,
        GITHUB_EVENTS_FAILED,
        GITHUB_EVENTS_REPLAYED,
        MERGE_GROUPS_SCANNED,
        MERGE_GROUPS_FAILED,
        CHECK_RERUNS,
        SCAN_RETRIES,
        POLICY_ROLLBACKS,
        POLICY_ROLLBACK_FAILURES,
        NOTIFICATIONS_CREATED,
        NOTIFICATIONS_SENT,
        NOTIFICATIONS_FAILED,
        NOTIFICATION_RETRIES,
    }
)


class Metrics(Protocol):
    def increment(self, name: str, value: int = 1, **labels: str) -> None: ...


class NullMetrics:
    def increment(self, name: str, value: int = 1, **labels: str) -> None:
        return None


class InMemoryMetrics:
    """Thread-safe counters keyed by metric name and labels."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()

    def increment(self, name: str, value: int = 1, **labels: str) -> None:
        if name not in KNOWN_METRICS:
            raise ValueError(f"unknown metric {name!r}")
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._counters[key] += value

    def value(self, name: str, **labels: str) -> int:
        """Sum of a counter over all label sets matching ``labels``."""
        with self._lock:
            return sum(
                count
                for (metric, metric_labels), count in self._counters.items()
                if metric == name and set(labels.items()) <= set(metric_labels)
            )

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            totals: Counter[str] = Counter()
            for (metric, _), count in self._counters.items():
                totals[metric] += count
        return dict(sorted(totals.items()))
