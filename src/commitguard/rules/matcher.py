"""Deterministic identity matching against rule data.

Matching is exact on normalised values (see
:mod:`commitguard.provenance.normalization`) - never substring or fuzzy - and
combines evidence deliberately:

==========================================  ============  ==================
Evidence                                    Alone         Confidence
==========================================  ============  ==================
exact configured email                      match         high
GitHub login (noreply address or name)      match         high
distinctive name prefix (``Claude Opus``)   match         high
automation email at vendor domain           match         high
full-name alias (``Claude``)                match         medium
full-name alias + vendor domain             match         high
ambiguous alias (``Devin``) + vendor domain match         medium
ambiguous alias alone                       no match      -
vendor domain alone (``jane@anthropic.com``) no match     -
==========================================  ============  ==================
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence

from pydantic import BaseModel, ConfigDict

from commitguard.core.result import Confidence, MatchKind, MatchReason
from commitguard.provenance.author import github_login
from commitguard.provenance.normalization import (
    name_tokens,
    normalize_domain,
    normalize_email,
    normalize_name,
)
from commitguard.rules.models import (
    AI_DOMAINS_FILE,
    AI_IDENTITIES_FILE,
    BOT_IDENTITIES_FILE,
    AIDomainRules,
    DomainRule,
    IdentityRule,
    RuleSet,
)

_STRONG = frozenset(
    {
        MatchKind.EMAIL,
        MatchKind.GITHUB_LOGIN,
        MatchKind.NAME_PREFIX,
        MatchKind.AUTOMATION_EMAIL,
    }
)


class IdentityMatch(BaseModel):
    """The rule an identity matched, how confidently, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    display_name: str
    vendor: str
    confidence: Confidence
    reasons: tuple[MatchReason, ...]


