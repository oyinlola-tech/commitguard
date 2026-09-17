"""Detection performance: latency, message-size scaling, CPU time and memory.

What is measured, and how:

* **wall-clock** - ``time.perf_counter`` around each ``Analyzer.analyze`` call;
* **CPU time** - ``time.process_time`` over a whole batch (user + system time of
  this process);
* **Python allocations** - ``tracemalloc`` peak during a *separate* pass over the
  same commits: memory allocated by the analysis itself, excluding the
  interpreter's baseline. Timing passes run without ``tracemalloc``, which slows
  Python code by roughly 2-3x;
* **peak RSS** - the process's maximum resident set size (``resource``; not
  available on Windows, reported as ``None`` there). RSS is a process-lifetime
  high-water mark, so it is reported once, after all scenarios.

Batches reuse one analyzer, as the hooks and the GitHub App do. A warm-up pass
runs first so imports and rule compilation are not attributed to the first
sample; rule loading is measured separately (``startup``).
"""

import gc
import sys
import time
import tracemalloc
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from commitguard.git.commit import Commit
from commitguard.policies.defaults import default_policy_set
from commitguard.provenance.author import Identity
from commitguard.research.metrics import LatencySummary
from commitguard.services.analysis import Analyzer

BENCHMARK_VERSION = "1.0.0"
BATCH_SIZES = (1, 10, 100, 1_000, 10_000)
MESSAGE_SIZES = (1_024, 10_240, 102_400, 1_048_576, 10_485_760)

HUMAN = Identity(name="Ada Lovelace", email="ada@example.com")
TRAILER = "Co-authored-by: Claude <noreply@anthropic.com>"


class BatchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    commits: int
    wall_ms: float
    cpu_ms: float
    commits_per_second: float
    latency: LatencySummary
    python_peak_allocated_bytes: int


class SizeResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message_bytes: int
    repetitions: int
    decision: str
    latency: LatencySummary
    cpu_ms_per_commit: float
    python_peak_allocated_bytes: int


class PerformanceResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    benchmark: str = "performance"
    benchmark_version: str = BENCHMARK_VERSION
    startup_ms: float
    batches: tuple[BatchResult, ...]
    message_sizes: tuple[SizeResult, ...]
    peak_rss_bytes: int | None
    notes: tuple[str, ...]


def _mixed_commits(count: int) -> list[Commit]:
    """Realistic mix: 60% clean with human trailers, 35% AI co-author, 5% bot author."""
    commits = []
    for index in range(count):
        kind = index % 20
        if kind < 12:
            message = f"fix: handle case {index}\n\nSigned-off-by: Ada Lovelace <ada@example.com>\n"
            author = HUMAN
        elif kind < 19:
            message = f"feat: add feature {index}\n\n{TRAILER}\n"
            author = HUMAN
        else:
            message = "chore(deps): bump a dependency\n"
            author = Identity(
                name="dependabot[bot]", email="49699333+dependabot[bot]@users.noreply.github.com"
            )
        commits.append(Commit(author=author, committer=author, message=message))
    return commits


def _sized_commit(size: int) -> Commit:
    """A commit of about ``size`` bytes: wrapped prose, then an AI co-author trailer."""
    line = "The storage layer keeps immutable versions of every published policy.\n"
    body = (line * (size // len(line) + 1))[: max(0, size - len(TRAILER) - 40)]
    return Commit(author=HUMAN, committer=HUMAN, message=f"docs: notes\n\n{body}\n\n{TRAILER}\n")


def peak_rss_bytes() -> int | None:
    try:
        import resource
    except ImportError:  # Windows
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)  # macOS: bytes, Linux: KiB


def _allocation_peak(analyzer: Analyzer, commits: Sequence[Commit]) -> int:
    gc.collect()
    tracemalloc.start()
    try:
        for commit in commits:
            analyzer.analyze(commit)
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def _batch(analyzer: Analyzer, commits: Sequence[Commit]) -> BatchResult:
    gc.collect()
    durations = []
    cpu_start = time.process_time()
    wall_start = time.perf_counter()
    for commit in commits:
        begin = time.perf_counter()
        analyzer.analyze(commit)
        durations.append(time.perf_counter() - begin)
    wall = time.perf_counter() - wall_start
    cpu = time.process_time() - cpu_start
    peak = _allocation_peak(analyzer, commits)
    return BatchResult(
        commits=len(commits),
        wall_ms=round(wall * 1000, 3),
        cpu_ms=round(cpu * 1000, 3),
        commits_per_second=round(len(commits) / wall, 1) if wall > 0 else 0.0,
        latency=LatencySummary.from_seconds(durations),
        python_peak_allocated_bytes=peak,
    )


def _size(analyzer: Analyzer, size: int) -> SizeResult:
    commit = _sized_commit(size)
    repetitions = max(5, min(200, 50_000_000 // max(size, 1)))
    gc.collect()
    durations = []
    decision = ""
    cpu_start = time.process_time()
    for _ in range(repetitions):
        begin = time.perf_counter()
        report = analyzer.analyze(commit)
        durations.append(time.perf_counter() - begin)
        decision = report.action.value
    cpu = time.process_time() - cpu_start
    peak = _allocation_peak(analyzer, [commit])
    return SizeResult(
        message_bytes=len(commit.message.encode("utf-8")),
        repetitions=repetitions,
        decision=decision,
        latency=LatencySummary.from_seconds(durations),
        cpu_ms_per_commit=round(cpu * 1000 / repetitions, 4),
        python_peak_allocated_bytes=peak,
    )


def run_performance(
    *,
    batch_sizes: Sequence[int] = BATCH_SIZES,
    message_sizes: Sequence[int] = MESSAGE_SIZES,
) -> PerformanceResult:
    started = time.perf_counter()
    analyzer = Analyzer.create(default_policy_set())
    startup = time.perf_counter() - started
    for commit in _mixed_commits(200):  # warm-up
        analyzer.analyze(commit)
    batches = tuple(_batch(analyzer, _mixed_commits(size)) for size in batch_sizes)
    sizes = tuple(_size(analyzer, size) for size in message_sizes)
    notes = (
        "Commits are built in memory: this isolates detection and policy evaluation from Git I/O "
        "(see the hooks and repository benchmarks for end-to-end Git timings).",
        "startup_ms includes loading and compiling the built-in rules (cached per process).",
        "Allocation peaks come from a separate tracemalloc pass; timings are measured without it.",
    )
    return PerformanceResult(
        startup_ms=round(startup * 1000, 3),
        batches=batches,
        message_sizes=sizes,
        peak_rss_bytes=peak_rss_bytes(),
        notes=notes,
    )
