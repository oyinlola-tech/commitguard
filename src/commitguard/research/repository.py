"""Scanning real repository histories of increasing size.

For each size a repository is created with ``git fast-import`` (every 20th
commit carries an AI co-author trailer, one in twenty-five is authored by an
automation account through its message only - the mix matters less than the
count). The measured operation is what ``commitguard scan`` and the CI and App
range scans do: list the commits of a range with ``git rev-list``, read their
metadata with batched ``git cat-file``, and analyse each commit.

Reported per size: repository creation time (not part of scanning), listing,
reading and analysis time separately, commits per second, and whether every
seeded violation was found.
"""

import time
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from commitguard.core.decision import Action
from commitguard.git.repository import Repository
from commitguard.policies.defaults import default_policy_set
from commitguard.research.gitenv import fast_import_stream, workspace
from commitguard.services.analysis import Analyzer

BENCHMARK_VERSION = "1.0.0"
HISTORY_SIZES = (100, 1_000, 10_000, 100_000)
AI_EVERY = 20


class HistoryResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    commits: int
    create_ms: float
    list_ms: float
    read_ms: float
    analyze_ms: float
    scan_total_ms: float
    commits_per_second: float
    seeded_violations: int
    blocked: int
    all_seeded_violations_found: bool


class RepositoryResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    benchmark: str = "repository"
    benchmark_version: str = BENCHMARK_VERSION
    histories: tuple[HistoryResult, ...]
    notes: tuple[str, ...]


def _messages(count: int) -> list[str]:
    return [
        f"feat: change {i}\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
        if i % AI_EVERY == AI_EVERY - 1
        else f"fix: change {i}\n\nSigned-off-by: Ada Lovelace <ada@example.com>\n"
        for i in range(count)
    ]


def run_repository(sizes: Sequence[int] = HISTORY_SIZES) -> RepositoryResult:
    analyzer = Analyzer.create(default_policy_set())
    histories = []
    for size in sizes:
        if size < 2:
            raise ValueError("history sizes must be at least 2")
        with workspace() as space:
            path = space.init(f"history-{size}")
            messages = _messages(size)
            started = time.perf_counter()
            space.git(path, "fast-import", "--quiet", input_bytes=fast_import_stream(messages))
            space.git(path, "checkout", "--quiet", "main")
            create = time.perf_counter() - started

            repository = Repository.discover(path)
            first = repository.resolve_commit("main~" + str(size - 1))
            started = time.perf_counter()
            shas = [first, *repository.list_commits(f"{first}..main", max_count=size)]
            listed = time.perf_counter()
            commits = repository.read_commits(shas)
            read = time.perf_counter()
            blocked = sum(1 for c in commits if analyzer.analyze(c).action is Action.BLOCK)
            analyzed = time.perf_counter()
        seeded = sum(1 for i in range(size) if i % AI_EVERY == AI_EVERY - 1)
        total = analyzed - started
        histories.append(
            HistoryResult(
                commits=len(shas),
                create_ms=round(create * 1000, 1),
                list_ms=round((listed - started) * 1000, 1),
                read_ms=round((read - listed) * 1000, 1),
                analyze_ms=round((analyzed - read) * 1000, 1),
                scan_total_ms=round(total * 1000, 1),
                commits_per_second=round(len(shas) / total, 1) if total > 0 else 0.0,
                seeded_violations=seeded,
                blocked=blocked,
                all_seeded_violations_found=blocked == seeded,
            )
        )
    return RepositoryResult(
        histories=tuple(histories),
        notes=(
            "The commitguard scan command limits a range to 1,000 commits by default (a safety "
            "bound); this benchmark calls the same library functions without that bound to "
            "measure scaling.",
            "Repositories are local; fetch time from a remote is not included.",
        ),
    )
