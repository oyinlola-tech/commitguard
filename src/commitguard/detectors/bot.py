"""Bot detector: explicitly configured automation identities.

Bots are not AI agents. Dependabot or a release bot produce ``bot_identity``
findings - never ``ai_*`` findings - so a repository can allow bots while
blocking AI attribution. Only identities listed in ``rules/bot-identities.yaml``
match; there is no generic ``[bot]`` heuristic.
"""

from collections.abc import Sequence
from typing import ClassVar

from commitguard.core.context import CommitContext
from commitguard.core.result import Evidence, EvidenceSource, Finding, Severity
from commitguard.detectors.base import Detector, group_by_rule, require_complete_trailers
from commitguard.rules.matcher import CompiledRules, IdentityMatch

RULE_BOT_IDENTITY = "bot_identity"


class BotDetector(Detector):
    name: ClassVar[str] = "bot"
    rules: ClassVar[frozenset[str]] = frozenset({RULE_BOT_IDENTITY})
    description: ClassVar[str] = (
        "Configured automation/bot account as author, committer or co-author."
    )

    def __init__(self, rules: CompiledRules) -> None:
        self._matcher = rules.bot_matcher
        self._coauthor_keys = frozenset(rules.rules.patterns.coauthor_trailer_keys)

    def detect(self, context: CommitContext) -> Sequence[Finding]:
        commit = context.commit
        require_complete_trailers(commit)
        candidates: list[tuple[EvidenceSource, str, str | None, str | None, int | None]] = [
            (
                EvidenceSource.AUTHOR,
                str(commit.author),
                commit.author.name,
                commit.author.email,
                None,
            ),
            (
                EvidenceSource.COMMITTER,
                str(commit.committer),
                commit.committer.name,
                commit.committer.email,
                None,
            ),
        ]
        candidates.extend(
            (EvidenceSource.COAUTHOR_TRAILER, t.value, t.name, t.email, t.line_number)
            for t in commit.trailers
            if t.normalized_key in self._coauthor_keys
        )

        matches: list[tuple[str, tuple[Evidence, IdentityMatch]]] = []
        for source, value, name, email, line_number in candidates:
            match = self._matcher.match(name, email)
            if match is not None:
                evidence = Evidence(
                    source=source, value=value, line_number=line_number, matched=match.reasons
                )
                matches.append((match.rule_id, (evidence, match)))

        findings = []
        for items in group_by_rule(matches).values():
            match = max((m for _, m in items), key=lambda m: m.confidence.rank)
            findings.append(
                Finding(
                    detector=self.name,
                    rule_id=RULE_BOT_IDENTITY,
                    severity=Severity.LOW,
                    confidence=match.confidence,
                    title="Bot identity detected",
                    message=(
                        f"The commit involves the configured automation identity "
                        f"{match.display_name}. This is a bot finding, not an AI attribution."
                    ),
                    evidence=tuple(evidence for evidence, _ in items),
                    commit_sha=commit.sha,
                    remediation=(
                        "If this automation is expected, set `bot_identity` to `allow` in "
                        ".commitguard.yaml; otherwise investigate where the commit came from."
                    ),
                )
            )
        return findings
