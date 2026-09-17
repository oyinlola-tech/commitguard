"""Trailer detector: configured attribution trailers, malformed trailers and
exact tool attribution footers.

Nothing here is flagged merely for existing. ``Reviewed-by`` or
``Signed-off-by`` only produce ``ai_trailer`` when a configured rule says so
(e.g. the value is an AI identity); ``malformed_trailer`` only applies to keys
listed in ``malformed_trailer_checks``. ``Co-authored-by`` belongs to the
co-author detector and is not reported here as AI attribution.

Message markers are exact whole-line matches of tool-inserted footers such as
``🤖 Generated with [Claude Code](https://claude.com/claude-code)``. There is
no free-text inference: "use AI service for recommendations" is not evidence.
"""

import unicodedata
from collections.abc import Sequence
from typing import ClassVar

from commitguard.core.context import CommitContext
from commitguard.core.result import (
    Confidence,
    Evidence,
    EvidenceSource,
    Finding,
    MatchKind,
    MatchReason,
    Severity,
)
from commitguard.detectors.base import Detector, require_complete_trailers
from commitguard.provenance.normalization import normalize_text
from commitguard.provenance.trailers import Trailer, TrailerIssue
from commitguard.rules.matcher import CompiledRules
from commitguard.rules.models import PATTERNS_FILE, MalformedTrailerCheck, TrailerRule

RULE_AI_TRAILER = "ai_trailer"
RULE_MALFORMED_TRAILER = "malformed_trailer"

MAX_MARKER_LINE_CHARS = 300


def _marker_key(line: str) -> str:
    """Normalised line with leading emoji/symbols removed (``🤖 Generated ...``)."""
    text = normalize_text(line)
    index = 0
    while index < len(text) and (
        text[index].isspace() or unicodedata.category(text[index]) in ("So", "Sk", "Sm", "Mn")
    ):
        index += 1
    return text[index:]


class TrailerDetector(Detector):
    name: ClassVar[str] = "trailer"
    rules: ClassVar[frozenset[str]] = frozenset({RULE_AI_TRAILER, RULE_MALFORMED_TRAILER})
    description: ClassVar[str] = (
        "Configured AI attribution trailers/footers and malformed trailers."
    )

    def __init__(self, rules: CompiledRules) -> None:
        patterns = rules.rules.patterns
        self._rules = rules
        self._attribution: dict[str, TrailerRule] = {
            key: rule for rule in patterns.attribution_trailers for key in rule.keys
        }
        self._malformed: dict[str, MalformedTrailerCheck] = {}
        for check in patterns.malformed_trailer_checks:
            for key in check.keys:
                self._malformed.setdefault(key, check)
        self._markers: dict[str, tuple[str, str, str]] = {
            _marker_key(line): (marker.id, marker.agent, line)
            for marker in patterns.message_markers
            for line in marker.lines
        }

    def detect(self, context: CommitContext) -> Sequence[Finding]:
        commit = context.commit
        require_complete_trailers(commit)
        findings: list[Finding] = []
        for trailer in commit.trailers:
            key = trailer.normalized_key
            if (rule := self._attribution.get(key)) is not None:
                finding = self._attribution_finding(rule, trailer, commit.sha)
                if finding is not None:
                    findings.append(finding)
            if (check := self._malformed.get(key)) is not None:
                finding = self._malformed_finding(check, trailer, commit.sha)
                if finding is not None:
                    findings.append(finding)
        findings.extend(self._marker_findings(commit.message, commit.sha))
        return findings

    # ------------------------------------------------------------------ #
    def _attribution_finding(
        self, rule: TrailerRule, trailer: Trailer, sha: str | None
    ) -> Finding | None:
        key_reason = MatchReason(
            kind=MatchKind.TRAILER_KEY,
            value=trailer.normalized_key,
            rule=f"{PATTERNS_FILE}#{rule.id}",
        )
        reasons: tuple[MatchReason, ...]
        if rule.match == "key_present":
            if normalize_text(trailer.value) in rule.ignore_values:
                return None
            reasons, confidence, subject = (key_reason,), Confidence.HIGH, "AI assistance"
        else:
            match = self._rules.ai_matcher.match(trailer.name, trailer.email)
            if match is None:
                return None
            reasons = (key_reason, *match.reasons)
            confidence, subject = match.confidence, f"the AI agent {match.display_name}"
        return Finding(
            detector=self.name,
            rule_id=RULE_AI_TRAILER,
            severity=Severity.HIGH,
            confidence=confidence,
            title="AI attribution trailer detected",
            message=f"The {trailer.key!r} trailer attributes the commit to {subject}.",
            evidence=(
                Evidence(
                    source=EvidenceSource.TRAILER,
                    value=trailer.raw,
                    line_number=trailer.line_number,
                    matched=reasons,
                ),
            ),
            commit_sha=sha,
            remediation="Remove the AI attribution trailer from the commit message before pushing.",
        )

    def _malformed_finding(
        self, check: MalformedTrailerCheck, trailer: Trailer, sha: str | None
    ) -> Finding | None:
        # Characters before a key (a quoted "> Signed-off-by:" line) are recorded so that
        # attribution cannot hide behind them, but are not by themselves malformed.
        notes = [
            f"trailer: {issue.value}"
            for issue in trailer.issues
            if issue is not TrailerIssue.LEADING_CHARACTERS
        ]
        if check.require_identity:
            notes.extend(f"identity: {issue.value}" for issue in trailer.identity.issues)
        if not notes:
            return None
        return Finding(
            detector=self.name,
            rule_id=RULE_MALFORMED_TRAILER,
            severity=Severity.LOW,
            confidence=Confidence.HIGH,
            title="Malformed trailer",
            message=(
                f"The {trailer.key!r} trailer is malformed. Tools disagree on how to read "
                "malformed trailers, which can hide or misstate attribution."
            ),
            evidence=(
                Evidence(
                    source=EvidenceSource.TRAILER,
                    value=trailer.raw,
                    line_number=trailer.line_number,
                    notes=tuple(notes),
                ),
            ),
            commit_sha=sha,
            remediation="Rewrite the trailer as 'Key: Name <email>' or remove it.",
        )

    def _marker_findings(self, message: str, sha: str | None) -> list[Finding]:
        findings = []
        for line_number, line in enumerate(message.splitlines(), start=1):
            if len(line) > MAX_MARKER_LINE_CHARS:
                continue
            hit = self._markers.get(_marker_key(line))
            if hit is None:
                continue
            marker_id, agent_id, marker_line = hit
            agent = self._rules.agents[agent_id]
            findings.append(
                Finding(
                    detector=self.name,
                    rule_id=RULE_AI_TRAILER,
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    title="AI attribution footer detected",
                    message=(
                        f"The commit message contains an attribution line inserted by "
                        f"{agent.display_name}."
                    ),
                    evidence=(
                        Evidence(
                            source=EvidenceSource.MESSAGE,
                            value=line.strip(),
                            line_number=line_number,
                            matched=(
                                MatchReason(
                                    kind=MatchKind.MESSAGE_MARKER,
                                    value=marker_line,
                                    rule=f"{PATTERNS_FILE}#{marker_id}",
                                ),
                            ),
                        ),
                    ),
                    commit_sha=sha,
                    remediation="Remove the tool attribution line from the commit message.",
                )
            )
        return findings
