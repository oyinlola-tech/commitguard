from collections.abc import Sequence
from typing import ClassVar

import pytest

from commitguard.core.context import ScanContext
from commitguard.core.result import Finding
from commitguard.detectors.base import Detector
from commitguard.detectors.registry import DetectorRegistry, builtin_registry
from commitguard.exceptions.detection import DetectorRegistrationError
from commitguard.policies.defaults import KNOWN_POLICY_IDS


def _detector(name: str, rules: frozenset[str]) -> Detector:
    class _D(Detector):
        description: ClassVar[str] = "test"

        def detect(self, context: ScanContext) -> Sequence[Finding]:
            return []

    _D.name = name
    _D.rules = rules
    return _D()


def test_register_and_lookup() -> None:
    registry = DetectorRegistry()
    detector = _detector("sample", frozenset({"sample_rule"}))
    registry.register(detector)
    assert registry.get("sample") is detector
    assert "sample" in registry
    assert len(registry) == 1
    assert registry.rules() == {"sample_rule"}


def test_duplicate_names_are_rejected() -> None:
    registry = DetectorRegistry()
    registry.register(_detector("sample", frozenset({"a"})))
    with pytest.raises(DetectorRegistrationError, match="already registered"):
        registry.register(_detector("sample", frozenset({"b"})))


@pytest.mark.parametrize(
    ("name", "rules"),
    [
        ("Bad-Name", frozenset({"rule"})),
        ("good", frozenset({"Bad Rule"})),
        ("good", frozenset()),
    ],
)
def test_invalid_detectors_are_rejected(name: str, rules: frozenset[str]) -> None:
    with pytest.raises(DetectorRegistrationError):
        DetectorRegistry().register(_detector(name, rules))


def test_non_detectors_are_rejected() -> None:
    with pytest.raises(DetectorRegistrationError):
        DetectorRegistry().register(object())  # type: ignore[arg-type]


def test_unknown_detector_lookup() -> None:
    with pytest.raises(DetectorRegistrationError):
        DetectorRegistry().get("missing")


def test_builtin_registry_contents() -> None:
    registry = builtin_registry()
    assert [d.name for d in registry.all()] == ["bot", "coauthor", "identity", "trailer"]


def test_every_builtin_rule_has_a_default_policy_and_vice_versa() -> None:
    assert builtin_registry().rules() == KNOWN_POLICY_IDS
