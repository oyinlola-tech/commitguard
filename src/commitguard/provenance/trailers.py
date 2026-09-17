"""Commit message trailer parsing.

Trailers are ``Key: value`` lines, conventionally in the last paragraph of a
commit message. They are the primary place AI agents record attribution::

    feat: implement authentication

    Co-authored-by: Claude <noreply@anthropic.com>

Parsing is lenient and never raises. Because attribution can be hidden or
mangled deliberately, the parser also records:

* trailer-formatted lines **outside** the final paragraph
  (``in_trailer_block=False``) - Git ignores them, a reader does not;
* ``*-by`` keys written without a colon (``Co-authored-by Claude ...``) or with
  spaces/underscores/invisible characters in the key;
* indented lines that are themselves trailers, instead of folding them into
  the previous value as a continuation (which would hide them);
* every Unicode line separator (``\\u2028``, ``\\r``...), not only ``\\n``;
* keys preceded by symbols or punctuation (``\\ufffdCo-authored-by:``,
  ``> Co-authored-by:``, ``• Co-authored-by:``): the prefix is ignored and the
  trailer is recorded with ``LEADING_CHARACTERS`` (found by the detection
  benchmark: a replacement character from malformed UTF-8 hid attribution).

Work is linear in the message size and bounded to :data:`MAX_TRAILERS`
trailers; anything beyond sets ``truncated`` so callers can fail closed.
No regular expressions are used.
"""

import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from commitguard.provenance.author import ParsedIdentity, parse_identity
from commitguard.provenance.normalization import normalize_trailer_key

COAUTHOR_TRAILER_KEY = "co-authored-by"
MAX_TRAILERS = 1000
MAX_KEY_LENGTH = 64
#: At most this many leading symbol/punctuation characters are skipped before a key.
MAX_LEADING_CHARACTERS = 16


class TrailerIssue(StrEnum):
    """Structural problems with a trailer line."""

    MISSING_SEPARATOR = "missing_separator"
    NONSTANDARD_KEY = "nonstandard_key"
    EMPTY_VALUE = "empty_value"
    LEADING_CHARACTERS = "leading_characters"


class Trailer(BaseModel):
    """A trailer (or trailer-like line) found in a commit message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(description="Key as written (after Unicode NFKC)")
    value: str
    raw: str = Field(description="Original line text, for evidence")
    line_number: int = Field(ge=1, description="1-based line number in the message")
    in_trailer_block: bool = Field(description="True if in Git's trailer block (final paragraph)")
    issues: tuple[TrailerIssue, ...] = ()
    identity: ParsedIdentity

    @property
    def normalized_key(self) -> str:
        return normalize_trailer_key(self.key)

    @property
    def name(self) -> str | None:
        return self.identity.name

    @property
    def email(self) -> str | None:
        return self.identity.email

    @property
    def well_formed(self) -> bool:
        return not self.issues


class ParsedTrailers(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trailers: tuple[Trailer, ...] = ()
    truncated: bool = False


@dataclass
class _Draft:
    key: str
    value_parts: list[str]
    raw_parts: list[str]
    line_number: int
    last_line: int
    paragraph: int
    issues: list[TrailerIssue] = field(default_factory=list)


def _is_standard_key(key: str) -> bool:
    return (
        0 < len(key) <= MAX_KEY_LENGTH
        and key[0].isascii()
        and key[0].isalnum()
        and all((ch.isascii() and ch.isalnum()) or ch == "-" for ch in key)
    )


def _is_by_key(key: str) -> bool:
    """A ``*-by`` key possibly written with spaces, underscores or invisible chars."""
    if not 0 < len(key) <= MAX_KEY_LENGTH:
        return False
    normalized = normalize_trailer_key(key)
    return (
        normalized.endswith("-by")
        and len(normalized) > 3
        and all((ch.isascii() and ch.isalnum()) or ch == "-" for ch in normalized)
    )


def _parse_line(line: str) -> tuple[str, str, list[TrailerIssue]] | None:
    text = unicodedata.normalize("NFKC", line).strip()
    if not text:
        return None
    parsed = _parse_text(text)
    if parsed is not None or text[0].isalnum():
        return parsed
    # Symbols or punctuation before the key must not hide a trailer.
    start = 0
    while start < len(text) and start < MAX_LEADING_CHARACTERS and not text[start].isalnum():
        start += 1
    rest = text[start:].strip()
    if not rest or not rest[0].isalnum():
        return None
    retried = _parse_text(rest)
    if retried is None:
        return None
    key, value, issues = retried
    return key, value, [*issues, TrailerIssue.LEADING_CHARACTERS]


def _parse_text(text: str) -> tuple[str, str, list[TrailerIssue]] | None:

    colon = text.find(":")
    if 0 < colon <= MAX_KEY_LENGTH + 8:
        written_key = text[:colon]
        key = written_key.rstrip()
        value = text[colon + 1 :].strip()
        if _is_standard_key(key) and not value.startswith("//"):  # skip "https://..."
            issues = [] if key == written_key else [TrailerIssue.NONSTANDARD_KEY]
            return key, value, issues
        if _is_by_key(key):
            return key, value, [TrailerIssue.NONSTANDARD_KEY]

    # "Co-authored-by Claude <...>" / "Co-authored-by=Claude"
    head = text.split(None, 1)[0]
    key, eq, after_eq = head.partition("=")
    if _is_by_key(key):
        rest = (after_eq + text[len(head) :]) if eq else text[len(head) :]
        value = rest.strip().lstrip("=").strip()
        if value:
            return key, value, [TrailerIssue.MISSING_SEPARATOR]
    return None


def parse_trailers(message: str) -> ParsedTrailers:
    """Extract trailers from a raw commit message. Never raises."""
    drafts: list[_Draft] = []
    truncated = False
    paragraph = 0
    previous_blank = True
    last_content_paragraph = 0

    for line_number, line in enumerate(message.splitlines(), start=1):
        if not line.strip():
            if not previous_blank:
                paragraph += 1
            previous_blank = True
            continue
        previous_blank = False
        last_content_paragraph = paragraph

        parsed = _parse_line(line)
        if parsed is not None and line_number == 1 and not _is_by_key(parsed[0]):
            parsed = None  # the subject ("feat: ...") is not a trailer
        if parsed is None:
            continuation = line[:1].isspace()
            if continuation and drafts and drafts[-1].last_line == line_number - 1:
                # Collected as parts and joined once: repeated string concatenation
                # would be quadratic on messages with many continuation lines.
                drafts[-1].value_parts.append(line.strip())
                drafts[-1].raw_parts.append(line.strip())
                drafts[-1].last_line = line_number
            continue

        if len(drafts) >= MAX_TRAILERS:
            truncated = True
            break
        key, value, issues = parsed
        drafts.append(
            _Draft(key, [value], [line.strip()], line_number, line_number, paragraph, issues)
        )

    trailers = []
    for draft in drafts:
        value = " ".join(part for part in draft.value_parts if part)
        issues = list(draft.issues)
        if not value:
            issues.append(TrailerIssue.EMPTY_VALUE)
        trailers.append(
            Trailer(
                key=draft.key,
                value=value,
                raw=" ".join(draft.raw_parts),
                line_number=draft.line_number,
                in_trailer_block=draft.paragraph == last_content_paragraph
                and last_content_paragraph > 0,
                issues=tuple(issues),
                identity=parse_identity(value),
            )
        )
    return ParsedTrailers(trailers=tuple(trailers), truncated=truncated)
