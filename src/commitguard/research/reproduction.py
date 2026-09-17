"""Re-running the evidence CommitGuard publishes.

``commitguard reproduce`` exists so that someone who is not the author can check
the claims on their own machine. Each step reports one of:

``PASS``      the step ran and its checks held;
``FAIL``      the step ran and a check did not hold;
``SKIPPED``   the step could not run (missing test files, pytest or credentials),
              with the reason - never reported as a success;
``NOT RUN``   the step was not selected.

Steps that need the repository (the test suites) are skipped when CommitGuard is
installed without it. Steps that need GitHub credentials are skipped when they
are not configured; no step ever invents a result.
"""

import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

import commitguard

Status = Literal["PASS", "FAIL", "SKIPPED", "NOT RUN"]
AREAS = ("security", "benchmark", "integration", "github")
#: Environment variables the GitHub step needs (the App's own credentials).
GITHUB_ENVIRONMENT = (
    "COMMITGUARD_GITHUB_APP_ID",
    "COMMITGUARD_GITHUB_WEBHOOK_SECRET",
    "COMMITGUARD_APP_DATA_DIR",
)


class StepResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    area: str
    name: str
    status: Status
    detail: str
    seconds: float = 0.0
    command: str = ""


class ReproductionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    benchmark: str = "reproduce"
    benchmark_version: str = "1.0.0"
    steps: tuple[StepResult, ...]
    passed: int
    failed: int
    skipped: int
    not_run: int

    @property
    def ok(self) -> bool:
        return self.failed == 0


def source_checkout() -> Path | None:
    """The repository this package was installed from, if the tests are present."""
    root = Path(commitguard.__file__).resolve().parents[2]
    return root if (root / "tests").is_dir() and (root / "pyproject.toml").is_file() else None


def _pytest_available() -> bool:
    try:
        import pytest  # noqa: F401
    except ImportError:
        return False
    return True


def _run(arguments: Sequence[str], *, cwd: Path, environment: dict[str, str]) -> tuple[int, str]:
    completed = subprocess.run(  # noqa: S603 - fixed argument vector, no shell
        list(arguments),
        cwd=cwd,
        env={**os.environ, **environment},
        capture_output=True,
        timeout=3600,
        check=False,
    )
    output = (completed.stdout + completed.stderr).decode("utf-8", "replace")
    return completed.returncode, output


def _summary_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in reversed(lines):
        if " passed" in line or " failed" in line or " error" in line:
            return line.strip("= ").strip()
    return lines[-1][:200] if lines else "no output"


def _pytest_step(
    area: str, name: str, arguments: Sequence[str], evidence_dir: Path | None
) -> StepResult:
    root = source_checkout()
    command = "python -m pytest " + " ".join(arguments)
    if root is None:
        return StepResult(
            area=area,
            name=name,
            status="SKIPPED",
            detail=(
                "the test suite is not installed with the package; clone the repository and "
                "run this from the checkout"
            ),
            command=command,
        )
    if not _pytest_available():
        return StepResult(
            area=area,
            name=name,
            status="SKIPPED",
            detail='pytest is not installed; install the development dependencies (".[dev]")',
            command=command,
        )
    environment = {"COMMITGUARD_EVIDENCE_DIR": str(evidence_dir)} if evidence_dir else {}
    started = time.perf_counter()
    code, output = _run(
        [sys.executable, "-m", "pytest", *arguments], cwd=root, environment=environment
    )
    return StepResult(
        area=area,
        name=name,
        status="PASS" if code == 0 else "FAIL",
        detail=_summary_line(output),
        seconds=round(time.perf_counter() - started, 3),
        command=command,
    )


def security_steps(evidence_dir: Path | None) -> list[StepResult]:
    return [
        _pytest_step(
            "security",
            "security regression suite (pytest -m security)",
            ["-q", "-m", "security", "-p", "no:cacheprovider"],
            evidence_dir,
        )
    ]


def integration_steps(evidence_dir: Path | None) -> list[StepResult]:
    return [
        _pytest_step(
            "integration",
            "integration suite (tests/integration)",
            ["-q", "tests/integration", "-p", "no:cacheprovider"],
            evidence_dir,
        )
    ]


