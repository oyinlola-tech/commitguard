"""The rule catalogue shown in the dashboard.

Rule IDs are the IDs the detectors emit and policies configure - the same
``ai_coauthor`` the CLI, hooks, GitHub Action and GitHub App print. This module
only *describes* them for display (name, detector, severity, remediation
steps); detection stays in :mod:`commitguard.detectors` and decisions in
:mod:`commitguard.policies`. A unit test checks that the catalogue matches the
registered detectors, the built-in policies and the severities the detectors
actually emit, so the description cannot drift from the behaviour.

Rules are bundled with the installed package and are trusted; there are no
repository-defined rules, so nothing here is editable from the dashboard.
"""

from dataclasses import dataclass
from functools import cache

from commitguard import __version__
from commitguard.controlplane.views import RuleDataFile, RuleDetail, RuleView
from commitguard.core.result import EvidenceSource, Severity
from commitguard.policies.defaults import DEFAULT_POLICIES
from commitguard.rules.loader import builtin_rules_fingerprint, load_builtin_rules
from commitguard.rules.models import (
    AI_DOMAINS_FILE,
    AI_IDENTITIES_FILE,
    BOT_IDENTITIES_FILE,
    PATTERNS_FILE,
)

RULES_VERSION_LENGTH = 12
NO_REWRITE_NOTICE = "CommitGuard does not rewrite Git history or modify commits automatically."


@dataclass(frozen=True, slots=True)
class RuleDescription:
    rule_id: str
    name: str
    detector: str
    severity: Severity
    evidence_sources: tuple[EvidenceSource, ...]
    data_files: tuple[str, ...]
    steps: tuple[str, ...]


_HISTORY_STEPS = (
    "Rewrite the affected commit on your branch (for example `git commit --amend` for the "
    "latest commit, or an interactive rebase for older ones) and push the corrected branch.",
    "CommitGuard scans the updated pull request or branch automatically; the violation is "
    "resolved when the commit is no longer part of it.",
)

CATALOG: tuple[RuleDescription, ...] = (
    RuleDescription(
        rule_id="ai_coauthor",
        name="AI co-author attribution",
        detector="coauthor",
        severity=Severity.HIGH,
        evidence_sources=(EvidenceSource.COAUTHOR_TRAILER,),
        data_files=(PATTERNS_FILE, AI_IDENTITIES_FILE, AI_DOMAINS_FILE),
        steps=(
            "Remove the Co-authored-by line that names the AI agent from the commit message.",
            *_HISTORY_STEPS,
        ),
    ),
    RuleDescription(
        rule_id="ai_identity",
        name="AI agent as author or committer",
        detector="identity",
        severity=Severity.HIGH,
        evidence_sources=(EvidenceSource.AUTHOR, EvidenceSource.COMMITTER),
        data_files=(AI_IDENTITIES_FILE, AI_DOMAINS_FILE),
        steps=(
            "Recreate the commit under the responsible human contributor's identity "
            "(for example `git commit --amend --reset-author`).",
            *_HISTORY_STEPS,
        ),
    ),
    RuleDescription(
        rule_id="ai_trailer",
        name="AI attribution trailer or footer",
        detector="trailer",
        severity=Severity.HIGH,
        evidence_sources=(EvidenceSource.TRAILER, EvidenceSource.MESSAGE),
        data_files=(PATTERNS_FILE,),
        steps=(
            "Remove the attribution trailer or tool-generated footer from the commit message.",
            *_HISTORY_STEPS,
        ),
    ),
    RuleDescription(
        rule_id="malformed_trailer",
        name="Malformed trailer",
        detector="trailer",
        severity=Severity.LOW,
        evidence_sources=(EvidenceSource.TRAILER,),
        data_files=(PATTERNS_FILE,),
        steps=(
            "Rewrite the trailer as `Key: Name <email>` or remove it; malformed trailers can be "
            "used to hide attribution from parsers.",
            *_HISTORY_STEPS,
        ),
    ),
    RuleDescription(
        rule_id="bot_identity",
        name="Automation or bot identity",
        detector="bot",
        severity=Severity.LOW,
        evidence_sources=(
            EvidenceSource.AUTHOR,
            EvidenceSource.COMMITTER,
            EvidenceSource.COAUTHOR_TRAILER,
        ),
        data_files=(BOT_IDENTITIES_FILE,),
        steps=(
            "Confirm the automation is expected in this repository.",
            "If it is, an administrator can lower the policy for bot_identity in the repository's "
            "trusted .commitguard.yaml; otherwise investigate where the commit came from.",
        ),
    ),
)

CATALOG_BY_ID = {entry.rule_id: entry for entry in CATALOG}


def rules_version() -> str:
    return builtin_rules_fingerprint()[:RULES_VERSION_LENGTH]


def remediation_steps(rule_id: str) -> tuple[str, ...]:
    entry = CATALOG_BY_ID.get(rule_id)
    steps = entry.steps if entry else ()
    return (*steps, NO_REWRITE_NOTICE)


def rule_view(entry: RuleDescription) -> RuleView:
    policy = DEFAULT_POLICIES[entry.rule_id]
    return RuleView(
        id=entry.rule_id,
        name=entry.name,
        description=policy.description,
        detector=entry.detector,
        severity=entry.severity,
        default_action=policy.action,
        source="bundled",
        trusted=True,
        status="active",
        rules_version=rules_version(),
        tool_version=__version__,
    )


def list_rules() -> tuple[RuleView, ...]:
    return tuple(rule_view(entry) for entry in CATALOG)


@cache
def _data_file_entries() -> dict[str, int]:
    rules = load_builtin_rules().rules
    patterns = rules.patterns
    return {
        AI_IDENTITIES_FILE: len(rules.ai_identities.agents),
        AI_DOMAINS_FILE: len(rules.ai_domains.domains),
        BOT_IDENTITIES_FILE: len(rules.bots.bots),
        PATTERNS_FILE: len(patterns.coauthor_trailer_keys)
        + len(patterns.attribution_trailers)
        + len(patterns.malformed_trailer_checks)
        + len(patterns.message_markers),
    }


def rule_detail(rule_id: str) -> RuleDetail | None:
    entry = CATALOG_BY_ID.get(rule_id)
    if entry is None:
        return None
    counts = _data_file_entries()
    return RuleDetail(
        rule=rule_view(entry),
        remediation=remediation_steps(rule_id),
        evidence_sources=tuple(source.label for source in entry.evidence_sources),
        data_files=tuple(RuleDataFile(name=name, entries=counts[name]) for name in entry.data_files),
        editable=False,
    )
