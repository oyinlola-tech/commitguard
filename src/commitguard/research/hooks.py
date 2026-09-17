"""Git operations with and without CommitGuard hooks.

Two otherwise identical repositories are created: one plain, one with the
CommitGuard hooks (``pre-commit``, ``commit-msg``, ``pre-push``) installed by
the production installer. The same Git commands run against both, repeatedly,
and the difference in wall-clock time is the hook overhead.

The benchmark also records what happened (exit codes), because the timings are
only meaningful if the hooks actually enforced:

* a clean commit is allowed with and without hooks;
* an AI co-authored commit is blocked by the hooks;
* ``git commit --no-verify`` bypasses the local hooks (measured, not assumed);
* a push of a violating commit is blocked by ``pre-push``.
"""

import sys
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from commitguard.git.hooks import install_hooks
from commitguard.git.repository import Repository
from commitguard.research.gitenv import Workspace, workspace
from commitguard.research.metrics import LatencySummary

BENCHMARK_VERSION = "1.0.0"
DEFAULT_REPETITIONS = 20

CLEAN = "feat: add session rotation\n\nSigned-off-by: Ada Lovelace <ada@example.com>\n"
AI = "feat: add payment service\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"


class Scenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    hooks_installed: bool
    exit_codes: dict[str, int]
    latency: LatencySummary


