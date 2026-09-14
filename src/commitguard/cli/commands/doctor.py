"""``commitguard doctor``: diagnose the local installation.

Every check reports honestly; checks for features that do not exist yet are
shown as ``todo`` rather than ``ok``.
"""

from dataclasses import dataclass
from enum import StrEnum

import typer

from commitguard import __version__
from commitguard.cli.output import ExitCode, info
from commitguard.config.loader import load_repository_config
from commitguard.exceptions.base import CommitGuardError
from commitguard.git.commands import MINIMUM_GIT_VERSION, git_executable, git_version
from commitguard.git.repository import Repository
from commitguard.security.sanitization import sanitize_for_terminal
from commitguard.utils.platform import MINIMUM_PYTHON, python_version, python_version_supported


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    TODO = "todo"


@dataclass(frozen=True, slots=True)
class CheckResult:
    status: Status
    name: str
    detail: str


def _run_checks() -> list[CheckResult]:
    results: list[CheckResult] = []

    minimum_python = ".".join(map(str, MINIMUM_PYTHON))
    results.append(
        CheckResult(
            Status.OK if python_version_supported() else Status.FAIL,
            "python",
            f"{python_version()} (requires >= {minimum_python})",
        )
    )

    try:
        executable = git_executable()
        version = git_version()
    except CommitGuardError as exc:
        results.append(CheckResult(Status.FAIL, "git", str(exc)))
        return results
    minimum_git = ".".join(map(str, MINIMUM_GIT_VERSION))
    results.append(
        CheckResult(
            Status.OK if version[:2] >= MINIMUM_GIT_VERSION else Status.FAIL,
            "git",
            f"{'.'.join(map(str, version))} at {executable} (requires >= {minimum_git})",
        )
    )

    try:
        repository = Repository.discover()
    except CommitGuardError:
        results.append(CheckResult(Status.WARN, "repository", "not inside a Git work tree"))
        return results
    results.append(CheckResult(Status.OK, "repository", str(repository.root)))

    try:
        _, source = load_repository_config(repository.root)
    except CommitGuardError as exc:
        results.append(CheckResult(Status.FAIL, "configuration", str(exc)))
    else:
        if source is None:
            detail = "no .commitguard.yaml; built-in defaults apply (run `commitguard init`)"
            results.append(CheckResult(Status.WARN, "configuration", detail))
        else:
            results.append(CheckResult(Status.OK, "configuration", f"valid: {source}"))

    results.append(
        CheckResult(Status.TODO, "hooks", "hook installation checks arrive in Phase 3")
    )
    results.append(
        CheckResult(Status.TODO, "detectors", "built-in detectors are stubs until Phase 2")
    )
    return results


def doctor_command() -> None:
    """Check whether CommitGuard can run correctly in this environment."""
    info(f"commitguard {__version__}")
    results = _run_checks()
    for result in results:
        detail = sanitize_for_terminal(result.detail, max_length=1000)
        info(f"[{result.status.value:>4}] {result.name}: {detail}")

    if any(result.status is Status.FAIL for result in results):
        raise typer.Exit(code=int(ExitCode.BLOCKED))
