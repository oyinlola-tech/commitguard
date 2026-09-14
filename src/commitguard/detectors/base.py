"""Detector interface.

Contract every detector must honour:

1. **Pure.** Output depends only on the :class:`ScanContext` (and static rule
   data loaded at construction). No network, no subprocesses, no writes.
2. **Deterministic.** The same context always yields the same findings in the
   same order.
3. **Declared rules.** Every finding's ``rule`` is listed in :attr:`rules`;
   the engine rejects anything else.
4. **Evidence.** Every finding carries the raw evidence that triggered it.
5. **No decisions.** Detectors report; policies decide.
6. **Hostile input.** Commit data may be crafted to crash or evade a detector.
   Raising is acceptable (the scan fails closed); silently skipping is not.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar

from commitguard.core.context import ScanContext
from commitguard.core.result import Finding


class Detector(ABC):
    """Base class for all detectors."""

    #: Unique, snake_case detector name (used in findings and configuration).
    name: ClassVar[str]
    #: Rule IDs this detector may emit. Each must have a policy.
    rules: ClassVar[frozenset[str]]
    #: One-line human description.
    description: ClassVar[str]

    @abstractmethod
    def detect(self, context: ScanContext) -> Sequence[Finding]:
        """Analyse ``context`` and return zero or more findings."""
