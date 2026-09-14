"""Identity detector: AI agents recorded as the commit author or committer.

Some agents commit under their own identity instead of adding a trailer, e.g.
``Copilot <175728472+Copilot@users.noreply.github.com>`` as author.

Only identities configured as AI agents match. Automation accounts that are
not AI agents (Dependabot, CI) are the bot detector's concern.
"""

from collections.abc import Sequence
from typing import ClassVar

from commitguard.core.context import CommitContext
from commitguard.core.result import Evidence, EvidenceSource, Finding, Severity
from commitguard.detectors.base import Detector, group_by_rule
from commitguard.rules.matcher import CompiledRules, IdentityMatch

RULE_AI_IDENTITY = "ai_identity"


class IdentityDetector(Detector):
    name: ClassVar[str] = "identity"
    rules: ClassVar[frozenset[str]] = frozenset({RULE_AI_IDENTITY})
    description: ClassVar[str] = "AI agent identity recorded as the commit author or committer."

    def __init__(self, rules: CompiledRules) -> None:
        self._matcher = rules.ai_matcher

    def detect(self, context: CommitContext) -> Sequence[Finding]:
        commit = context.commit
        matches: list[tuple[str, tuple[EvidenceSource, str, IdentityMatch]]] = []
        for source, identity in (
            (EvidenceSource.AUTHOR, commit.author),
            (EvidenceSource.COMMITTER, commit.committer),
        ):
            match = self._matcher.match(identity.name, identity.email)
            if match is not None:
                matches.append((match.rule_id, (source, str(identity), match)))

        findings = []
        for items in group_by_rule(matches).values():
            match = max((m for _, _, m in items), key=lambda m: m.confidence.rank)
            roles = " and ".join(source.label for source, _, _ in items)
            findings.append(
                Finding(
                    detector=self.name,
                    rule_id=RULE_AI_IDENTITY,
                    severity=Severity.HIGH,
                    confidence=match.confidence,
                    title="AI agent identity detected",
                    message=(
                        f"The {roles} identity is associated with the AI agent "
                        f"{match.display_name}."
                    ),
                    evidence=tuple(
                        Evidence(source=source, value=value, matched=m.reasons)
                        for source, value, m in items
                    ),
                    commit_sha=commit.sha,
                    remediation=(
                        "Recreate the commit under the responsible human contributor's identity "
                        "before pushing. CommitGuard never rewrites commits itself."
                    ),
                )
            )
        return findings
