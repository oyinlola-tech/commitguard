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
  benchmark: a replacement character from malformed UTF-8 hid attribution);
  the same applies to non-ASCII letters and numbers (``\\u32acCo-authored-by:``,
  found by property-based fuzzing), because keys are ASCII.

Work is linear in the message size and bounded to :data:`MAX_TRAILERS`
trailers; anything beyond sets ``truncated`` so callers can fail closed.
No regular expressions are used.
"""

import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from commitguard.provenance.author import ParsedIdentity, parse_identity
from commitguard.provenance.normalization import is_latin_lookalike, normalize_trailer_key

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


def _is_ascii_alnum(char: str) -> bool:
    return char.isascii() and char.isalnum()


def _starts_key(char: str) -> bool:
    # Look-alike letters are part of a disguised key (U+0421 in "Co-authored-by"), not a prefix.
    return _is_ascii_alnum(char) or is_latin_lookalike(char)


def _skip_leading(text: str) -> str:
    start = 0
    while start < len(text) and start < MAX_LEADING_CHARACTERS and not _starts_key(text[start]):
        start += 1
    return text[start:].strip()


def _parse_line(line: str) -> tuple[str, str, list[TrailerIssue]] | None:
    text = unicodedata.normalize("NFKC", line).strip()
    if not text:
        return None
    stripped = line.strip()
    if _is_ascii_alnum(stripped[0]):
        # The common case, including every well-formed trailer: no prefix to consider.
        return _parse_text(text) if _is_ascii_alnum(text[0]) else None
    # Characters before the key are not part of it: symbols and punctuation ("> ", "- ",
    # U+FFFD) and non-ASCII letters or numbers (U+32AC, U+2460) must neither hide a
    # trailer nor turn a quoted or listed line into a malformed key. Keys are ASCII, so the
    # prefix is whatever precedes the first ASCII letter or digit, judged both after NFKC
    # (U+32AC becomes a CJK ideograph) and before it (U+2460 becomes "1", U+24DE a plain
    # "o"). When the readings disagree, the shortest key wins: it attributes the fewest
    # prefix characters to the key.
    readings = [
        _skip_leading(text),
        unicodedata.normalize("NFKC", _skip_leading(stripped)).strip(),
    ]
    if _is_ascii_alnum(text[0]):
        readings.append(text)
    best: tuple[str, str, list[TrailerIssue]] | None = None
    for candidate in dict.fromkeys(readings):
        if not candidate or not _starts_key(candidate[0]):
            continue
        parsed = _parse_text(candidate)
        if parsed is None or (best is not None and len(parsed[0]) >= len(best[0])):
            continue
        key, value, issues = parsed
        prefixed = candidate != text
        best = (key, value, [*issues, TrailerIssue.LEADING_CHARACTERS] if prefixed else issues)
    return best


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