class IdentityMatcher:
    """Match ``(name, email)`` pairs against identity rules (and optional domains)."""

    def __init__(
        self,
        rules: Sequence[IdentityRule],
        *,
        source: str,
        domains: AIDomainRules | None = None,
        domain_source: str = "",
    ) -> None:
        self._rules = {rule.id: rule for rule in rules}
        self._source = source
        self._domain_source = domain_source
        self._names = _index(rules, lambda r: r.names, normalize_name)
        self._ambiguous = _index(rules, lambda r: r.ambiguous_names, normalize_name)
        self._emails = _index(rules, lambda r: r.emails, normalize_email)
        self._logins = _index(rules, lambda r: r.github_logins, normalize_name)
        self._prefixes = sorted(
            (name_tokens(prefix), rule.id) for rule in rules for prefix in rule.name_prefixes
        )
        self._automation_local_parts = frozenset(domains.automation_local_parts if domains else ())
        self._domains: dict[str, DomainRule] = {
            d.domain: d for d in (domains.domains if domains else ()) if d.agent in self._rules
        }

    def match(self, name: str | None, email: str | None) -> IdentityMatch | None:
        reasons: dict[str, list[MatchReason]] = defaultdict(list)

        if name:
            normalized = normalize_name(name)
            self._lookup(reasons, self._names, normalized, MatchKind.NAME)
            self._lookup(reasons, self._ambiguous, normalized, MatchKind.AMBIGUOUS_NAME)
            if normalized.endswith("[bot]"):  # a GitHub App account name used verbatim
                self._lookup(reasons, self._logins, normalized, MatchKind.GITHUB_LOGIN)
            tokens = tuple(normalized.split())
            for prefix, rule_id in self._prefixes:
                if tokens[: len(prefix)] == prefix and len(tokens) >= len(prefix):
                    reasons[rule_id].append(
                        self._reason(MatchKind.NAME_PREFIX, " ".join(prefix), rule_id)
                    )

        if email:
            normalized_email = normalize_email(email)
            self._lookup(reasons, self._emails, normalized_email, MatchKind.EMAIL)
            login = github_login(normalized_email)
            if login is not None:
                self._lookup(reasons, self._logins, normalize_name(login), MatchKind.GITHUB_LOGIN)
            self._match_domain(reasons, normalized_email)

        candidates = [
            match
            for rule_id, rule_reasons in reasons.items()
            if (match := self._evaluate(rule_id, rule_reasons)) is not None
        ]
        if not candidates:
            return None
        # Deterministic: strongest confidence, then most reasons, then rule id.
        return min(candidates, key=lambda m: (-m.confidence.rank, -len(m.reasons), m.rule_id))

    # ------------------------------------------------------------------ #
    def _reason(self, kind: MatchKind, value: str, rule_id: str) -> MatchReason:
        return MatchReason(kind=kind, value=value, rule=f"{self._source}#{rule_id}")

    def _lookup(
        self,
        reasons: dict[str, list[MatchReason]],
        index: dict[str, str],
        value: str,
        kind: MatchKind,
    ) -> None:
        rule_id = index.get(value)
        if rule_id is not None:
            reasons[rule_id].append(self._reason(kind, value, rule_id))

    def _match_domain(self, reasons: dict[str, list[MatchReason]], email: str) -> None:
        local, _, domain = email.rpartition("@")
        domain = normalize_domain(domain)
        rule = self._domains.get(domain)
        if rule is None:
            rule = next(
                (
                    d
                    for d in self._domains.values()
                    if d.include_subdomains and domain.endswith("." + d.domain)
                ),
                None,
            )
        if rule is None or not local:
            return
        base_local = local.split("+", 1)[0]
        if rule.local_parts == "any" or base_local in self._automation_local_parts:
            kind, value = MatchKind.AUTOMATION_EMAIL, email
        else:
            kind, value = MatchKind.VENDOR_DOMAIN, domain
        reference = f"{self._domain_source}#{rule.domain}"
        reasons[rule.agent].append(MatchReason(kind=kind, value=value, rule=reference))

    def _evaluate(self, rule_id: str, rule_reasons: list[MatchReason]) -> IdentityMatch | None:
        kinds = {reason.kind for reason in rule_reasons}
        if kinds & _STRONG or {MatchKind.NAME, MatchKind.VENDOR_DOMAIN} <= kinds:
            confidence = Confidence.HIGH
        elif (
            MatchKind.NAME in kinds or {MatchKind.AMBIGUOUS_NAME, MatchKind.VENDOR_DOMAIN} <= kinds
        ):
            confidence = Confidence.MEDIUM
        else:
            return None
        rule = self._rules[rule_id]
        return IdentityMatch(
            rule_id=rule.id,
            display_name=rule.display_name,
            vendor=rule.vendor,
            confidence=confidence,
            reasons=tuple(dict.fromkeys(rule_reasons)),
        )


def _index(
    rules: Iterable[IdentityRule],
    values: Callable[[IdentityRule], tuple[str, ...]],
    normalize: Callable[[str], str],
) -> dict[str, str]:
    index: dict[str, str] = {}
    for rule in rules:
        for value in values(rule):
            key = normalize(value)
            existing = index.setdefault(key, rule.id)
            if existing != rule.id:
                raise ValueError(f"{value!r} is claimed by both {existing!r} and {rule.id!r}")
    return index


class CompiledRules:
    """A validated :class:`RuleSet` plus the matchers built from it (pure, no I/O)."""

    def __init__(self, rules: RuleSet) -> None:
        self.rules = rules
        self.ai_matcher = IdentityMatcher(
            rules.ai_identities.agents,
            source=AI_IDENTITIES_FILE,
            domains=rules.ai_domains,
            domain_source=AI_DOMAINS_FILE,
        )
        self.bot_matcher = IdentityMatcher(rules.bots.bots, source=BOT_IDENTITIES_FILE)
        self.agents = {agent.id: agent for agent in rules.ai_identities.agents}
