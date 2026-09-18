"""``commitguard doctor``: diagnose installation, configuration and enforcement.

Every check reports honestly. Output is grouped into sections and ends with an
overall status:

* HEALTHY   - everything required for local enforcement is in place;
* DEGRADED  - CommitGuard works but enforcement is incomplete or needs attention;
* UNHEALTHY - a check failed; hooks will block operations (exit code 2).
"""

import importlib.util
import json
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

import typer

from commitguard import __version__
from commitguard.cli.output import ExitCode, info, supports_unicode
from commitguard.config.enforcement import build_enforcement, build_remediation
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
from commitguard.github.workflow import WorkflowIssueLevel, inspect_repository_workflows
from commitguard.policies.loader import build_policy_set
from commitguard.provenance.author import Identity
from commitguard.rules.loader import builtin_rules_dir, load_builtin_rules
from commitguard.security.sanitization import sanitize_for_terminal
from commitguard.services.analysis import Analyzer, pending_commit
from commitguard.utils.platform import MINIMUM_PYTHON, python_version, python_version_supported
from commitguard.utils.subprocess import run_command

#: Environment variables that configure a GitHub App service on this machine.
_APP_ENVIRONMENT = (
    "COMMITGUARD_GITHUB_APP_ID",
    "COMMITGUARD_GITHUB_WEBHOOK_SECRET",
    "COMMITGUARD_APP_DATA_DIR",
)


class Status(StrEnum):
    OK = "ok"
    INFO = "info"  # neutral fact; never changes the overall status
    NOT_CONFIGURED = "not_configured"  # a capability that is simply not set up here
    WARN = "warn"
    FAIL = "fail"


