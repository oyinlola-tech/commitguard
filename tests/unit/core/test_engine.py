from collections.abc import Sequence
from typing import ClassVar

import pytest

from commitguard.core.context import CommitContext
from commitguard.core.engine import DetectionEngine
from commitguard.core.result import Confidence, Evidence, EvidenceSource, Finding, Severity
from commitguard.detectors.base import Detector
from commitguard.detectors.registry import DetectorRegistry, builtin_registry
from commitguard.git.commit import Commit
from commitguard.rules.matcher import CompiledRules


def _finding(detector: str, rule: str, sha: str | None = None) -> Finding:
    return Finding(
        detector=detector,
        rule_id=rule,
        severity=Severity.LOW,
        confidence=Confidence.HIGH,
        title="t",
        message="m",
        evidence=(Evidence(source=EvidenceSource.MESSAGE, value="v"),),
        commit_sha=sha,
        remediation="r",
    )


class StaticDetector(Detector):
    name: ClassVar[str] = "static"
    rules: ClassVar[frozenset[str]] = frozenset({"static_rule"})
    description: ClassVar[str] = "Always reports one finding."

    def detect(self, context: CommitContext) -> Sequence[Finding]:
        return [_finding(self.name, "static_rule")]


class CrashingDetector(Detector):
    name: ClassVar[str] = "crashing"
    rules: ClassVar[frozenset[str]] = frozenset({"crash_rule"})
    description: ClassVar[str] = "Raises on hostile input."

    def detect(self, context: CommitContext) -> Sequence[Finding]:
        raise ValueError("boom \x1b[2K")


class UndeclaredRuleDetector(Detector):
    name: ClassVar[str] = "undeclared"
    rules: ClassVar[frozenset[str]] = frozenset({"declared_rule"})
    description: ClassVar[str] = "Emits a rule it did not declare."

    def detect(self, context: CommitContext) -> Sequence[Finding]:
        return [_finding(self.name, "other_rule")]


class ImpersonatingDetector(Detector):
    name: ClassVar[str] = "impersonating"
    rules: ClassVar[frozenset[str]] = frozenset({"static_rule"})
    description: ClassVar[str] = "Attributes findings to another detector."

    def detect(self, context: CommitContext) -> Sequence[Finding]:
        return [_finding("static", "static_rule")]


class WrongCommitDetector(Detector):
    name: ClassVar[str] = "wrong_commit"
    rules: ClassVar[frozenset[str]] = frozenset({"wrong_rule"})
    description: ClassVar[str] = "Refers to another commit."

    def detect(self, context: CommitContext) -> Sequence[Finding]:
        return [_finding(self.name, "wrong_rule", sha="b" * 40)]


def _engine(*detectors: Detector) -> DetectionEngine:
    registry = DetectorRegistry()
    for detector in detectors:
        registry.register(detector)
    return DetectionEngine(registry)


def test_collects_findings(human_commit: Commit) -> None:
    result = _engine(StaticDetector()).run(CommitContext(commit=human_commit))
    assert [f.rule_id for f in result.findings] == ["static_rule"]
    assert result.complete
    assert result.detectors_run == ("static",)


def test_detector_exception_becomes_sanitised_failure(human_commit: Commit) -> None:
    result = _engine(CrashingDetector(), StaticDetector()).run(CommitContext(commit=human_commit))
    assert not result.complete
    (failure,) = result.failures
    assert failure.detector == "crashing"
    assert failure.error_type == "ValueError"
    assert "\x1b" not in failure.message
    assert [f.rule_id for f in result.findings] == ["static_rule"]


@pytest.mark.parametrize(
    "detector", [UndeclaredRuleDetector(), ImpersonatingDetector(), WrongCommitDetector()]
)
def test_invalid_detector_output_is_a_failure(human_commit: Commit, detector: Detector) -> None:
    result = _engine(detector).run(CommitContext(commit=human_commit))
    assert result.findings == ()
    assert [f.error_type for f in result.failures] == ["DetectionError"]


def test_detectors_run_in_deterministic_order(human_commit: Commit) -> None:
    result = _engine(StaticDetector(), CrashingDetector()).run(CommitContext(commit=human_commit))
    assert result.detectors_run == ("crashing", "static")


def test_detectors_without_enabled_rules_are_skipped(human_commit: Commit) -> None:
    engine = _engine(StaticDetector(), CrashingDetector())
    result = engine.run(CommitContext(commit=human_commit), enabled_rules={"static_rule"})
    assert result.detectors_run == ("static",)
    assert result.detectors_skipped == ("crashing",)
    assert result.complete


def test_builtin_detectors_run_cleanly_on_a_human_commit(
    human_commit: Commit, rules: CompiledRules
) -> None:
    result = DetectionEngine(builtin_registry(rules)).run(CommitContext(commit=human_commit))
    assert result.findings == ()
    assert result.complete
    assert result.detectors_run == ("bot", "coauthor", "identity", "trailer")
