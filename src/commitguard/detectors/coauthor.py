"""Co-author detector: AI agents listed in ``Co-authored-by`` trailers.

Target example::

    feat: implement authentication

    Co-authored-by: Claude <noreply@anthropic.com>

TODO(phase-2): implement using
    * :func:`commitguard.provenance.trailers.parse_trailers`;
    * :func:`commitguard.provenance.author.parse_identity`;
    * rule data from ``rules/ai-identities.yaml`` and ``rules/ai-domains.yaml``.
Matching must be case-insensitive, whitespace-tolerant, and resistant to
Unicode look-alikes; every match must carry the trailer line as evidence.
"""

from collections.abc import Sequence
from typing import ClassVar

from commitguard.core.context import ScanContext
from commitguard.core.result import Finding
from commitguard.detectors.base import Detector

RULE_AI_COAUTHOR = "ai_coauthor"


class CoauthorDetector(Detector):
    name: ClassVar[str] = "coauthor"
    rules: ClassVar[frozenset[str]] = frozenset({RULE_AI_COAUTHOR})
    description: ClassVar[str] = "AI agent listed as a co-author in commit trailers."

    def detect(self, context: ScanContext) -> Sequence[Finding]:
        raise NotImplementedError("CoauthorDetector is planned for Phase 2")
