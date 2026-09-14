"""Detector interface.

Contract every detector must honour:

1. **Pure.** Output depends only on the :class:`CommitContext` and the rule
   data given at construction. No network, subprocesses, files or Git.
2. **Deterministic.** The same context always yields the same findings in the
   same order.
3. **Declared rules.** Every finding's ``rule_id`` is listed in :attr:`rules`;
   the engine rejects anything else.
4. **Evidence.** Every finding carries the concise metadata that triggered it.
5. **No decisions.** Detectors report; policies decide allow/warn/block.
6. **Hostile input.** Commit data may be crafted to crash or evade a detector.
   Raising is acceptable (the scan fails closed); silently skipping is not.
7. **Honest wording.** Findings describe attribution/identity *evidence*;
   they never claim that code was written by an AI.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar

from commitguard.core.context import CommitContext
from commitguard.core.result import Finding
from commitguard.exceptions.detection import DetectionError
from commitguard.git.commit import Commit


class Detector(ABC):
    """Base class for all detectors."""

    #: Unique, snake_case detector name (used in findings and output).
    name: ClassVar[str]
    #: Rule IDs this detector may emit. Each must have a policy.
    rules: ClassVar[frozenset[str]]
    #: One-line human description.
    description: ClassVar[str]

    @abstractmethod
    def detect(self, context: CommitContext) -> Sequence[Finding]:
        """Analyse ``context`` and return zero or more findings."""


def require_complete_trailers(commit: Commit) -> None:
    """Fail closed if the commit has more trailers than can be analysed.

    Otherwise an attacker could hide attribution behind a flood of trailers.
    """
    if commit.trailers_truncated:
        raise DetectionError("commit has too many trailers to analyse completely")


def group_by_rule[T](items: list[tuple[str, T]]) -> dict[str, list[T]]:
    """Group ``(rule_id, item)`` pairs preserving first-seen order (deterministic)."""
    grouped: dict[str, list[T]] = {}
    for key, item in items:
        grouped.setdefault(key, []).append(item)
    return grouped
