"""Detector registry.

The registry is an explicit, in-process list. There is intentionally no
dynamic plugin discovery (entry points, import-by-name from configuration):
configuration must never cause arbitrary code to be imported or executed.
"""

from commitguard.detectors.base import Detector
from commitguard.exceptions.base import UnsafeInputError
from commitguard.exceptions.detection import DetectorRegistrationError
from commitguard.security.validation import validate_identifier


class DetectorRegistry:
    """An ordered collection of uniquely named detectors."""

    def __init__(self) -> None:
        self._detectors: dict[str, Detector] = {}

    def register(self, detector: Detector) -> None:
        if not isinstance(detector, Detector):
            raise DetectorRegistrationError(f"{detector!r} is not a Detector")
        try:
            validate_identifier(detector.name, kind="detector name")
            for rule in detector.rules:
                validate_identifier(rule, kind="rule id")
        except (AttributeError, UnsafeInputError) as exc:
            raise DetectorRegistrationError(str(exc)) from exc
        if not detector.rules:
            raise DetectorRegistrationError(f"detector {detector.name!r} declares no rules")
        if detector.name in self._detectors:
            raise DetectorRegistrationError(f"detector {detector.name!r} is already registered")
        self._detectors[detector.name] = detector

    def get(self, name: str) -> Detector:
        try:
            return self._detectors[name]
        except KeyError:
            raise DetectorRegistrationError(f"no detector named {name!r}") from None

    def all(self) -> tuple[Detector, ...]:
        """All detectors, sorted by name for deterministic execution."""
        return tuple(self._detectors[name] for name in sorted(self._detectors))

    def rules(self) -> frozenset[str]:
        """Every rule ID declared by a registered detector."""
        return frozenset(rule for detector in self._detectors.values() for rule in detector.rules)

    def __len__(self) -> int:
        return len(self._detectors)

    def __contains__(self, name: object) -> bool:
        return name in self._detectors


def builtin_registry() -> DetectorRegistry:
    """Return a registry containing CommitGuard's built-in detectors.

    Note: the built-in detectors are interface stubs until Phase 2 and raise
    ``NotImplementedError``; the engine records that as a detector failure.
    """
    from commitguard.detectors.bot import BotDetector
    from commitguard.detectors.coauthor import CoauthorDetector
    from commitguard.detectors.identity import IdentityDetector
    from commitguard.detectors.trailer import TrailerDetector

    registry = DetectorRegistry()
    for detector in (CoauthorDetector(), IdentityDetector(), TrailerDetector(), BotDetector()):
        registry.register(detector)
    return registry
