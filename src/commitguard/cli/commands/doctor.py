"""``commitguard doctor``: diagnose installation, configuration and enforcement.

Every check reports honestly. Output is grouped into sections and ends with an
overall status:

* HEALTHY   - everything required for local enforcement is in place;
* DEGRADED  - CommitGuard works but enforcement is incomplete or needs attention;
* UNHEALTHY - a check failed; hooks will block operations (exit code 2).
"""

from dataclasses import dataclass
from enum import StrEnum

import typer

from commitguard import __version__
from commitguard.cli.output import ExitCode, info, supports_unicode
from commitguard.config.enforcement import build_enforcement
from commitguard.config.loader import LoadedConfig, load_effective_config
from commitguard.core.decision import Action
from commitguard.exceptions.base import CommitGuardError
from commitguard.git.commands import MINIMUM_GIT_VERSION, git_executable, git_version
from commitguard.git.hooks import (
    CHAINED_SUFFIX,
    HookState,
    HookType,
    default_python,
    hook_status,
    repository_hooks_dir,
)
from commitguard.git.repository import Repository
from commitguard.policies.loader import build_policy_set
from commitguard.provenance.author import Identity
from commitguard.security.sanitization import sanitize_for_terminal
from commitguard.services.analysis import Analyzer, pending_commit
from commitguard.utils.platform import MINIMUM_PYTHON, python_version, python_version_supported
from commitguard.utils.subprocess import run_command


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class Check:
    section: str
    status: Status
    detail: str
    remediation: str = ""


def _python_runs_commitguard(python: str) -> bool:
    try:
        result = run_command([python, "-P", "-m", "commitguard", "--version"], timeout=30)
    except (OSError, ValueError):
        return False
    return result.ok and result.stdout.startswith(b"commitguard ")


def _hook_checks(repository: Repository, loaded: LoadedConfig | None) -> list[Check]:
    checks: list[Check] = []
    try:
        hooks_dir = repository_hooks_dir(repository)
    except CommitGuardError as exc:
        return [
            Check("Hooks", Status.FAIL, str(exc), "set core.hooksPath deliberately or unset it")
        ]
    hooks_path_setting = repository.config_get("core.hooksPath")
    if hooks_path_setting is not None:
        checks.append(Check("Hooks", Status.WARN, f"core.hooksPath is set to {hooks_path_setting}"))
    if not hooks_dir.is_dir():
        checks.append(
            Check(
                "Hooks", Status.FAIL, f"hooks directory missing: {hooks_dir}", "commitguard install"
            )
        )
        return checks
    checks.append(Check("Hooks", Status.OK, f"hooks directory: {hooks_dir}"))

    enforcement = build_enforcement(*loaded.configs) if loaded else None
    interpreters: dict[str, bool] = {}
    for hook in HookType:
        status = hook_status(hooks_dir, hook, expected_python=default_python())
        name = hook.value
        disabled = enforcement is not None and not enforcement.enabled(name)
        chained = f" (chained: {status.chained.name})" if status.chained else ""
        if status.state is HookState.INSTALLED:
            checks.append(Check("Hooks", Status.OK, f"{name} installed{chained}"))
        elif status.state is HookState.OUTDATED:
            checks.append(
                Check(
                    "Hooks",
                    Status.WARN,
                    f"{name} installed for a different interpreter ({status.python})",
                    "commitguard install   (to use the current installation)",
                )
            )
        elif status.state is HookState.MISSING:
            checks.append(
                Check("Hooks", Status.FAIL, f"{name} hook missing", "commitguard install")
            )
        elif status.state is HookState.FOREIGN:
            checks.append(
                Check(
                    "Hooks",
                    Status.FAIL,
                    f"{name} hook exists but is not managed by CommitGuard",
                    f"commitguard install   (preserves it as {name}{CHAINED_SUFFIX})",
                )
            )
        elif status.state is HookState.MODIFIED:
            checks.append(
                Check(
                    "Hooks",
                    Status.WARN,
                    f"{name} hook appears to have been modified",
                    "commitguard install   (restores the managed block)",
                )
            )
        elif status.state is HookState.CORRUPT:
            checks.append(
                Check("Hooks", Status.FAIL, f"{name} hook markers are corrupt", "inspect the file")
            )
        elif status.state is HookState.NOT_EXECUTABLE:
            checks.append(
                Check(
                    "Hooks",
                    Status.FAIL,
                    f"{name} hook is not executable; Git will skip it",
                    "commitguard install",
                )
            )
        if status.python and status.state is not HookState.MISSING:
            if status.python not in interpreters:
                interpreters[status.python] = bool(status.python_available) and (
                    _python_runs_commitguard(status.python)
                )
            if not interpreters[status.python]:
                checks.append(
                    Check(
                        "Hooks",
                        Status.WARN,
                        f"{name} hook interpreter cannot run CommitGuard ({status.python}); "
                        "the hook will fall back to `commitguard` on PATH or block",
                        "commitguard install",
                    )
                )
        if disabled:
            checks.append(
                Check(
                    "Enforcement",
                    Status.WARN,
                    f"{name} enforcement disabled in configuration",
                    "set enforcement." + name.replace("-", "_") + ": true",
                )
            )
    return checks


