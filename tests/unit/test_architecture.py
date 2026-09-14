"""Architecture guard rails: import boundaries and absence of import cycles."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "commitguard"

# Layers that must stay pure: no CLI, no network, no Git I/O, no subprocesses.
PURE_PACKAGES = ("core", "detectors", "policies", "provenance")
FORBIDDEN_IN_PURE = (
    "commitguard.cli",
    "commitguard.github",
    "commitguard.audit",
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
        for name in _imports(path):
            if any(_matches(name, forbidden) for forbidden in FORBIDDEN_IN_PURE):
                violations.append(f"{_module_name(path)} imports {name}")
    assert violations == []


def test_pure_layers_do_not_transitively_load_forbidden_commitguard_modules() -> None:
    forbidden = [f for f in FORBIDDEN_IN_PURE if f.startswith("commitguard.")]
    targets = [m for m in _all_modules() if m.split(".")[1:2] and m.split(".")[1] in PURE_PACKAGES]
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
