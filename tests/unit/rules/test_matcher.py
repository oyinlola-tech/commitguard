import pytest

from commitguard.core.result import Confidence, MatchKind
from commitguard.rules.matcher import IdentityMatcher
from commitguard.rules.models import AIDomainRules, DomainRule, IdentityRule


@pytest.fixture
def matcher() -> IdentityMatcher:
    rules = [
        IdentityRule(
            id="claude",
            display_name="Claude",
            names=("Claude", "Claude Code"),
            name_prefixes=("Claude Opus",),
            emails=("noreply@anthropic.com",),
        ),
        IdentityRule(
            id="devin",
            display_name="Devin",
            names=("Devin AI",),
            ambiguous_names=("Devin",),
            github_logins=("devin-ai-integration[bot]",),
        ),
    ]
    domains = AIDomainRules(
        schema_version=1,
        automation_local_parts=("noreply", "bot"),
        domains=(
            DomainRule(domain="anthropic.com", agent="claude"),
            DomainRule(domain="cognition.ai", agent="devin", include_subdomains=True),
        ),
    )
    return IdentityMatcher(rules, source="ai.yaml", domains=domains, domain_source="domains.yaml")


@pytest.mark.parametrize(
    ("name", "email", "rule", "confidence"),
    [
        ("Claude", None, "claude", Confidence.MEDIUM),
        ("claude code", "x@example.com", "claude", Confidence.MEDIUM),
        ("Anyone", "NoReply@Anthropic.com", "claude", Confidence.HIGH),
        ("Anyone", "bot+ci@anthropic.com", "claude", Confidence.HIGH),
        ("Claude Opus 4.5 (1M context)", "x@example.com", "claude", Confidence.HIGH),
        ("Claude", "jane@anthropic.com", "claude", Confidence.HIGH),
        ("Devin", "devin@eng.cognition.ai", "devin", Confidence.MEDIUM),
        ("x", "1+devin-ai-integration[bot]@users.noreply.github.com", "devin", Confidence.HIGH),
        ("devin-ai-integration[bot]", None, "devin", Confidence.HIGH),
    ],
)
def test_matches(matcher: IdentityMatcher, name, email, rule, confidence) -> None:  # type: ignore[no-untyped-def]
    match = matcher.match(name, email)
    assert match is not None
    assert match.rule_id == rule
    assert match.confidence is confidence
    assert all(r.rule.startswith(("ai.yaml#", "domains.yaml#")) for r in match.reasons)


@pytest.mark.parametrize(
    ("name", "email"),
    [
        ("Claudette", None),
        ("Claude Dupont", "claude@dupont.fr"),
        ("Opus", None),
        ("Jane Doe", "jane@anthropic.com"),
        ("Devin", "devin@example.com"),
        ("Devin", None),
        (None, None),
        ("", ""),
        ("Anyone", "noreply@notanthropic.com"),
        ("Anyone", "noreply@anthropic.com.evil.io"),
    ],
)
def test_non_matches(matcher: IdentityMatcher, name, email) -> None:  # type: ignore[no-untyped-def]
    assert matcher.match(name, email) is None


def test_reasons_explain_the_match(matcher: IdentityMatcher) -> None:
    match = matcher.match("Claude", "noreply@anthropic.com")
    assert match is not None
    kinds = {r.kind for r in match.reasons}
    assert kinds == {MatchKind.NAME, MatchKind.EMAIL, MatchKind.AUTOMATION_EMAIL}


def test_conflicting_aliases_are_rejected() -> None:
    rules = [
        IdentityRule(id="a", display_name="A", names=("Agent",)),
        IdentityRule(id="b", display_name="B", names=("agent",)),
    ]
    with pytest.raises(ValueError, match="claimed by both"):
        IdentityMatcher(rules, source="x")


def test_builtin_rules_cover_requested_agents(rules) -> None:  # type: ignore[no-untyped-def]
    ids = {agent.id for agent in rules.rules.ai_identities.agents}
    assert {
        "claude",
        "chatgpt",
        "openai_codex",
        "github_copilot",
        "cursor",
        "gemini",
        "windsurf",
        "cline",
        "roo_code",
        "amazon_q",
        "codeium",
        "devin",
    } <= ids
