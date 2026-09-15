"""Pagination, sorting and search input for list endpoints.

* ``limit`` defaults to :data:`DEFAULT_LIMIT` and is capped at :data:`MAX_LIMIT`:
  no endpoint returns an unbounded collection.
* Cursors are opaque, URL-safe strings. Large, append-mostly collections
  (scans, violations, audit events) use keyset cursors - the sort key of the
  last row returned - so pages stay fast and stable while new rows arrive.
  Small collections (repositories, members, policy versions) use offset cursors.
  A cursor only encodes a position: it grants nothing, because every page is
  still filtered by the caller's access scope.
* Sort keys come from per-endpoint allow-lists; a query parameter never names a
  column.
"""

import base64
import binascii
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from commitguard.controlplane.errors import InputValidationError

DEFAULT_LIMIT = 25
MAX_LIMIT = 100
MAX_CURSOR_CHARS = 512
MAX_SEARCH_CHARS = 100
MAX_OFFSET = 100_000

type CursorValue = str | int | float | None


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: list[T]
    next_cursor: str | None
    limit: int


def parse_limit(raw: str | None) -> int:
    if raw is None or raw == "":
        return DEFAULT_LIMIT
    if not raw.isascii() or not raw.isdigit() or len(raw) > 4:
        raise InputValidationError("limit must be a positive integer", field="limit")
    value = int(raw)
    if not 1 <= value <= MAX_LIMIT:
        raise InputValidationError(f"limit must be between 1 and {MAX_LIMIT}", field="limit")
    return value


def encode_cursor(values: Sequence[CursorValue]) -> str:
    raw = json.dumps(list(values), separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str | None, kinds: Sequence[type]) -> list[CursorValue] | None:
    """Decode a cursor whose values must have exactly the given types."""
    if cursor is None or cursor == "":
        return None
    if len(cursor) > MAX_CURSOR_CHARS or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
        raise InputValidationError("invalid cursor", field="cursor")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        values = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise InputValidationError("invalid cursor", field="cursor") from None
    if not isinstance(values, list) or len(values) != len(kinds):
        raise InputValidationError("invalid cursor", field="cursor")
    for value, kind in zip(values, kinds, strict=True):
        if kind is float and isinstance(value, int) and not isinstance(value, bool):
            continue
        if not isinstance(value, kind) or isinstance(value, bool):
            raise InputValidationError("invalid cursor", field="cursor")
    return values


def offset_cursor(cursor: str | None) -> int:
    values = decode_cursor(cursor, (int,))
    offset = int(values[0]) if values else 0  # type: ignore[arg-type]
    if not 0 <= offset <= MAX_OFFSET:
        raise InputValidationError("invalid cursor", field="cursor")
    return offset


def parse_search(raw: str | None) -> str | None:
    """Normalised free-text search: trimmed, bounded, printable."""
    if raw is None:
        return None
    value = " ".join(raw.split())
    if not value:
        return None
    if len(value) > MAX_SEARCH_CHARS:
        raise InputValidationError(
            f"search must be at most {MAX_SEARCH_CHARS} characters", field="q"
        )
    if any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in value):
        raise InputValidationError("search contains control characters", field="q")
    return value


def like_pattern(value: str) -> str:
    """A LIKE pattern matching ``value`` literally anywhere (``ESCAPE '\\'``)."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def parse_choice[E](raw: str | None, choices: dict[str, E], field: str) -> E | None:
    if raw is None or raw == "":
        return None
    if raw not in choices:
        allowed = ", ".join(sorted(choices))
        raise InputValidationError(f"{field} must be one of: {allowed}", field=field)
    return choices[raw]


def parse_int_id(raw: str | None, field: str) -> int | None:
    if raw is None or raw == "":
        return None
    if not raw.isascii() or not raw.isdigit() or len(raw) > 16 or int(raw) <= 0:
        raise InputValidationError(f"{field} must be a positive integer", field=field)
    return int(raw)


def parse_timestamp(raw: str | None, field: str) -> datetime | None:
    """An ISO 8601 date or date-time; naive values are UTC."""
    if raw is None or raw == "":
        return None
    if len(raw) > 40:
        raise InputValidationError(f"{field} must be an ISO 8601 date", field=field)
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        raise InputValidationError(f"{field} must be an ISO 8601 date", field=field) from None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


_HEX_RE = re.compile(r"\A[0-9a-f]{4,64}\Z")


def sha_prefix(value: str | None) -> str | None:
    """A lower-case hex prefix usable for commit SHA search, or None."""
    if value is None:
        return None
    candidate = value.lower()
    return candidate if _HEX_RE.match(candidate) else None
