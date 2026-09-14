"""Bot detector: automation accounts (dependency bots, CI identities).

Bots are not AI agents, and many are legitimate (Dependabot, Renovate), so the
default policy only warns. Keeping this separate from AI detection lets a
repository allow bots while blocking AI attribution, or vice versa.

TODO(phase-5): implement against ``rules/bot-identities.yaml``.
"""

from collections.abc import Sequence
from typing import ClassVar

from commitguard.core.context import ScanContext
from commitguard.core.result import Finding
from commitguard.detectors.base import Detector

RULE_BOT_IDENTITY = "bot_identity"


class BotDetector(Detector):
    name: ClassVar[str] = "bot"
    rules: ClassVar[frozenset[str]] = frozenset({RULE_BOT_IDENTITY})
    description: ClassVar[str] = "Automation or bot account as author, committer or co-author."

    def detect(self, context: ScanContext) -> Sequence[Finding]:
        raise NotImplementedError("BotDetector is planned for Phase 5")
