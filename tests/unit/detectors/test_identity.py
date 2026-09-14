"""IdentityDetector tests.

The interface tests pass today. The behavioural tests are the Phase 2
specification: they are strict xfails, so implementing the detector makes them
pass, which fails the run until the xfail marker is removed.
"""

import pytest

from commitguard.core.context import ScanContext
from commitguard.detectors.base import Detector
from commitguard.detectors.identity import IdentityDetector

spec = pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="IdentityDetector is planned for Phase 2"
)


def test_implements_detector_interface() -> None:
    detector = IdentityDetector()
    assert isinstance(detector, Detector)
    assert detector.name == "identity"
    assert detector.rules
    assert detector.description


@pytest.mark.phase2
@spec
def test_reports_exactly_the_expected_rules(commit_case) -> None:  # type: ignore[no-untyped-def]
    detector = IdentityDetector()
    findings = detector.detect(ScanContext(commit=commit_case.commit))
    assert {f.rule for f in findings} == commit_case.expected_rules & detector.rules


@pytest.mark.phase2
@spec
def test_findings_carry_evidence_and_remediation(commit_case) -> None:  # type: ignore[no-untyped-def]
    detector = IdentityDetector()
    for finding in detector.detect(ScanContext(commit=commit_case.commit)):
        assert finding.detector == detector.name
        assert finding.evidence
        assert finding.remediation


@pytest.mark.phase2
@spec
def test_is_deterministic(commit_case) -> None:  # type: ignore[no-untyped-def]
    detector = IdentityDetector()
    context = ScanContext(commit=commit_case.commit)
    assert detector.detect(context) == detector.detect(context)
