"""Secret values and redaction.

Credentials (the GitHub App private key, the webhook secret, JWTs, installation
tokens) are wrapped in :class:`Secret` as soon as they are read, so an
accidental ``repr()``, f-string or log call prints ``**********`` instead of
the value. Only code that must send the value calls :meth:`Secret.reveal`.

:class:`SecretRedactor` is the second line of defence: every secret CommitGuard
mints or loads is registered with it, and structured logs, error messages and
HTTP responses are passed through :func:`redact`, which also removes
credential-shaped strings (GitHub tokens, JWTs, PEM keys, ``Authorization``
header values) that were never registered.
"""

import hmac
import re
import threading
from typing import NoReturn

REDACTED = "[REDACTED]"
MIN_REGISTERED_SECRET_LENGTH = 8

_PATTERNS = (
    # PEM blocks (private keys), even if truncated.
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S
    ),
    # GitHub token formats: ghs_ (installation), ghp_, gho_, ghu_, ghr_, github_pat_.
    re.compile(r"\b(?:gh[posur]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    # Authorization header values and bearer/basic credentials.
    re.compile(r"(?i)(authorization\s*[:=]\s*)\S+(?:\s+\S+)?"),
    re.compile(r"(?i)\b((?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]{8,}"),
    # Webhook signatures are not secrets, but they are credential-derived noise.
    re.compile(r"sha256=[0-9a-fA-F]{64}"),
)

# JWTs (header.payload.signature, base64url, the header starting "eyJ") are found in two
# linear steps instead of one regular expression. The expression
# ``\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}`` backtracks quadratically
# on text such as "eyJ-eyJ-eyJ-..." (80 KB took 4.6 s; found by the ReDoS tests), and
# redaction runs on untrusted text. First, maximal dotted runs of base64url characters are
# matched (the look-behind means a run is only ever scanned from its first character);
# then each run is split on dots and checked without backtracking.
_DOTTED_RUN = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){2,}")
_JWT_MIN_SEGMENT = 5


def _jwt_start(segment: str) -> int:
    """Index of the first "eyJ" at a word boundary with a long enough header, else -1."""
    index = segment.find("eyJ")
    while index != -1:
        if index == 0 or segment[index - 1] == "-":
            return index if len(segment) - index - 3 >= _JWT_MIN_SEGMENT else -1
        index = segment.find("eyJ", index + 1)
    return -1


def _redact_dotted_run(match: re.Match[str]) -> str:
    segments = match.group(0).split(".")
    out: list[str] = []
    i = 0
    while i < len(segments):
        start = -1
        if (
            i + 2 < len(segments)
            and len(segments[i + 1]) >= _JWT_MIN_SEGMENT
            and len(segments[i + 2]) >= _JWT_MIN_SEGMENT
        ):
            start = _jwt_start(segments[i])
        if start < 0:
            out.append(segments[i])
            i += 1
        else:
            out.append(segments[i][:start] + REDACTED)
            i += 3
    return ".".join(out)


class Secret:
    """A string that never shows its value unless explicitly revealed."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("Secret value must be a string")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('**********')"

    __str__ = __repr__

    def __format__(self, format_spec: str) -> str:
        return repr(self)

    def __bool__(self) -> bool:
        return bool(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return hmac.compare_digest(self._value.encode(), other._value.encode())

    def __hash__(self) -> int:
        return id(self)

    def __reduce__(self) -> NoReturn:
        raise TypeError("Secret values cannot be pickled")


class SecretRedactor:
    """Thread-safe registry of secret strings that must never be emitted."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: set[str] = set()

    def register(self, value: "str | Secret") -> None:
        raw = value.reveal() if isinstance(value, Secret) else value
        if len(raw) < MIN_REGISTERED_SECRET_LENGTH:
            return  # too short to redact without mangling ordinary text
        with self._lock:
            self._values.add(raw)
            # PEM keys may appear line by line or with escaped newlines.
            for line in raw.splitlines():
                if len(line) >= 16 and "-----" not in line:
                    self._values.add(line)

    def forget(self, value: "str | Secret") -> None:
        raw = value.reveal() if isinstance(value, Secret) else value
        with self._lock:
            self._values.discard(raw)

    def redact(self, text: str) -> str:
        with self._lock:
            values = sorted(self._values, key=len, reverse=True)
        for value in values:
            if value in text:
                text = text.replace(value, REDACTED)
        text = _DOTTED_RUN.sub(_redact_dotted_run, text)
        for pattern in _PATTERNS:
            if pattern.groups:
                text = pattern.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
            else:
                text = pattern.sub(REDACTED, text)
        return text


_default_redactor = SecretRedactor()


def default_redactor() -> SecretRedactor:
    return _default_redactor


def register_secret(value: "str | Secret") -> None:
    _default_redactor.register(value)


def redact(text: str) -> str:
    """Remove registered secrets and credential-shaped strings from ``text``."""
    return _default_redactor.redact(text)
