"""Contributor identities.

An identity is a *claimed* ``name <email>`` pair. Git performs no verification
of these values, so they are untrusted evidence, never proof of authorship.

Two models exist on purpose:

* :class:`Identity` - an author/committer as recorded in a commit object,
  where Git guarantees both fields exist (they may still be empty or odd);
* :class:`ParsedIdentity` - the best-effort parse of free text such as a
  ``Co-authored-by`` value, where either part may be missing or malformed.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from commitguard.provenance.normalization import normalize_email

GITHUB_NOREPLY_DOMAIN = "users.noreply.github.com"


class Identity(BaseModel):
    """A Git identity recorded in a commit (author or committer)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    email: str

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"


class IdentityIssue(StrEnum):
    """Why a free-text identity is not a well-formed ``Name <email>``."""

    EMPTY = "empty"
    MISSING_NAME = "missing_name"
    MISSING_EMAIL = "missing_email"
    MISSING_BRACKETS = "missing_brackets"
    UNBALANCED_BRACKETS = "unbalanced_brackets"
    INVALID_EMAIL = "invalid_email"
    TRAILING_CONTENT = "trailing_content"


class ParsedIdentity(BaseModel):
    """Best-effort parse of an identity string. Never raises on bad input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None
    email: str | None
    issues: tuple[IdentityIssue, ...] = ()

    @property
    def well_formed(self) -> bool:
        return not self.issues

    def __str__(self) -> str:
        if self.email is None:
            return self.name or ""
        return f"{self.name or ''} <{self.email}>".strip()


def is_plausible_email(value: str) -> bool:
    """Structural email check: one ``@``, non-empty local part and dotted domain.

    Intentionally not RFC 5322: the goal is to classify evidence, not to
    validate deliverability.
    """
    local, sep, domain = value.rpartition("@")
    return (
        bool(sep)
        and bool(local)
        and "@" not in local
        and "." in domain.strip(".")
        and not any(ch.isspace() or ch in "<>" for ch in value)
    )


def parse_identity(value: str) -> ParsedIdentity:
    """Parse ``Name <email>`` leniently, recording every deviation as an issue.

    Examples::

        "Claude <noreply@anthropic.com>"  -> name, email, no issues
        "Claude"                          -> name only, MISSING_EMAIL
        "Claude noreply@anthropic.com"    -> name, email, MISSING_BRACKETS
        "<invalid>"                       -> email "invalid", MISSING_NAME, INVALID_EMAIL
        "Claude <>"                       -> name only, MISSING_EMAIL
    """
    text = value.strip()
    if not text:
        return ParsedIdentity(name=None, email=None, issues=(IdentityIssue.EMPTY,))

    issues: list[IdentityIssue] = []
    open_index = text.find("<")
    close_index = text.find(">", open_index + 1) if open_index >= 0 else -1

    if open_index >= 0 and close_index > open_index:
        name = text[:open_index].strip() or None
        email = text[open_index + 1 : close_index].strip() or None
        trailing = text[close_index + 1 :].strip()
        if trailing:
            issues.append(IdentityIssue.TRAILING_CONTENT)
        if name is not None and (">" in name or "<" in name):
            issues.append(IdentityIssue.UNBALANCED_BRACKETS)
    elif open_index >= 0 or ">" in text:
        issues.append(IdentityIssue.UNBALANCED_BRACKETS)
        stripped = text.replace("<", " ").replace(">", " ")
        name, email = _split_bare_email(stripped)
    else:
        name, email = _split_bare_email(text)
        if email is not None:
            issues.append(IdentityIssue.MISSING_BRACKETS)

    if name is None:
        issues.append(IdentityIssue.MISSING_NAME)
    if email is None:
        issues.append(IdentityIssue.MISSING_EMAIL)
    elif not is_plausible_email(email):
        issues.append(IdentityIssue.INVALID_EMAIL)

    return ParsedIdentity(name=name, email=email, issues=tuple(dict.fromkeys(issues)))


def _split_bare_email(text: str) -> tuple[str | None, str | None]:
    """Split ``Name user@host`` on the last whitespace token containing ``@``."""
    tokens = text.split()
    for index in range(len(tokens) - 1, -1, -1):
        if "@" in tokens[index]:
            name = " ".join(tokens[:index] + tokens[index + 1 :]) or None
            return name, tokens[index]
    return (" ".join(tokens) or None), None


def github_login(email: str) -> str | None:
    """Return the GitHub login from a ``[id+]login@users.noreply.github.com`` address."""
    local, sep, domain = normalize_email(email).rpartition("@")
    if not sep or domain != GITHUB_NOREPLY_DOMAIN or not local:
        return None
    user_id, plus, login = local.partition("+")
    if plus:
        return login if user_id.isdigit() and login else None
    return local
