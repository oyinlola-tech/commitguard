"""Validation of untrusted identifiers before they are used.

The most important rule here: a user- or metadata-supplied Git revision must
never be interpreted as a command-line option (``--output=/etc/passwd``).
"""

import re

from commitguard.exceptions.base import UnsafeInputError

MAX_REVISION_LENGTH = 256

_SHA_RE = re.compile(r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_IDENTIFIER_RE = re.compile(r"\A[a-z][a-z0-9_]{0,63}\Z")
# section[.subsection].key - subsections are restricted here to keep it simple.
_GIT_CONFIG_KEY_RE = re.compile(r"\A[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9_./-]+)?\.[A-Za-z][A-Za-z0-9-]*\Z")


def validate_revision(revision: str) -> str:
    """Validate a Git revision expression supplied by a user or hook.

    Rejects empty values, option-like values, control characters and overly
    long input. This is defence in depth: the Git wrapper additionally passes
    ``--end-of-options`` before revisions.
    """
    if not revision:
        raise UnsafeInputError("revision must not be empty")
    if len(revision) > MAX_REVISION_LENGTH:
        raise UnsafeInputError("revision is too long")
    if revision.startswith("-"):
        raise UnsafeInputError("revision must not start with '-'")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in revision):
        raise UnsafeInputError("revision must not contain control characters")
    return revision


def is_git_sha(value: str) -> bool:
    """Return True if ``value`` is a full lowercase SHA-1 or SHA-256 object ID."""
    return bool(_SHA_RE.match(value))


def validate_git_sha(value: str) -> str:
    """Validate a full Git object ID (SHA-1 or SHA-256)."""
    if not is_git_sha(value):
        raise UnsafeInputError("value is not a full Git object id")
    return value


def validate_identifier(value: str, *, kind: str = "identifier") -> str:
    """Validate an internal identifier (detector name, rule ID, policy ID).

    Identifiers are lowercase snake_case, start with a letter, and are at most
    64 characters, so they are safe to use as YAML keys, log fields and IDs.
    """
    if not _IDENTIFIER_RE.match(value):
        raise UnsafeInputError(
            f"invalid {kind} {value!r}: expected lowercase snake_case (max 64 chars)"
        )
    return value


def validate_git_config_key(key: str) -> str:
    """Validate a Git configuration key such as ``core.hooksPath``."""
    if len(key) > MAX_REVISION_LENGTH or not _GIT_CONFIG_KEY_RE.match(key):
        raise UnsafeInputError(f"invalid git config key {key!r}")
    return key
