"""Trailer detector: AI attribution in non-co-author trailers, and malformed trailers.

Examples of attribution outside ``Co-authored-by``::

    Generated-by: <agent>
    Assisted-by: <agent>
    AI-Assisted: true

Malformed trailer-like lines (``Co-authored-by Claude noreply@anthropic.com``)
are reported separately because they are a common evasion technique: some
tools display them as attribution while strict parsers ignore them.

TODO(phase-2): implement using ``rules/patterns.yaml``.
"""

from collections.abc import Sequence
from typing import ClassVar

from commitguard.core.context import ScanContext
from commitguard.core.result import Finding
from commitguard.detectors.base import Detector

RULE_AI_TRAILER = "ai_trailer"
RULE_MALFORMED_TRAILER = "malformed_trailer"


class TrailerDetector(Detector):
    name: ClassVar[str] = "trailer"
    rules: ClassVar[frozenset[str]] = frozenset({RULE_AI_TRAILER, RULE_MALFORMED_TRAILER})
    description: ClassVar[str] = "AI attribution trailers and malformed trailer-like lines."

    def detect(self, context: ScanContext) -> Sequence[Finding]:
        raise NotImplementedError("TrailerDetector is planned for Phase 2")