def _run_checks() -> list[Check]:
    checks: list[Check] = []
    minimum_python = ".".join(map(str, MINIMUM_PYTHON))
    checks.append(
        Check(
            "CommitGuard",
            Status.OK if python_version_supported() else Status.FAIL,
            f"commitguard {__version__} on Python {python_version()} "
            f"(requires >= {minimum_python})",
        )
    )
    checks.append(Check("CommitGuard", Status.OK, f"interpreter: {default_python()}"))

    try:
        executable = git_executable()
        version = git_version()
    except CommitGuardError as exc:
        checks.append(Check("Git", Status.FAIL, str(exc), "install Git >= 2.31"))
        return checks
    minimum_git = ".".join(map(str, MINIMUM_GIT_VERSION))
    checks.append(
        Check(
            "Git",
            Status.OK if version[:2] >= MINIMUM_GIT_VERSION else Status.FAIL,
            f"Git {'.'.join(map(str, version))} at {executable} (requires >= {minimum_git})",
        )
    )

    try:
        repository = Repository.discover()
    except CommitGuardError:
        checks.append(
            Check("Repository", Status.WARN, "not inside a Git work tree", "cd into a repository")
        )
        return checks
    checks.append(Check("Repository", Status.OK, f"repository: {repository.root}"))
    checks.append(Check("Repository", Status.OK, f"Git directory: {repository.git_dir}"))

    loaded: LoadedConfig | None = None
    try:
        loaded = load_effective_config(repository.root)
    except CommitGuardError as exc:
        checks.append(
            Check("Configuration", Status.FAIL, str(exc), "fix the file; hooks block until then")
        )
    else:
        if not any(source.path for source in loaded.sources):
            checks.append(
                Check(
                    "Configuration",
                    Status.WARN,
                    "no configuration files; built-in defaults apply",
                    "commitguard init",
                )
            )
        else:
            layers = ", ".join(str(source) for source in loaded.sources)
            checks.append(Check("Configuration", Status.OK, f"configuration valid ({layers})"))

    try:
        policies = build_policy_set(*(loaded.configs if loaded else ()))
        analyzer = Analyzer.create(policies)
        probe = pending_commit(
            "probe\n\nCo-authored-by: Claude <noreply@anthropic.com>\n",
            Identity(name="Doctor", email="doctor@example.com"),
            Identity(name="Doctor", email="doctor@example.com"),
        )
        report = analyzer.analyze(probe)
    except CommitGuardError as exc:
        checks.append(Check("Detection engine", Status.FAIL, str(exc), "reinstall CommitGuard"))
    else:
        if report.failures or not any(f.finding.rule_id == "ai_coauthor" for f in report.findings):
            checks.append(
                Check(
                    "Detection engine", Status.FAIL, "self-test did not detect a known AI co-author"
                )
            )
        else:
            checks.append(Check("Detection engine", Status.OK, "available (self-test passed)"))
        enabled = [p for p in policies.values() if p.enabled]
        blocking = [p for p in enabled if p.action is Action.BLOCK]
        checks.append(
            Check(
                "Policies",
                Status.OK,
                f"{len(enabled)} of {len(policies)} policies enabled, {len(blocking)} blocking",
            )
        )
        if not blocking:
            checks.append(Check("Policies", Status.WARN, "no policy is set to block"))

    checks.extend(_hook_checks(repository, loaded))
    return checks


def doctor_command() -> None:
    """Check installation, configuration, detection engine and hook enforcement."""
    ok, cross, bang = ("✓", "✗", "⚠") if supports_unicode() else ("OK", "X", "!")
    symbol = {Status.OK: ok, Status.WARN: bang, Status.FAIL: cross}
    checks = _run_checks()

    info("CommitGuard Doctor")
    section = None
    for check in checks:
        if check.section != section:
            section = check.section
            info("")
            info(section)
        info(f"{symbol[check.status]} {sanitize_for_terminal(check.detail, max_length=1000)}")
        if check.remediation and check.status is not Status.OK:
            info(f"    Fix: {sanitize_for_terminal(check.remediation, max_length=300)}")

    hook_problem = any(
        c.section in ("Hooks", "Enforcement") and c.status is not Status.OK for c in checks
    )
    info("")
    if hook_problem:
        info("Security enforcement is incomplete.")
    if any(c.status is Status.FAIL for c in checks):
        info("Status: UNHEALTHY")
        raise typer.Exit(code=int(ExitCode.ERROR))
    info("Status: DEGRADED" if any(c.status is Status.WARN for c in checks) else "Status: HEALTHY")
