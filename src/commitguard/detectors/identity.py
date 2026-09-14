"""Identity detector: AI agents recorded as commit author or committer.

Some agents commit under their own identity rather than adding a trailer
(e.g. an agent's ``[bot]`` account authoring a commit on a branch).

TODO(phase-2): implement against ``rules/ai-identities.yaml`` and
``rules/ai-domains.yaml``, checking both ``author`` and ``committer``. The
evidence source must say which field matched.
"""

from collections.abc import Sequence
from typing import ClassVar

from commitguard.core.context import ScanContext
from commitguard.core.result import Finding
from commitguard.detectors.base import Detector

RULE_AI_IDENTITY = "ai_identity"


class IdentityDetector(Detector):
    name: ClassVar[str] = "identity"
    rules: ClassVar[frozenset[str]] = frozenset({RULE_AI_IDENTITY})
    description: ClassVar[str] = "AI agent recorded as the commit author or committer."

    def detect(self, context: ScanContext) -> Sequence[Finding]:
        raise NotImplementedError("IdentityDetector is planned for Phase 2")
