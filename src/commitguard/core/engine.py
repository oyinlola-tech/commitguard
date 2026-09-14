"""Detection engine: runs registered detectors over a scan context.

The engine is deliberately small and policy-agnostic:

* detectors run in a deterministic order (sorted by name);
* a detector that raises is recorded as a :class:`DetectorFailure` rather than
  crashing the scan - the policy evaluator then fails closed;
* output is validated: detectors must return :class:`Finding` objects for
  rules they declared, attributed to themselves.
"""

from commitguard.core.context import ScanContext
from commitguard.core.result import DetectorFailure, Finding, ScanResult
from commitguard.detectors.base import Detector
from commitguard.detectors.registry import DetectorRegistry
from commitguard.exceptions.detection import DetectionError
from commitguard.security.sanitization import sanitize_for_terminal


class DetectionEngine:
    """Execute every detector in a registry against a :class:`ScanContext`."""

    def __init__(self, registry: DetectorRegistry) -> None:
        self._registry = registry

    def run(self, context: ScanContext) -> ScanResult:
        findings: list[Finding] = []
        failures: list[DetectorFailure] = []
        detectors = self._registry.all()

        for detector in detectors:
            try:
                findings.extend(self._run_detector(detector, context))
            except Exception as exc:  # noqa: BLE001 - any detector failure must fail closed
                failures.append(
                    DetectorFailure(
                        detector=detector.name,
                        error_type=type(exc).__name__,
                        message=sanitize_for_terminal(str(exc) or type(exc).__name__),
                    )
                )

        return ScanResult(
            commit_sha=context.commit.sha,
            detectors_run=tuple(detector.name for detector in detectors),
            findings=tuple(findings),
            failures=tuple(failures),
        )

    @staticmethod
    def _run_detector(detector: Detector, context: ScanContext) -> list[Finding]:
        produced = list(detector.detect(context))
        for finding in produced:
            if not isinstance(finding, Finding):
                raise DetectionError(f"detector returned {type(finding).__name__}, not Finding")
            if finding.detector != detector.name:
                raise DetectionError(f"finding attributed to {finding.detector!r}")
            if finding.rule not in detector.rules:
                raise DetectionError(f"finding uses undeclared rule {finding.rule!r}")
        return produced
