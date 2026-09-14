"""Deterministic hashing helpers.

Used to derive stable identifiers (e.g. finding fingerprints) so the same
violation on the same commit always produces the same ID for audit purposes.
"""

import hashlib
from collections.abc import Iterable

_FIELD_SEPARATOR = b"\x1f"  # ASCII unit separator: cannot be confused with text


def sha256_hex(data: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def fingerprint(parts: Iterable[str]) -> str:
    """Return a stable SHA-256 fingerprint over an ordered sequence of strings.

    Each part is length-prefixed, so ``["ab", "c"]`` and ``["a", "bc"]`` never
    collide even if a part contains the separator byte.
    """
    digest = hashlib.sha256()
    for part in parts:
        encoded = part.encode("utf-8", errors="surrogatepass")
        digest.update(str(len(encoded)).encode("ascii"))
        digest.update(_FIELD_SEPARATOR)
        digest.update(encoded)
    return digest.hexdigest()
