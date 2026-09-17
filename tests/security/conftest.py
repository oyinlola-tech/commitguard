"""Shared configuration for the fuzzing, ReDoS and property-based security tests.

Every test in this directory carries the ``security`` marker (tests/conftest.py).

Hypothesis runs derandomized by default, so a CI failure reproduces locally with
the same examples. Environment variables:

* ``COMMITGUARD_FUZZ_EXAMPLES`` - examples per property (default 300);
* ``COMMITGUARD_FUZZ_RANDOM=1`` - explore new random examples instead;
* ``COMMITGUARD_REDOS_CHARS`` - adversarial input length for the ReDoS tests
  (default 50,000 characters);
* ``COMMITGUARD_EVIDENCE_DIR`` - when set, the number of examples executed per
  property and the worst regular-expression timings are written there as JSON,
  for the security report.
"""

import json
import os
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings

FUZZ_EXAMPLES = int(os.environ.get("COMMITGUARD_FUZZ_EXAMPLES", "300"))

settings.register_profile(
    "commitguard-security",
    max_examples=FUZZ_EXAMPLES,
    derandomize=os.environ.get("COMMITGUARD_FUZZ_RANDOM") != "1",
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    print_blob=True,
)
settings.load_profile("commitguard-security")


class Evidence:
    """Counts fuzzing examples and writes evidence documents (session-scoped)."""

    def __init__(self) -> None:
        self.executed: Counter[str] = Counter()

    def count(self, name: str) -> None:
        self.executed[name] += 1

    @staticmethod
    def write(name: str, document: object) -> None:
        directory = os.environ.get("COMMITGUARD_EVIDENCE_DIR")
        if not directory:
            return
        target = Path(directory) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(document, indent=2, sort_keys=True) + "\n"
        target.write_text(text, encoding="utf-8")


@pytest.fixture(scope="session")
def evidence() -> Iterator[Evidence]:
    recorder = Evidence()
    yield recorder
    if recorder.executed:
        recorder.write(
            "fuzzing.json",
            {
                "examples_per_property_setting": FUZZ_EXAMPLES,
                "derandomized": os.environ.get("COMMITGUARD_FUZZ_RANDOM") != "1",
                "executed": dict(sorted(recorder.executed.items())),
                "total_executed": sum(recorder.executed.values()),
            },
        )