class Overhead(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: str
    without_hooks_p50_ms: float
    with_hooks_p50_ms: float
    overhead_p50_ms: float
    overhead_ratio: float | None


class Observation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    check: str
    expected: str
    observed: str
    matches: bool


class HooksResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    benchmark: str = "hooks"
    benchmark_version: str = BENCHMARK_VERSION
    repetitions: int
    python: str
    scenarios: tuple[Scenario, ...]
    overhead: tuple[Overhead, ...]
    observations: tuple[Observation, ...]
    notes: tuple[str, ...]


def _measure(
    name: str,
    description: str,
    hooked: bool,
    repetitions: int,
    run: Callable[[int], tuple[float, int]],
) -> Scenario:
    durations = []
    codes: dict[str, int] = {}
    for index in range(repetitions):
        seconds, code = run(index)
        durations.append(seconds)
        codes[str(code)] = codes.get(str(code), 0) + 1
    return Scenario(
        name=name,
        description=description,
        hooks_installed=hooked,
        exit_codes=codes,
        latency=LatencySummary.from_seconds(durations),
    )


def _repository(space: Workspace, name: str, hooked: bool) -> tuple[Repository, str]:
    remote = space.init(f"{name}-remote.git", bare=True)
    path = space.init(name)
    space.git(path, "remote", "add", "origin", str(remote))
    space.git(path, "commit", "--quiet", "--allow-empty", "--no-verify", "-m", "chore: initial")
    space.git(path, "push", "--quiet", "--no-verify", "origin", "main")
    repository = Repository.discover(path)
    if hooked:
        install_hooks(repository, python=sys.executable)
    return repository, name


def run_hooks(repetitions: int = DEFAULT_REPETITIONS) -> HooksResult:
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    with workspace() as space:
        plain, _ = _repository(space, "plain", hooked=False)
        hooked, _ = _repository(space, "hooked", hooked=True)
        p, h = plain.root, hooked.root

        def commit(path: Path, message: str, *extra: str) -> Callable[[int], tuple[float, int]]:
            def run(_: int) -> tuple[float, int]:
                seconds, result = space.timed_git(
                    path, "commit", "--quiet", "--allow-empty", *extra, "-m", message
                )
                return seconds, result.returncode

            return run

        def push(
            label: str, path: Path, message: str, *extra: str
        ) -> Callable[[int], tuple[float, int]]:
            def run(index: int) -> tuple[float, int]:
                branch = f"{label}-{index}"  # unique: a reused name could be rejected by the remote
                # Start from the remote state so earlier scenarios cannot leak commits in.
                space.git(path, "checkout", "--quiet", "-B", branch, "origin/main")
                space.git(path, "commit", "--quiet", "--allow-empty", "--no-verify", "-m", message)
                seconds, result = space.timed_git(path, "push", "--quiet", *extra, "origin", branch)
                return seconds, result.returncode

            return run

        scenarios = (
            _measure(
                "commit-clean-plain",
                "git commit, clean message, no hooks",
                False,
                repetitions,
                commit(p, CLEAN),
            ),
            _measure(
                "commit-clean-hooked",
                "git commit, clean message, CommitGuard hooks",
                True,
                repetitions,
                commit(h, CLEAN),
            ),
            _measure(
                "commit-ai-hooked",
                "git commit, AI co-author, CommitGuard hooks",
                True,
                repetitions,
                commit(h, AI),
            ),
            _measure(
                "commit-ai-no-verify",
                "git commit --no-verify, AI co-author, hooks installed",
                True,
                repetitions,
                commit(h, AI, "--no-verify"),
            ),
            _measure(
                "push-clean-plain",
                "git push of one new clean commit, no hooks",
                False,
                repetitions,
                push("push-clean-plain", p, CLEAN),
            ),
            _measure(
                "push-clean-hooked",
                "git push of one new clean commit, CommitGuard pre-push",
                True,
                repetitions,
                push("push-clean-hooked", h, CLEAN),
            ),
            _measure(
                "push-ai-hooked",
                "git push of one new AI co-authored commit, pre-push",
                True,
                repetitions,
                push("push-ai-hooked", h, AI),
            ),
            _measure(
                "push-ai-no-verify",
                "git push --no-verify of an AI co-authored commit",
                True,
                repetitions,
                push("push-ai-no-verify", h, AI, "--no-verify"),
            ),
        )
    by_name = {s.name: s for s in scenarios}

    def overhead(operation: str, plain_name: str, hooked_name: str) -> Overhead:
        base, with_hooks = by_name[plain_name].latency.p50_ms, by_name[hooked_name].latency.p50_ms
        return Overhead(
            operation=operation,
            without_hooks_p50_ms=base,
            with_hooks_p50_ms=with_hooks,
            overhead_p50_ms=round(with_hooks - base, 3),
            overhead_ratio=round(with_hooks / base, 2) if base > 0 else None,
        )

    def observe(check: str, scenario: str, expected_code: str) -> Observation:
        codes = by_name[scenario].exit_codes
        observed = ", ".join(f"exit {code} x{count}" for code, count in sorted(codes.items()))
        matches = set(codes) == {expected_code} if expected_code == "0" else "0" not in codes
        return Observation(
            check=check,
            expected=f"exit {'0' if expected_code == '0' else 'non-zero'} every time",
            observed=observed,
            matches=matches,
        )

    observations = (
        observe("clean commit allowed without hooks", "commit-clean-plain", "0"),
        observe("clean commit allowed with hooks", "commit-clean-hooked", "0"),
        observe("AI co-authored commit blocked by commit hooks", "commit-ai-hooked", "!0"),
        observe("--no-verify bypasses local commit hooks", "commit-ai-no-verify", "0"),
        observe("clean push allowed by pre-push", "push-clean-hooked", "0"),
        observe("AI co-authored push blocked by pre-push", "push-ai-hooked", "!0"),
        observe("--no-verify bypasses the local pre-push hook", "push-ai-no-verify", "0"),
    )
    return HooksResult(
        repetitions=repetitions,
        python=sys.executable,
        scenarios=scenarios,
        overhead=(
            overhead("git commit (clean)", "commit-clean-plain", "commit-clean-hooked"),
            overhead("git push (clean, one commit)", "push-clean-plain", "push-clean-hooked"),
        ),
        observations=observations,
        notes=(
            "Each hook starts a Python interpreter; interpreter start-up is part of the "
            "measured overhead.",
            "Pushes go to a local bare repository, so network latency is excluded.",
            "--no-verify results demonstrate that local hooks are advisory; server-side "
            "enforcement is measured separately.",
        ),
    )
