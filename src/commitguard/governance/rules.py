"""Organization rules: declarative identity data added to the trusted detection rules.

Detection logic stays in the trusted detection engine. What an organization can
add is **data** of the kinds the bundled rule files already contain:

* ``ai_identities`` - additional AI agents (e.g. an internal coding agent),
  detected by the same ``coauthor``, ``identity`` and ``trailer`` detectors and
  reported under the same rule IDs (``ai_coauthor``, ``ai_identity``, ...);
* ``bot_identities`` - additional automation accounts (``bot_identity``).

Each entry has an ``id``, a display name and exact identifiers - full names,
distinctive name prefixes, e-mail addresses, GitHub logins. Matching uses the
bundled matcher: exact comparison after normalisation. There are **no regular
expressions, wildcards, expressions, scripts or imports**, so an organization
rule cannot execute code and cannot cause catastrophic backtracking (ReDoS).
The same validation as the bundled files applies (plausible e-mails, no empty
aliases) plus limits on entries and values, and an alias already claimed by a
bundled agent is rejected rather than silently overriding it.

Trust levels
============

==================  ============================  ===================================
Level               Source                        Can
==================  ============================  ===================================
``built_in``        rule files in the package     define rules and detectors
``organization``    this module, versioned        add identity data to existing rules
``repository``      ``.commitguard.yaml``         configure policies only - no rules
==================  ============================  ===================================

Versions are immutable (database triggers). A scan records the rules version
it used: ``<bundled rules fingerprint>`` or ``<fingerprint>+org-v<N>``. Changing
organization rules needs ``rules:manage``, is audited, and invalidates the
effective policy of every repository of the organization, so scheduled scans
re-evaluate branches under the new rules.
"""

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from commitguard.audit.models import Actor, AuditEventType
from commitguard.controlplane.access import Permission, Principal
from commitguard.controlplane.errors import ConflictError, InputValidationError
from commitguard.github.storage import SqliteStateStore
from commitguard.governance.cache import invalidate_repositories
from commitguard.governance.common import MAX_REASON_CHARS, dt, req_dt, require, text, ts
from commitguard.rules.loader import builtin_rules_fingerprint, load_builtin_rules
from commitguard.rules.matcher import CompiledRules
from commitguard.rules.models import AIIdentityRules, BotIdentityRules, IdentityRule, RuleSet
from commitguard.security.hashing import sha256_hex
from commitguard.services.audit import AuditService

MAX_ENTRIES = 100
MAX_VALUES = 20
MAX_VALUE_CHARS = 128
MAX_DOCUMENT_BYTES = 65_536
ID_PREFIX = "org_"
TRUST_LEVELS = ("built_in", "organization", "repository")


class OrganizationIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=48, pattern=r"^[a-z][a-z0-9_]{0,47}$")
    display_name: str = Field(min_length=1, max_length=MAX_VALUE_CHARS)
    names: tuple[str, ...] = ()
    name_prefixes: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    github_logins: tuple[str, ...] = ()

    @field_validator("names", "name_prefixes", "emails", "github_logins")
    @classmethod
    def _bounded(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > MAX_VALUES:
            raise ValueError(f"at most {MAX_VALUES} values")
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item) > MAX_VALUE_CHARS:
                raise ValueError(f"values must be 1-{MAX_VALUE_CHARS} characters")
            if any(ord(ch) < 32 for ch in item):
                raise ValueError("values must not contain control characters")
        return tuple(dict.fromkeys(item.strip() for item in value))


class OrganizationRuleDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ai_identities: tuple[OrganizationIdentity, ...] = ()
    bot_identities: tuple[OrganizationIdentity, ...] = ()

    @field_validator("ai_identities", "bot_identities")
    @classmethod
    def _limit(cls, value: tuple[OrganizationIdentity, ...]) -> tuple[OrganizationIdentity, ...]:
        if len(value) > MAX_ENTRIES:
            raise ValueError(f"at most {MAX_ENTRIES} entries")
        return value

    def canonical(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


class OrganizationRulesView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_id: int
    version: int
    fingerprint: str | None
    document: OrganizationRuleDocument
    rules_version: str  # as recorded with scans
    created_at: datetime | None
    created_by: str | None
    reason: str | None
    trust_levels: tuple[dict[str, str], ...]
    can_manage: bool


def _identity_rule(entry: OrganizationIdentity) -> IdentityRule:
    return IdentityRule(
        id=f"{ID_PREFIX}{entry.id}",
        display_name=entry.display_name,
        vendor="organization",
        names=entry.names,
        name_prefixes=entry.name_prefixes,
        emails=entry.emails,
        github_logins=entry.github_logins,
        verified=False,
        reference="organization rules",
    )


def compile_rules(document: OrganizationRuleDocument) -> CompiledRules:
    """The bundled rules plus the organization's identity data. Raises ``ValueError``."""
    builtin = load_builtin_rules().rules
    rules = RuleSet(
        ai_identities=AIIdentityRules(
            schema_version=1,
            agents=(
                *builtin.ai_identities.agents,
                *(_identity_rule(e) for e in document.ai_identities),
            ),
        ),
        ai_domains=builtin.ai_domains,
        bots=BotIdentityRules(
            schema_version=1,
            bots=(*builtin.bots.bots, *(_identity_rule(e) for e in document.bot_identities)),
        ),
        patterns=builtin.patterns,
    )
    return CompiledRules(rules)


@lru_cache(maxsize=64)
def _compiled_for(fingerprint: str, canonical: str) -> CompiledRules:
    del fingerprint  # part of the cache key only
    return compile_rules(OrganizationRuleDocument.model_validate_json(canonical))


def rules_version_label(version: int) -> str:
    base = builtin_rules_fingerprint()
    return base if version == 0 else f"{base[:16]}+org-v{version}"


def parse_document(raw: object) -> OrganizationRuleDocument:
    if not isinstance(raw, dict):
        raise InputValidationError("rules must be an object", field="rules")
    try:
        document = OrganizationRuleDocument.model_validate(raw)
    except ValidationError as exc:
        error = exc.errors()[0]
        location = ".".join(str(part) for part in error.get("loc", ()))
        raise InputValidationError(
            f"Invalid rule {location}: {error.get('msg', 'invalid value')}",
            field=f"rules.{location}",
        ) from None
    for kind, entries in (
        ("ai_identities", document.ai_identities),
        ("bot_identities", document.bot_identities),
    ):
        ids = [e.id for e in entries]
        if len(ids) != len(set(ids)):
            raise InputValidationError(f"duplicate id in {kind}", field=f"rules.{kind}")
        for entry in entries:
            if not (entry.names or entry.name_prefixes or entry.emails or entry.github_logins):
                raise InputValidationError(
                    f"{kind} entry {entry.id!r} needs names, prefixes, e-mails or logins",
                    field=f"rules.{kind}",
                )
    if len(document.canonical().encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise InputValidationError("The organization rules are too large.", field="rules")
    try:
        compile_rules(document)
    except ValidationError as exc:
        message = str(exc.errors()[0].get("msg", "invalid rule"))[:300]
        raise InputValidationError(
            f"The rules conflict with the bundled rules: {message}", field="rules"
        ) from None
    except ValueError as exc:
        message = str(exc)[:300] or "conflicts with bundled rules"
        raise InputValidationError(
            f"The rules conflict with the bundled rules: {message}", field="rules"
        ) from None
    return document


class OrganizationRuleService:
    def __init__(
        self,
        store: SqliteStateStore,
        audit: AuditService,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._audit = audit
        self._now = now

    def _latest(self, db: sqlite3.Connection | SqliteStateStore, account_id: int) -> Any:
        sql = (
            "SELECT * FROM organization_rule_versions WHERE account_id = ? "
            "ORDER BY version DESC LIMIT 1"
        )
        if isinstance(db, sqlite3.Connection):
            return db.execute(sql, (account_id,)).fetchone()
        rows = db.query(sql, (account_id,))
        return rows[0] if rows else None

    def current(
        self, account_id: int, db: sqlite3.Connection | None = None
    ) -> tuple[int, OrganizationRuleDocument, str | None]:
        row = self._latest(db if db is not None else self._store, account_id)
        if row is None:
            return 0, OrganizationRuleDocument(), None
        stored = str(row["document"])
        if sha256_hex(stored.encode("utf-8")) != row["fingerprint"]:
            # A tampered document is not used: fail closed to the bundled rules only,
            # which never weakens detection (organization rules only add identities).
            return int(row["version"]), OrganizationRuleDocument(), row["fingerprint"]
        return (
            int(row["version"]),
            OrganizationRuleDocument.model_validate_json(stored),
            str(row["fingerprint"]),
        )

    def compiled(self, account_id: int) -> tuple[CompiledRules | None, str]:
        """Rules for scans of the account and the version label to record."""
        version, document, fingerprint = self.current(account_id)
        if version == 0 or not (document.ai_identities or document.bot_identities):
            return None, rules_version_label(0)
        return _compiled_for(fingerprint or "", document.canonical()), rules_version_label(version)

    def view(self, principal: Principal, account_id: int) -> OrganizationRulesView:
        require(principal, Permission.RULES_READ, account_id)
        row = self._latest(self._store, account_id)
        version, document, fingerprint = self.current(account_id)
        return OrganizationRulesView(
            organization_id=account_id,
            version=version,
            fingerprint=fingerprint,
            document=document,
            rules_version=rules_version_label(version),
            created_at=dt(row["created_at"]) if row else None,
            created_by=row["created_by_login"] if row else None,
            reason=row["reason"] if row else None,
            trust_levels=(
                {
                    "level": "built_in",
                    "source": "rule files bundled with CommitGuard",
                    "can": "define rules and detectors",
                },
                {
                    "level": "organization",
                    "source": "organization rules (this page), versioned",
                    "can": "add AI agent and bot identities to existing rules",
                },
                {
                    "level": "repository",
                    "source": ".commitguard.yaml",
                    "can": "configure policies; cannot add rules or weaken mandatory policy",
                },
            ),
            can_manage=principal.can(Permission.RULES_MANAGE, account_id),
        )

    def update(
        self,
        principal: Principal,
        account_id: int,
        *,
        expected_version: object,
        rules: object,
        reason: object,
    ) -> OrganizationRulesView:
        require(principal, Permission.RULES_MANAGE, account_id)
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 0
        ):
            raise InputValidationError(
                "expected_version must be a non-negative integer", field="expected_version"
            )
        document = parse_document(rules)
        reason_text = text(reason, "reason", limit=MAX_REASON_CHARS, required=True)
        canonical = document.canonical()
        now = self._now()
        with self._store.transaction() as db:
            latest, current, _ = self.current(account_id, db)
            if latest != expected_version:
                raise ConflictError(
                    f"The organization rules were changed by someone else (now version {latest})."
                )
            if current.canonical() == canonical:
                raise InputValidationError("The rules are identical to the current version.")
            removed = sorted(
                {e.id for e in (*current.ai_identities, *current.bot_identities)}
                - {e.id for e in (*document.ai_identities, *document.bot_identities)}
            )
            db.execute(
                "INSERT INTO organization_rule_versions (account_id, version, document, "
                "fingerprint, created_at, created_by_id, created_by_login, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    account_id,
                    latest + 1,
                    canonical,
                    sha256_hex(canonical.encode("utf-8")),
                    ts(now),
                    principal.user_id,
                    principal.login,
                    reason_text,
                ),
            )
            invalidate_repositories(db, account_id, None, now)
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.ORGANIZATION_RULES_CHANGED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=account_id,
                    old_version=latest,
                    new_version=latest + 1,
                    ai_identities=len(document.ai_identities),
                    bot_identities=len(document.bot_identities),
                    removed=",".join(removed) or None,
                    reason=reason_text,
                ),
            )
        self._audit.log_stored(stored)
        return self.view(principal, account_id)

    def history(self, principal: Principal, account_id: int) -> list[dict[str, object]]:
        require(principal, Permission.RULES_READ, account_id)
        return [
            {
                "version": int(r["version"]),
                "fingerprint": r["fingerprint"],
                "created_at": req_dt(r["created_at"]).isoformat(),
                "created_by": r["created_by_login"],
                "reason": r["reason"],
            }
            for r in self._store.query(
                "SELECT version, fingerprint, created_at, created_by_login, reason FROM "
                "organization_rule_versions WHERE account_id = ? ORDER BY version DESC LIMIT 50",
                (account_id,),
            )
        ]