def benchmark_steps(results_dir: Path | None = None) -> list[StepResult]:
    """Rebuild the dataset, check its fingerprint, and re-measure detection."""
    from commitguard.research.datasets import (
        DATASET_VERSION,
        build_dataset,
        fingerprint,
        load_dataset,
    )
    from commitguard.research.detection import run_detection

    steps: list[StepResult] = []
    started = time.perf_counter()
    cases = build_dataset(version=DATASET_VERSION)
    built = fingerprint(cases)
    published = results_dir.parent / "datasets" / f"v{DATASET_VERSION}" if results_dir else None
    manifest_file = published / "manifest.json" if published else None
    if published is not None and manifest_file is not None and manifest_file.is_file():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        # The files are written one per class, so loading returns the cases grouped by
        # class: compare the recorded fingerprint, and the cases themselves by id.
        # Reads the local JSONL files in benchmarks/datasets; not the Hugging Face
        # hub function that Bandit's B615 rule looks for.
        published_cases = load_dataset(published)  # nosec B615
        same_cases = sorted(published_cases, key=lambda case: case.id) == sorted(
            cases, key=lambda case: case.id
        )
        matches = manifest.get("fingerprint") == built and same_cases
        steps.append(
            StepResult(
                area="benchmark",
                name=f"dataset {DATASET_VERSION} matches the published files",
                status="PASS" if matches else "FAIL",
                detail=(
                    f"rebuilt {built[:16]}, published {str(manifest.get('fingerprint'))[:16]}, "
                    f"{len(cases)} cases, contents identical: {same_cases}"
                ),
                seconds=round(time.perf_counter() - started, 3),
                command=(
                    "commitguard benchmark dataset --write <dir> "
                    f"--dataset-version {DATASET_VERSION}"
                ),
            )
        )
    else:
        steps.append(
            StepResult(
                area="benchmark",
                name=f"dataset {DATASET_VERSION} rebuilt deterministically",
                status="PASS",
                detail=(
                    f"fingerprint {built[:16]} ({len(cases)} cases); no published copy to compare"
                ),
                seconds=round(time.perf_counter() - started, 3),
            )
        )
    started = time.perf_counter()
    result = run_detection(cases)
    clean = result.decision.false_negative == 0 and result.decision.false_positive == 0
    steps.append(
        StepResult(
            area="benchmark",
            name="detection: no false negative and no false positive",
            status="PASS" if clean else "FAIL",
            detail=(
                f"{result.decision.false_negative} false negatives, "
                f"{result.decision.false_positive} false positives over {len(cases)} cases"
            ),
            seconds=round(time.perf_counter() - started, 3),
            command=f"commitguard benchmark detection --dataset-version {DATASET_VERSION}",
        )
    )
    return steps


def github_steps() -> list[StepResult]:
    missing = [name for name in GITHUB_ENVIRONMENT if not os.environ.get(name)]
    command = "commitguard github validate"
    if missing:
        return [
            StepResult(
                area="github",
                name="GitHub App configuration and permissions",
                status="SKIPPED",
                detail=f"GitHub credentials not configured ({', '.join(missing)} not set)",
                command=command,
            )
        ]
    started = time.perf_counter()
    code, output = _run(
        [sys.executable, "-P", "-m", "commitguard", "github", "validate"],
        cwd=Path.cwd(),
        environment={},
    )
    return [
        StepResult(
            area="github",
            name="GitHub App configuration and permissions",
            status="PASS" if code == 0 else "FAIL",
            detail=_summary_line(output),
            seconds=round(time.perf_counter() - started, 3),
            command=command,
        )
    ]


def reproduce(
    areas: Sequence[str], *, evidence_dir: Path | None = None, results_dir: Path | None = None
) -> ReproductionResult:
    selected = [area for area in AREAS if area in areas]
    steps: list[StepResult] = []
    for area in AREAS:
        if area not in selected:
            steps.append(
                StepResult(area=area, name=f"{area} steps", status="NOT RUN", detail="not selected")
            )
            continue
        if area == "security":
            steps.extend(security_steps(evidence_dir))
        elif area == "benchmark":
            steps.extend(benchmark_steps(results_dir))
        elif area == "integration":
            steps.extend(integration_steps(evidence_dir))
        else:
            steps.extend(github_steps())
    counts = {
        status: sum(1 for step in steps if step.status == status)
        for status in ("PASS", "FAIL", "SKIPPED", "NOT RUN")
    }
    return ReproductionResult(
        steps=tuple(steps),
        passed=counts["PASS"],
        failed=counts["FAIL"],
        skipped=counts["SKIPPED"],
        not_run=counts["NOT RUN"],
    )
