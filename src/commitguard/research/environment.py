"""The benchmark manifest: what was measured, with what, on what.

A result without its environment cannot be compared or reproduced, so every
benchmark result embeds a :class:`BenchmarkManifest`. Values that cannot be
determined on a platform are recorded as ``None`` - never guessed.
"""

import os
import platform
import re
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from commitguard import __version__
from commitguard.policies.defaults import DEFAULT_POLICIES
from commitguard.rules.loader import builtin_rules_fingerprint
from commitguard.security.hashing import sha256_hex
from commitguard.utils.subprocess import run_command

MANIFEST_SCHEMA = 1


class BenchmarkManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = MANIFEST_SCHEMA
    commitguard_version: str
    git_version: str | None
    python_version: str
    python_implementation: str
    operating_system: str
    os_release: str
    os_version: str
    machine: str
    cpu_model: str | None
    cpu_count: int | None
    memory_bytes: int | None
    rules_version: str
    policy_version: str
    configuration_version: str
    dataset_version: str | None
    dataset_fingerprint: str | None
    timestamp: datetime
    command: str


def git_version() -> str | None:
    try:
        result = run_command(["git", "--version"], timeout=10)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", result.stdout.decode("utf-8", "replace"))
    return match.group(1) if match else None


def cpu_model() -> str | None:
    """The CPU model name, from the operating system, or None if unavailable."""
    system = platform.system()
    try:
        if system == "Linux":
            text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        elif system == "Darwin":
            result = run_command(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=10)
            if result.returncode == 0:
                return result.stdout.decode("utf-8", "replace").strip() or None
        elif system == "Windows":
            name = os.environ.get("PROCESSOR_IDENTIFIER")
            return name.strip() if name else None
    except OSError:
        return None
    return platform.processor() or None


def memory_bytes() -> int | None:
    """Physical memory, or None if the platform does not expose it without extra packages."""
    system = platform.system()
    try:
        if system == "Linux":
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
        elif system == "Darwin":
            result = run_command(["sysctl", "-n", "hw.memsize"], timeout=10)
            if result.returncode == 0:
                return int(result.stdout.strip())
        elif system == "Windows":
            return _windows_memory()
    except (OSError, ValueError):
        return None
    return None


def _windows_memory() -> int | None:  # pragma: no cover - exercised on Windows CI
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [  # noqa: RUF012 - ctypes structure definition
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return None
    return int(status.ullTotalPhys)


def default_policy_version() -> str:
    """Fingerprint of the built-in policy set (the policy the benchmarks evaluate)."""
    canonical = ";".join(
        f"{policy.id}={policy.action.value}:{int(policy.enabled)}"
        for policy in sorted(DEFAULT_POLICIES.values(), key=lambda p: p.id)
    )
    return sha256_hex(canonical.encode("utf-8"))


def collect_manifest(
    *,
    dataset_version: str | None = None,
    dataset_fingerprint: str | None = None,
    configuration_version: str = "built-in defaults",
    argv: list[str] | None = None,
) -> BenchmarkManifest:
    uname = platform.uname()
    return BenchmarkManifest(
        commitguard_version=__version__,
        git_version=git_version(),
        python_version=platform.python_version(),
        python_implementation=platform.python_implementation(),
        operating_system=uname.system,
        os_release=uname.release,
        os_version=uname.version,
        machine=uname.machine,
        cpu_model=cpu_model(),
        cpu_count=os.cpu_count(),
        memory_bytes=memory_bytes(),
        rules_version=builtin_rules_fingerprint(),
        policy_version=default_policy_version(),
        configuration_version=configuration_version,
        dataset_version=dataset_version,
        dataset_fingerprint=dataset_fingerprint,
        timestamp=datetime.now(UTC),
        command=shlex.join(["commitguard", *(sys.argv[1:] if argv is None else argv)]),
    )
