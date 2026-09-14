"""Schemas for rule files. Every model forbids unknown keys."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from commitguard.provenance.author import is_plausible_email
from commitguard.provenance.normalization import (
    normalize_domain,
    normalize_email,
    normalize_name,
    normalize_text,
    normalize_trailer_key,
)
from commitguard.security.validation import validate_identifier

_STRICT = ConfigDict(frozen=True, extra="forbid")

AI_IDENTITIES_FILE = "ai-identities.yaml"
AI_DOMAINS_FILE = "ai-domains.yaml"
BOT_IDENTITIES_FILE = "bot-identities.yaml"
PATTERNS_FILE = "patterns.yaml"


def _non_empty_after_normalization(values: tuple[str, ...], kind: str) -> tuple[str, ...]:
    for value in values:
        if not normalize_text(value):
            raise ValueError(f"{kind} entries must not be empty")
    return values


class IdentityRule(BaseModel):
    """An identity (AI agent or bot) described by aliases and addresses.

    * ``names`` - full-name aliases; a match alone is evidence (medium confidence).
    * ``ambiguous_names`` - aliases that are also common human names; only count
      together with an address/domain of the same agent.
    * ``name_prefixes`` - distinctive leading words (``Claude Opus`` matches
      ``Claude Opus 4.5``); a match alone is strong evidence.
    * ``emails`` / ``github_logins`` - exact identifiers; strong evidence.
    """

    model_config = _STRICT

    id: str
    display_name: str = Field(min_length=1)
    vendor: str = ""
    names: tuple[str, ...] = ()
    ambiguous_names: tuple[str, ...] = ()
    name_prefixes: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    github_logins: tuple[str, ...] = ()
    verified: bool = False
    reference: str = ""

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        return validate_identifier(value, kind="rule id")

    @field_validator("names", "ambiguous_names", "name_prefixes", "github_logins")
    @classmethod
    def _non_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _non_empty_after_normalization(value, "name/login")

    @field_validator("emails")
    @classmethod
    def _valid_emails(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for email in value:
            if not is_plausible_email(normalize_email(email)):
                raise ValueError(f"invalid email {email!r}")
        return value

    @model_validator(mode="after")
    def _has_identifiers(self) -> "IdentityRule":
        if not (self.names or self.name_prefixes or self.emails or self.github_logins):
            raise ValueError(f"rule {self.id!r} needs names, name_prefixes, emails or logins")
        return self


class AIIdentityRules(BaseModel):
    model_config = _STRICT

    schema_version: Literal[1]
    agents: tuple[IdentityRule, ...] = Field(min_length=1)


class DomainRule(BaseModel):
    """A vendor email domain associated with one agent.

    A domain alone is never enough: ``local_parts: automation`` (the default)
    means only automation-style local parts (``noreply@``) are strong evidence;
    other addresses at the domain only corroborate a name match.
    """

    model_config = _STRICT

    domain: str
    agent: str
    local_parts: Literal["automation", "any"] = "automation"
    include_subdomains: bool = False

    @field_validator("domain")
    @classmethod
    def _valid_domain(cls, value: str) -> str:
        normalized = normalize_domain(value)
        if "." not in normalized or normalized != value:
            raise ValueError(f"domain {value!r} must be lowercase and fully qualified")
        return value


class AIDomainRules(BaseModel):
    model_config = _STRICT

    schema_version: Literal[1]
    automation_local_parts: tuple[str, ...] = Field(min_length=1)
    domains: tuple[DomainRule, ...] = ()

    @field_validator("automation_local_parts")
    @classmethod
    def _normalized_local_parts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for part in value:
            if not part or part != normalize_text(part) or "@" in part or " " in part:
                raise ValueError(f"invalid automation local part {part!r}")
        return value


class BotIdentityRules(BaseModel):
    model_config = _STRICT

    schema_version: Literal[1]
    bots: tuple[IdentityRule, ...] = ()


def _canonical_keys(keys: tuple[str, ...]) -> tuple[str, ...]:
    for key in keys:
        if key != normalize_trailer_key(key) or not key:
            raise ValueError(f"trailer key {key!r} must be canonical (e.g. 'co-authored-by')")
    return keys


class TrailerRule(BaseModel):
    """A trailer that indicates AI attribution.

    ``match: key_present`` - the key itself is attribution (``AI-generated-by``).
    ``match: ai_identity`` - only when the value matches an AI identity rule
    (``Generated-by: protoc`` is fine, ``Generated-by: Claude Code`` is not).
    """

    model_config = _STRICT

    id: str
    keys: tuple[str, ...] = Field(min_length=1)
    match: Literal["key_present", "ai_identity"]
    description: str = Field(min_length=1)
    ignore_values: tuple[str, ...] = Field(
        default=("false", "no", "none", "n/a", "0"),
        description="Values (normalised) that negate the key, e.g. 'AI-assisted: no'",
    )

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        return validate_identifier(value, kind="trailer rule id")

    @field_validator("keys")
    @classmethod
    def _canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_keys(value)


class MalformedTrailerCheck(BaseModel):
    """Which trailer keys are checked for malformed structure/identities."""

    model_config = _STRICT

    keys: tuple[str, ...] = Field(min_length=1)
    require_identity: bool

    @field_validator("keys")
    @classmethod
    def _canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_keys(value)


class MessageMarkerRule(BaseModel):
    """Exact attribution lines inserted by tools (compared after normalisation).

    Lines must match *entirely*; there is no substring search, so ordinary
    wording such as "use AI service for recommendations" never matches.
    """

    model_config = _STRICT

    id: str
    agent: str
    lines: tuple[str, ...] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        return validate_identifier(value, kind="marker id")


class PatternRules(BaseModel):
    model_config = _STRICT

    schema_version: Literal[1]
    coauthor_trailer_keys: tuple[str, ...] = Field(min_length=1)
    attribution_trailers: tuple[TrailerRule, ...] = ()
    malformed_trailer_checks: tuple[MalformedTrailerCheck, ...] = ()
    message_markers: tuple[MessageMarkerRule, ...] = ()

    @field_validator("coauthor_trailer_keys")
    @classmethod
    def _canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_keys(value)


class RuleSet(BaseModel):
    """All rule files, cross-validated."""

    model_config = _STRICT

    ai_identities: AIIdentityRules
    ai_domains: AIDomainRules
    bots: BotIdentityRules
    patterns: PatternRules

    @model_validator(mode="after")
    def _cross_references(self) -> "RuleSet":
        agent_ids = [agent.id for agent in self.ai_identities.agents]
        _require_unique(agent_ids, "agent id")
        _require_unique([bot.id for bot in self.bots.bots], "bot id")
        known = set(agent_ids)
        for domain in self.ai_domains.domains:
            if domain.agent not in known:
                raise ValueError(f"domain {domain.domain!r} references unknown agent")
        _require_unique([d.domain for d in self.ai_domains.domains], "domain")
        for marker in self.patterns.message_markers:
            if marker.agent not in known:
                raise ValueError(f"message marker {marker.id!r} references unknown agent")
        _require_unique([r.id for r in self.patterns.attribution_trailers], "trailer rule id")
        _require_unique(
            [key for rule in self.patterns.attribution_trailers for key in rule.keys],
            "attribution trailer key",
        )
        coauthor = set(self.patterns.coauthor_trailer_keys)
        for rule in self.patterns.attribution_trailers:
            if coauthor & set(rule.keys):
                raise ValueError("co-author keys are handled by the coauthor detector")
        # Aliases shared between agents would make attribution ambiguous.
        _require_unique(
            [
                normalize_name(alias)
                for agent in self.ai_identities.agents
                for alias in (*agent.names, *agent.ambiguous_names, *agent.name_prefixes)
            ],
            "AI agent alias",
        )
        return self


def _require_unique(values: list[str], kind: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {kind} {value!r}")
        seen.add(value)
