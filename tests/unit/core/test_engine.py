from collections.abc import Sequence
from typing import ClassVar

import pytest

from commitguard.core.context import ScanContext
from commitguard.core.engine import DetectionEngine
from commitguard.core.result import Evidence, Finding, Severity
from commitguard.detectors.base import Detector
from commitguard.detectors.registry import DetectorRegistry, builtin_registry
from commitguard.git.commit import Commit


def _finding(detector: str, rule: str) -> Finding:
    return Finding(
        detector=detector,
        rule=rule,
        severity=Severity.LOW,
        message="m",
        evidence=(Evidence(source="test", value="v"),),
        remediation="r",
    )


class StaticDetector(Detector):
    name: ClassVar[str] = "static"
    rules: ClassVar[frozenset[str]] = frozenset({"static_rule"})
    description: ClassVar[str] = "Always reports one finding."

    def detect(self, context: ScanContext) -> Sequence[Finding]:
        return [_finding(self.name, "static_rule")]


class CrashingDetector(Detector):
    name: ClassVar[str] = "crashing"
    rules: ClassVar[frozenset[str]] = frozenset({"crash_rule"})
    description: ClassVar[str] = "Raises on hostile input."

    def detect(self, context: ScanContext) -> Sequence[Finding]:
        raise ValueError("boom \x1b[2K")


class UndeclaredRuleDetector(Detector):
    name: ClassVar[str] = "undeclared"
    rules: ClassVar[frozenset[str]] = frozenset({"declared_rule"})
    description: ClassVar[str] = "Emits a rule it did not declare."

    def detect(self, context: ScanContext) -> Sequence[Finding]:
        return [_finding(self.name, "other_rule")]


class ImpersonatingDetector(Detector):
    name: ClassVar[str] = "impersonating"
    rules: ClassVar[frozenset[str]] = frozenset({"static_rule"})
    description: ClassVar[str] = "Attributes findings to another detector."

    def detect(self, context: ScanContext) -> Sequence[Finding]:
        return [_finding("static", "static_rule")]


def _engine(*detectors: Detector) -> DetectionEngine:
    registry = DetectorRegistry()
    for detector in detectors:
        registry.register(detector)
    return DetectionEngine(registry)


def test_collects_findings(human_commit: Commit) -> None:
    result = _engine(StaticDetector()).run(ScanContext(commit=human_commit))
    assert [f.rule for f in result.findings] == ["static_rule"]
    assert result.complete
    assert result.detectors_run == ("static",)


def test_detector_exception_becomes_sanitised_failure(human_commit: Commit) -> None:
    result = _engine(CrashingDetector(), StaticDetector()).run(ScanContext(commit=human_commit))
    assert not result.complete
    (failure,) = result.failures
    assert failure.detector == "crashing"
    assert failure.error_type == "ValueError"
    assert "\x1b" not in failure.message
    # Remaining detectors still run.
    assert [f.rule for f in result.findings] == ["static_rule"]


@pytest.mark.parametrize("detector", [UndeclaredRuleDetector(), ImpersonatingDetector()])
def test_invalid_detector_output_is_a_failure(human_commit: Commit, detector: Detector) -> None:
    result = _engine(detector).run(ScanContext(commit=human_commit))
    assert result.findings == ()
    assert [f.error_type for f in result.failures] == ["DetectionError"]


def test_detectors_run_in_deterministic_order(human_commit: Commit) -> None:
    result = _engine(StaticDetector(), CrashingDetector()).run(ScanContext(commit=human_commit))
    assert result.detectors_run == ("crashing", "static")


def test_builtin_stub_detectors_fail_closed_not_silently(human_commit: Commit) -> None:
    """Until Phase 2, built-in detectors must surface as failures, never as a clean scan."""
    result = DetectionEngine(builtin_registry()).run(ScanContext(commit=human_commit))
    assert result.findings == ()
    assert {f.error_type for f in result.failures} == {"NotImplementedError"}
    assert len(result.failures) == len(result.detectors_run) == 4
