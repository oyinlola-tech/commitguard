"""A portable validation of the local enforcement path on this machine.

Designed to run unchanged on Linux, macOS and Windows (GitHub Actions runners
for the latter two), so the compatibility matrix is built from observed results
rather than from what should work. Every check records what was expected and
what was observed; a check that could not run is ``SKIPPED`` with the reason,
never ``PASS``.

The repository lives in a directory whose path contains spaces and non-ASCII
characters, because path handling is where Git hooks most often break across
platforms.
"""

import os
import platform as platform_module
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from commitguard.git.hooks import (
    HookState,
    HookStatus,
    HookType,
    hook_status,
    install_hooks,
    repository_hooks_dir,
    uninstall_hooks,
)
from commitguard.git.repository import Repository
from commitguard.research.gitenv import Workspace, workspace
from commitguard.utils.subprocess import run_command

BENCHMARK_VERSION = "1.0.0"
Status = Literal["PASS", "FAIL", "SKIPPED"]

CLEAN = "feat: add session rotation\n\nSigned-off-by: Ada Lovelace <ada@example.com>\n"
AI = "feat: add payment service\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"


class Check(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    area: str
    name: str
    status: Status
    expected: str
    observed: str


class PlatformResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    benchmark: str = "platform"
    benchmark_version: str = BENCHMARK_VERSION
    system: str
    shell_for_hooks: str
    checks: tuple[Check, ...]
    passed: int
    failed: int
    skipped: int


def _statuses(repository: Repository) -> list[HookStatus]:
    directory = repository_hooks_dir(repository)
    return [hook_status(directory, hook, expected_python=sys.executable) for hook in HookType]


def _cli(space: Workspace, cwd: Path, *args: str) -> tuple[int, str]:
    result = run_command(
        [sys.executable, "-P", "-m", "commitguard", *args],
        cwd=cwd,
        env_overrides={**space.env, "PYTHONIOENCODING": "utf-8"},
        timeout=300,
    )
    output = (result.stdout + result.stderr).decode("utf-8", "replace")
    return result.returncode, output[-300:]


def run_platform() -> PlatformResult:
    checks: list[Check] = []

    def record(area: str, name: str, expected: str, probe: Callable[[], tuple[bool, str]]) -> bool:
        try:
            ok, observed = probe()
        except Exception as exc:  # noqa: BLE001 - a failing probe is a FAIL, not a crash
            ok, observed = False, f"{type(exc).__name__}: {exc}"[:300]
        checks.append(
            Check(
                area=area,
                name=name,
                status="PASS" if ok else "FAIL",
                expected=expected,
                observed=observed,
            )
        )
        return ok

    with workspace() as space:
        record(
            "environment",
            "git is available",
            "git --version succeeds",
            lambda: (
                (r := space.git(space.root, "--version", check=False)).returncode == 0,
                r.stdout.decode("utf-8", "replace").strip(),
            ),
        )
        base = space.root / "path with spaces" / "répertoire-ünïcøde"
        base.mkdir(parents=True)
        remote = space.init("remote.git", bare=True)
        space.git(base.parent, "init", "--quiet", str(base))
        path = base
        space.git(path, "remote", "add", "origin", str(remote))
        space.git(path, "commit", "--quiet", "--allow-empty", "--no-verify", "-m", "chore: init")
        space.git(path, "push", "--quiet", "--no-verify", "origin", "main")
        repository = Repository.discover(path)

        installed = record(
            "hooks",
            "install hooks (path with spaces and non-ASCII characters)",
            "pre-commit, commit-msg and pre-push installed",
            lambda: (
                all(
                    r.action.value in ("installed", "updated", "unchanged")
                    for r in install_hooks(repository, python=sys.executable)
                ),
                ", ".join(f"{s.hook.value}={s.state.value}" for s in _statuses(repository)),
            ),
        )
        if installed:
            record(
                "hooks",
                "hook integrity",
                "every hook reports installed",
                lambda: (
                    all(s.state is HookState.INSTALLED for s in _statuses(repository)),
                    ", ".join(f"{s.hook.value}={s.state.value}" for s in _statuses(repository)),
                ),
            )

        def head() -> str:
            return space.git(path, "rev-parse", "HEAD").stdout.decode().strip()

        def commit_probe(
            message: str, *extra: str, allowed: bool
        ) -> Callable[[], tuple[bool, str]]:
            def probe() -> tuple[bool, str]:
                before = head()
                result = space.git(
                    path, "commit", "--quiet", "--allow-empty", *extra, "-m", message, check=False
                )
                created = head() != before
                ok = (
                    (result.returncode == 0 and created)
                    if allowed
                    else (result.returncode != 0 and not created)
                )
                return (
                    ok,
                    f"exit {result.returncode}, commit {'created' if created else 'not created'}",
                )

            return probe

        record(
            "pre-commit / commit-msg",
            "clean commit allowed",
            "exit 0, commit created",
            commit_probe(CLEAN, allowed=True),
        )
        record(
            "pre-commit / commit-msg",
            "AI co-authored commit blocked",
            "non-zero exit, no commit",
            commit_probe(AI, allowed=False),
        )
        record(
            "bypass",
            "git commit --no-verify skips local hooks",
            "exit 0, commit created (documented limitation)",
            commit_probe(AI, "--no-verify", allowed=True),
        )

        def push_probe(
            message: str, branch: str, *extra: str, allowed: bool
        ) -> Callable[[], tuple[bool, str]]:
            def probe() -> tuple[bool, str]:
                space.git(path, "checkout", "--quiet", "-B", branch, "origin/main")
                space.git(path, "commit", "--quiet", "--allow-empty", "--no-verify", "-m", message)
                result = space.git(path, "push", "--quiet", *extra, "origin", branch, check=False)
                ok = result.returncode == 0 if allowed else result.returncode != 0
                return ok, f"exit {result.returncode}"

            return probe

        record("pre-push", "clean push allowed", "exit 0", push_probe(CLEAN, "clean", allowed=True))
        record(
            "pre-push",
            "AI co-authored push blocked",
            "non-zero exit",
            push_probe(AI, "ai", allowed=False),
        )
        record(
            "bypass",
            "git push --no-verify skips pre-push",
            "exit 0 (documented limitation)",
            push_probe(AI, "ai-bypass", "--no-verify", allowed=True),
        )

        space.git(path, "checkout", "--quiet", "clean")
        record(
            "cli",
            "commitguard scan on a clean commit",
            "exit 0",
            lambda: ((r := _cli(space, path, "scan", "HEAD"))[0] == 0, f"exit {r[0]}"),
        )
        space.git(path, "checkout", "--quiet", "ai")
        record(
            "cli",
            "commitguard scan on an AI co-authored commit",
            "exit 1 (blocked)",
            lambda: ((r := _cli(space, path, "scan", "HEAD"))[0] == 1, f"exit {r[0]}"),
        )
        config = path / ".commitguard.yaml"
        config.write_bytes(b"version: 1\r\npolicies:\r\n  bot_identity:\r\n    action: block\r\n")
        record(
            "configuration",
            "configuration with CRLF line endings",
            "exit 1 (valid configuration, AI commit still blocked)",
            lambda: ((r := _cli(space, path, "scan", "HEAD"))[0] == 1, f"exit {r[0]}"),
        )
        config.write_text("version: 1\npolicies: [not, a, mapping]\n", encoding="utf-8")
        record(
            "configuration",
            "invalid configuration fails closed",
            "exit 2 (error), never exit 0",
            lambda: ((r := _cli(space, path, "scan", "HEAD"))[0] == 2, f"exit {r[0]}"),
        )
        config.unlink()
        record(
            "cli",
            "commitguard check --message-file",
            "exit 1 for an AI co-authored message",
            lambda: _message_file_check(space, path),
        )
        record(
            "hooks",
            "uninstall removes the hooks",
            "hooks absent; an AI commit is no longer blocked locally",
            lambda: _uninstall_probe(space, path, repository),
        )

    passed = sum(1 for c in checks if c.status == "PASS")
    failed = sum(1 for c in checks if c.status == "FAIL")
    return PlatformResult(
        system=(
            f"{platform_module.system()} {platform_module.release()} ({platform_module.machine()})"
        ),
        shell_for_hooks="Git's sh (Git for Windows ships one)" if os.name == "nt" else "/bin/sh",
        checks=tuple(checks),
        passed=passed,
        failed=failed,
        skipped=len(checks) - passed - failed,
    )


def _message_file_check(space: Workspace, path: Path) -> tuple[bool, str]:
    message = path.parent / "message with spaces.txt"
    message.write_text(AI, encoding="utf-8")
    code, _ = _cli(space, path, "check", "--message-file", str(message))
    return code == 1, f"exit {code}"


def _uninstall_probe(space: Workspace, path: Path, repository: Repository) -> tuple[bool, str]:
    uninstall_hooks(repository)
    states = {s.hook.value: s.state.value for s in _statuses(repository)}
    before = space.git(path, "rev-parse", "HEAD").stdout
    result = space.git(path, "commit", "--quiet", "--allow-empty", "-m", AI, check=False)
    created = space.git(path, "rev-parse", "HEAD").stdout != before
    ok = all(state == HookState.MISSING.value for state in states.values()) and created
    return (
        ok,
        f"hooks {states}; AI commit exit {result.returncode}, "
        f"{'created' if created else 'not created'}",
    )
