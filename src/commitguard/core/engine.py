"""Detection engine: runs registered detectors over a commit context.

The engine is deliberately small and policy-agnostic:

* detectors run in a deterministic order (sorted by name);
* callers may pass the set of *enabled rule IDs*; a detector none of whose
  rules are enabled is skipped (recorded in ``detectors_skipped``). The engine
  never looks at actions - that remains the policy evaluator's job;
* a detector that raises is recorded as a :class:`DetectorFailure` rather than
  crashing the scan - the policy evaluator then fails closed;
* output is validated: detectors must return :class:`Finding` objects for
  rules they declared, attributed to themselves and to the scanned commit.
"""

from collections.abc import Collection

from commitguard.core.context import CommitContext
from commitguard.core.result import DetectionResult, DetectorFailure, Finding
from commitguard.detectors.base import Detector
from commitguard.detectors.registry import DetectorRegistry
from commitguard.exceptions.detection import DetectionError
from commitguard.security.sanitization import sanitize_for_terminal


class DetectionEngine:
    """Execute detectors from a registry against a :class:`CommitContext`."""

    def __init__(self, registry: DetectorRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> DetectorRegistry:
        return self._registry

    def run(
        self,
        context: CommitContext,
        *,
        enabled_rules: Collection[str] | None = None,
    ) -> DetectionResult:
        findings: list[Finding] = []
        failures: list[DetectorFailure] = []
        ran: list[str] = []
        skipped: list[str] = []

        for detector in self._registry.all():
            if enabled_rules is not None and not detector.rules & set(enabled_rules):
                skipped.append(detector.name)
                continue
            ran.append(detector.name)
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

        return DetectionResult(
            commit_sha=context.commit.sha,
            detectors_run=tuple(ran),
            detectors_skipped=tuple(skipped),
            findings=tuple(findings),
            failures=tuple(failures),
        )

    @staticmethod
    def _run_detector(detector: Detector, context: CommitContext) -> list[Finding]:
        produced = list(detector.detect(context))
        for finding in produced:
            if not isinstance(finding, Finding):
                raise DetectionError(f"detector returned {type(finding).__name__}, not Finding")
            if finding.detector != detector.name:
                raise DetectionError(f"finding attributed to {finding.detector!r}")
            if finding.rule_id not in detector.rules:
                raise DetectionError(f"finding uses undeclared rule {finding.rule_id!r}")
            if finding.commit_sha != context.commit.sha:
                raise DetectionError("finding refers to a different commit")
        return produced
