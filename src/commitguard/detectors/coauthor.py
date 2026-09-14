"""Co-author detector: AI agents listed in ``Co-authored-by`` style trailers.

Example that produces an ``ai_coauthor`` finding::

    feat: implement authentication

    Co-authored-by: Claude <noreply@anthropic.com>

Every co-author trailer is matched *independently* against the AI identity
rules, so human co-authors in the same commit never produce findings.
Trailers are considered even when malformed or outside Git's trailer block,
because hiding attribution that way is still attribution.
"""

from collections.abc import Sequence
from typing import ClassVar

from commitguard.core.context import CommitContext
from commitguard.core.result import Evidence, EvidenceSource, Finding, Severity
from commitguard.detectors.base import Detector, require_complete_trailers
from commitguard.provenance.trailers import Trailer
from commitguard.rules.matcher import CompiledRules

RULE_AI_COAUTHOR = "ai_coauthor"


class CoauthorDetector(Detector):
    name: ClassVar[str] = "coauthor"
    rules: ClassVar[frozenset[str]] = frozenset({RULE_AI_COAUTHOR})
    description: ClassVar[str] = "AI agent identity listed as a co-author in commit trailers."

    def __init__(self, rules: CompiledRules) -> None:
        self._matcher = rules.ai_matcher
        self._keys = frozenset(rules.rules.patterns.coauthor_trailer_keys)

    def detect(self, context: CommitContext) -> Sequence[Finding]:
        commit = context.commit
        require_complete_trailers(commit)
        findings: list[Finding] = []
        for trailer in commit.trailers:
            if trailer.normalized_key not in self._keys:
                continue
            match = self._matcher.match(trailer.name, trailer.email)
            if match is None:
                continue
            findings.append(
                Finding(
                    detector=self.name,
                    rule_id=RULE_AI_COAUTHOR,
                    severity=Severity.HIGH,
                    confidence=match.confidence,
                    title="AI coauthor detected",
                    message=(
                        f"A co-author trailer names an identity associated with the AI agent "
                        f"{match.display_name}."
                    ),
                    evidence=(
                        Evidence(
                            source=EvidenceSource.COAUTHOR_TRAILER,
                            value=trailer.value or trailer.raw,
                            line_number=trailer.line_number,
                            matched=match.reasons,
                            notes=_notes(trailer),
                        ),
                    ),
                    commit_sha=commit.sha,
                    remediation=(
                        "Remove the AI co-author attribution from the commit message before "
                        "pushing (for example with `git commit --amend`). CommitGuard never "
                        "rewrites commits itself."
                    ),
                )
            )
        return findings


def _notes(trailer: Trailer) -> tuple[str, ...]:
    notes: list[str] = []
    if not trailer.in_trailer_block:
        notes.append("outside the commit's trailer block")
    notes.extend(f"trailer: {issue.value}" for issue in trailer.issues)
    notes.extend(f"identity: {issue.value}" for issue in trailer.identity.issues)
    return tuple(notes)
