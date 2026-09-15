"""Architecture guard rails: import boundaries and absence of import cycles."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "commitguard"

# Layers that must stay pure: no CLI, no network, no Git I/O, no subprocesses.
PURE_PACKAGES = ("core", "detectors", "policies", "provenance", "rules")
# Modules inside pure packages that are allowed to do I/O.
IO_EXEMPT_MODULES = frozenset({"commitguard.rules.loader"})
FORBIDDEN_IN_PURE = (
    "commitguard.cli",
    "commitguard.github",
    "commitguard.audit",
    "commitguard.services",
    "commitguard.ci",
    "commitguard.rules.loader",
    "commitguard.config.loader",
    "commitguard.git.commands",
    "commitguard.git.repository",
    "commitguard.git.hooks",
    "commitguard.git.diff",
    "commitguard.utils.subprocess",
    "commitguard.utils.filesystem",
    "subprocess",
    "socket",
    "urllib",
    "http",
)


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT.parent).with_suffix("")
    parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
    return ".".join(parts)


def _all_modules() -> list[str]:
    return sorted(_module_name(p) for p in PACKAGE_ROOT.rglob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _matches(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


@pytest.mark.parametrize("package", PURE_PACKAGES)
def test_pure_layers_do_not_import_io_or_interface_modules(package: str) -> None:
    violations: list[str] = []
    for path in (PACKAGE_ROOT / package).rglob("*.py"):
        if _module_name(path) in IO_EXEMPT_MODULES:
            continue
        for name in _imports(path):
            if any(_matches(name, forbidden) for forbidden in FORBIDDEN_IN_PURE):
                violations.append(f"{_module_name(path)} imports {name}")
    assert violations == []


def test_pure_layers_do_not_transitively_load_forbidden_commitguard_modules() -> None:
    forbidden = [f for f in FORBIDDEN_IN_PURE if f.startswith("commitguard.")]
    targets = [
        m
        for m in _all_modules()
        if m.split(".")[1:2] and m.split(".")[1] in PURE_PACKAGES and m not in IO_EXEMPT_MODULES
    ]
    script = (
        "import importlib, sys\n"
        f"forbidden = {forbidden!r}\n"
        f"targets = {targets!r}\n"
        "bad = []\n"
        "for target in targets:\n"
        "    for name in [m for m in sys.modules if m.startswith('commitguard')]:\n"
        "        del sys.modules[name]\n"
        "    importlib.import_module(target)\n"
        "    for name in sys.modules:\n"
        "        if any(name == f or name.startswith(f + '.') for f in forbidden):\n"
        "            bad.append(f'{target} -> {name}')\n"
        "print('\\n'.join(bad))\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_only_cli_imports_cli() -> None:
    violations: list[str] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        module = _module_name(path)
        if module.startswith(("commitguard.cli", "commitguard.__main__")):
            continue
        violations.extend(
            f"{module} imports {name}"
            for name in _imports(path)
            if _matches(name, "commitguard.cli")
        )
    assert violations == []


def test_every_module_imports_first_in_a_clean_interpreter() -> None:
    """Importing each module as the *first* commitguard import exposes cycles."""
    modules = [m for m in _all_modules() if m != "commitguard.__main__"]
    script = (
        "import importlib, sys, traceback\n"
        f"modules = {modules!r}\n"
        "failed = []\n"
        "for module in modules:\n"
        "    for name in [m for m in sys.modules if m.startswith('commitguard')]:\n"
        "        del sys.modules[name]\n"
        "    try:\n"
        "        importlib.import_module(module)\n"
        "    except Exception:\n"
        "        failed.append(module + ': ' + traceback.format_exc().splitlines()[-1])\n"
        "print('\\n'.join(failed))\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
    assert len(modules) > 50  # sanity: the walk actually found the package


def test_no_shell_true_anywhere() -> None:
    offenders = [
        _module_name(path)
        for path in PACKAGE_ROOT.rglob("*.py")
        if "shell=True" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_detection_code_uses_no_network_or_llm_libraries() -> None:
    banned = (
        "requests",
        "httpx",
        "urllib3",
        "aiohttp",
        "openai",
        "anthropic",
        "google.generativeai",
    )
    violations = [
        f"{_module_name(path)} imports {name}"
        for path in PACKAGE_ROOT.rglob("*.py")
        for name in _imports(path)
        if any(_matches(name, b) for b in banned)
    ]
    assert violations == []


def test_yaml_is_only_loaded_through_the_strict_safe_loader() -> None:
    offenders = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        if _module_name(path) == "commitguard.security.safe_yaml":
            continue
        text = path.read_text(encoding="utf-8")
        if "yaml.load(" in text or "yaml.unsafe_load" in text or "yaml.full_load" in text:
            offenders.append(_module_name(path))
    assert offenders == []


@pytest.mark.parametrize("package", ["github", "ci"])
def test_ci_layers_do_not_reimplement_detection(package: str) -> None:
    """GitHub/CI code feeds commits to the shared Analyzer; it never uses detectors directly."""
    offenders = []
    paths = list((PACKAGE_ROOT / package).rglob("*.py"))
    if package == "ci":
        paths.append(PACKAGE_ROOT / "services" / "ci.py")
    for path in paths:
        for name in _imports(path):
            if _matches(name, "commitguard.detectors") or _matches(name, "commitguard.core.engine"):
                offenders.append(f"{_module_name(path)} imports {name}")
            if _matches(name, "commitguard.policies.evaluator"):
                offenders.append(f"{_module_name(path)} imports {name}")
    assert offenders == []


# --------------------------------------------------------------------------- #
# Phase 5: GitHub App boundaries
# --------------------------------------------------------------------------- #
NETWORK_IMPORTS = (
    "urllib.request",
    "urllib.error",
    "http.client",
    "http.server",
    "ssl",
    "socket",
    "socketserver",
    "wsgiref.simple_server",
    "smtplib",
)
NETWORK_ALLOWED = {
    # The only outbound HTTP client (fixed https API base, no redirects).
    "commitguard.github.client": {"urllib.request", "urllib.error", "http.client", "ssl"},
    # The only inbound HTTP server (development / single host).
    "commitguard.github.server": {"socketserver", "wsgiref.simple_server"},
    # Phase 7 notification channels: SMTP relay, and signed webhooks to registered
    # HTTPS endpoints (address pinned after the public-address check, no redirects).
    "commitguard.notifications.channels.email": {"smtplib", "ssl"},
    "commitguard.notifications.channels.webhook": {"http.client", "ssl", "socket"},
}


def test_network_access_is_confined_to_the_github_client_and_server() -> None:
    violations = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        module = _module_name(path)
        allowed = NETWORK_ALLOWED.get(module, set())
        for name in _imports(path):
            if any(_matches(name, n) for n in NETWORK_IMPORTS) and name not in allowed:
                violations.append(f"{module} imports {name}")
    assert violations == []


def test_cryptography_is_only_used_for_app_authentication() -> None:
    users = {
        _module_name(path)
        for path in PACKAGE_ROOT.rglob("*.py")
        if any(_matches(name, "cryptography") for name in _imports(path))
    }
    # auth signs the App JWT; the CLI only probes that the optional extra is installed.
    assert users <= {"commitguard.github.auth", "commitguard.cli.commands.github"}


@pytest.mark.parametrize(
    "module", ["services/scan.py", "services/enforcement.py", "services/audit.py", "services/ci.py"]
)
def test_shared_services_are_platform_neutral(module: str) -> None:
    path = PACKAGE_ROOT / module
    offenders = [
        name
        for name in _imports(path)
        if _matches(name, "commitguard.github")
        or _matches(name, "commitguard.cli")
        or _matches(name, "commitguard.detectors")
    ]
    assert offenders == []


def test_git_fetch_happens_only_in_the_mirror_manager() -> None:
    offenders = [
        _module_name(path)
        for path in PACKAGE_ROOT.rglob("*.py")
        if '"fetch"' in path.read_text(encoding="utf-8")
        and _module_name(path) != "commitguard.github.repositories"
    ]
    assert offenders == []


def test_cli_import_does_not_load_the_app_service_or_cryptography() -> None:
    script = (
        "import sys, commitguard.cli.app\n"
        "names = ('cryptography', 'commitguard.github.app', 'commitguard.github.client', "
        "'commitguard.github.auth', 'sqlite3')\n"
        "print(sorted(n for n in names if n in sys.modules))"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


# --------------------------------------------------------------------------- #
# Phase 6: control plane and dashboard API boundaries
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("package", ["controlplane", "api"])
def test_control_plane_does_not_detect_or_evaluate_policy(package: str) -> None:
    """The dashboard stores and explains ScanResults; it has no detection or policy engine."""
    offenders = []
    for path in (PACKAGE_ROOT / package).rglob("*.py"):
        for name in _imports(path):
            if (
                _matches(name, "commitguard.detectors")
                or _matches(name, "commitguard.core.engine")
                or _matches(name, "commitguard.policies.evaluator")
                or _matches(name, "commitguard.services.analysis")
            ):
                offenders.append(f"{_module_name(path)} imports {name}")
    assert offenders == []


def test_api_routes_contain_no_sql() -> None:
    """Routes call control plane services; SQL lives in the control plane and storage."""
    offenders = [
        _module_name(path)
        for path in (PACKAGE_ROOT / "api").rglob("*.py")
        if any(
            keyword in path.read_text(encoding="utf-8")
            for keyword in ("SELECT ", "INSERT ", "UPDATE ", "DELETE FROM", "sqlite3")
        )
    ]
    assert offenders == []


def test_dashboard_api_is_not_loaded_by_the_cli() -> None:
    script = (
        "import sys, commitguard.cli.app\n"
        "names = ('commitguard.api', 'commitguard.controlplane')\n"
        "print(sorted(n for n in names if n in sys.modules))"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