#: What each status is called in the output. NOT CONFIGURED is deliberately not a
#: pass: an unconfigured integration enforces nothing.
LABELS = {
    Status.OK: "PASS",
    Status.INFO: "INFO",
    Status.NOT_CONFIGURED: "NOT CONFIGURED",
    Status.WARN: "WARNING",
    Status.FAIL: "FAIL",
}


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
    if loaded is not None and build_remediation(*loaded.configs).auto_remove:
        # This turns a blocked commit into a commit. It must never be invisible.
        checks.append(
            Check(
                "Enforcement",
                Status.WARN,
                "remediation.auto_remove is on: prohibited attribution is deleted from the "
                "commit message instead of blocking the commit (identity findings still block)",
                "set remediation.auto_remove: false to block instead",
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
    checks.extend(_dependency_checks())

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
                    Status.NOT_CONFIGURED,
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

    checks.extend(_rule_checks(repository))
    checks.extend(_hook_checks(repository, loaded))
    checks.extend(_github_checks(repository))
    checks.extend(_app_checks())
    return checks


def _dependency_checks() -> list[Check]:
    """Runtime dependencies: the ones missing at run time, not at install time."""
    checks = []
    for module, purpose in (("yaml", "configuration and rules"), ("pydantic", "data models")):
        if importlib.util.find_spec(module) is not None:
            checks.append(
                Check("Runtime dependencies", Status.OK, f"{module} available ({purpose})")
            )
        else:
            checks.append(
                Check(
                    "Runtime dependencies",
                    Status.FAIL,
                    f"{module} is not importable ({purpose})",
                    "reinstall CommitGuard",
                )
            )
    # Presence only: importing it here would make every CLI invocation pay for it, and
    # the architecture tests keep cryptography out of everything but App authentication.
    if importlib.util.find_spec("cryptography") is None:
        checks.append(
            Check(
                "Runtime dependencies",
                Status.NOT_CONFIGURED,
                "cryptography is not installed; only the GitHub App service needs it",
            )
        )
    else:
        checks.append(
            Check("Runtime dependencies", Status.OK, "cryptography available (GitHub App service)")
        )
    return checks


def _app_checks() -> list[Check]:
    """Whether a GitHub App service is configured *on this machine*.

    Configuration is not a connection: this only reports what is set here, never
    that an installation is healthy on GitHub.
    """
    configured = [name for name in _APP_ENVIRONMENT if os.environ.get(name)]
    if not configured:
        return [
            Check(
                "GitHub App",
                Status.NOT_CONFIGURED,
                "no GitHub App service is configured on this machine",
                "only needed if you host the App: see docs/deployment/github-app.md",
            )
        ]
    missing = [name for name in _APP_ENVIRONMENT if not os.environ.get(name)]
    if missing:
        return [
            Check(
                "GitHub App",
                Status.WARN,
                f"GitHub App settings are incomplete: {', '.join(missing)} not set",
                "commitguard github validate",
            )
        ]
    return [
        Check(
            "GitHub App",
            Status.INFO,
            "GitHub App settings are present; this does not verify the installation",
            "commitguard github validate   (checks authentication and permissions)",
        )
    ]


def _rule_checks(repository: Repository) -> list[Check]:
    try:
        rules_dir = builtin_rules_dir()
        rules = load_builtin_rules()
    except CommitGuardError as exc:
        return [Check("Rules", Status.FAIL, str(exc), "reinstall CommitGuard")]
    checks = [
        Check(
            "Rules",
            Status.OK,
            f"bundled rules available ({len(rules.rules.ai_identities.agents)} AI agents, "
            f"{len(rules.rules.bots.bots)} bots) from {rules_dir}",
        )
    ]
    repo_rules = repository.root / "rules" / "ai-identities.yaml"
    try:
        same = repo_rules.exists() and repo_rules.resolve().parent == rules_dir.resolve()
    except OSError:
        same = False
    if repo_rules.exists() and not same:
        checks.append(
            Check(
                "Rules",
                Status.INFO,
                "this repository's rules/ directory is not used; detection rules always come "
                "from the installed CommitGuard package",
            )
        )
    return checks


def _github_checks(repository: Repository) -> list[Check]:
    section = "GitHub enforcement"
    inspections = inspect_repository_workflows(repository.root)
    if not inspections:
        return [
            Check(
                section,
                Status.NOT_CONFIGURED,
                "no GitHub workflow runs CommitGuard (local enforcement only)",
                "commitguard init --github --action-repository OWNER/REPO --action-ref <sha>",
            )
        ]
    checks = []
    for inspection in inspections:
        path = inspection.path.relative_to(repository.root).as_posix()
        names = ", ".join(inspection.check_names)
        checks.append(Check(section, Status.OK, f"workflow exists: {path} (check: {names})"))
        for issue in inspection.issues:
            status = {
                WorkflowIssueLevel.OK: Status.INFO,
                WorkflowIssueLevel.WARN: Status.WARN,
                WorkflowIssueLevel.FAIL: Status.FAIL,
            }[issue.level]
            checks.append(
                Check(section, status, f"{path}: {issue.message}", "commitguard github setup")
            )
    checks.append(
        Check(
            section,
            Status.INFO,
            "branch protection cannot be verified locally; the check only blocks merges when "
            "it is required on protected branches",
            "commitguard github setup",
        )
    )
    return checks


def _enforcement_summary(checks: list[Check]) -> tuple[str, bool, bool]:
    hook_problem = any(
        c.section in ("Hooks", "Enforcement") and c.status in (Status.WARN, Status.FAIL)
        for c in checks
    )
    local_ready = any(c.section == "Hooks" for c in checks) and not hook_problem
    github_ready = any(
        c.section == "GitHub enforcement" and c.detail.startswith("workflow exists") for c in checks
    ) and not any(
        c.section == "GitHub enforcement" and c.status in (Status.WARN, Status.FAIL) for c in checks
    )
    enforcement = {
        (True, True): "LOCAL + GITHUB ENFORCEMENT READY (branch protection not verified)",
        (True, False): "LOCAL ENFORCEMENT ONLY",
        (False, True): "GITHUB WORKFLOW READY, LOCAL ENFORCEMENT INCOMPLETE "
        "(branch protection not verified)",
        (False, False): "NO COMPLETE ENFORCEMENT LAYER",
    }[(local_ready, github_ready)]
    return enforcement, hook_problem, local_ready


def doctor_command(
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the checks as a JSON document.")
    ] = False,
) -> None:
    """Check installation, configuration, detection engine and hook enforcement.

    Each check reports PASS, WARNING, FAIL, NOT CONFIGURED or INFO. NOT CONFIGURED
    is never a pass: a capability that is not set up enforces nothing.
    """
    checks = _run_checks()
    enforcement, hook_problem, _ = _enforcement_summary(checks)
    failed = any(c.status is Status.FAIL for c in checks)
    warned = any(c.status is Status.WARN for c in checks)
    status = "UNHEALTHY" if failed else ("DEGRADED" if warned else "HEALTHY")

    if as_json:
        info(
            json.dumps(
                {
                    "commitguard_version": __version__,
                    "status": status,
                    "enforcement": enforcement,
                    "counts": {
                        LABELS[value]: sum(1 for c in checks if c.status is value)
                        for value in Status
                    },
                    "checks": [
                        {
                            "section": c.section,
                            "status": LABELS[c.status],
                            "detail": sanitize_for_terminal(c.detail, max_length=1000),
                            "remediation": sanitize_for_terminal(c.remediation, max_length=300)
                            or None,
                        }
                        for c in checks
                    ],
                },
                indent=2,
            )
        )
        if failed:
            raise typer.Exit(code=int(ExitCode.ERROR))
        return

    ok, cross = ("\u2713", "\u2717") if supports_unicode() else ("OK", "X")
    bang = "!"
    symbol = {
        Status.OK: ok,
        Status.INFO: "i",
        Status.NOT_CONFIGURED: "-",
        Status.WARN: bang,
        Status.FAIL: cross,
    }
    info("CommitGuard Doctor")
    section = None
    for check in checks:
        if check.section != section:
            section = check.section
            info("")
            info(section)
        label = LABELS[check.status]
        info(
            f"{symbol[check.status]} {label:<14} "
            f"{sanitize_for_terminal(check.detail, max_length=1000)}"
        )
        if check.remediation and check.status in (Status.WARN, Status.FAIL, Status.NOT_CONFIGURED):
            info(f"    Fix: {sanitize_for_terminal(check.remediation, max_length=300)}")

    info("")
    if hook_problem:
        info("Security enforcement is incomplete.")
    info(f"Enforcement: {enforcement}")
    info(f"Status: {status}")
    if failed:
        raise typer.Exit(code=int(ExitCode.ERROR))
