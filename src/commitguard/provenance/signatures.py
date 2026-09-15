"""Commit signature provenance.

TODO(phase-5): read signature status via Git (``%G?``, ``%GK``, ``%GS``) for
GPG, SSH and X.509 signatures, and let policies require verified signatures.
Verification depends on the local keyring / allowed-signers configuration,
which is itself a trust decision and must be explicit in configuration.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class SignatureStatus(StrEnum):
    """Signature state of a commit, mirroring Git's ``%G?`` codes."""

    GOOD = "good"  # G
    BAD = "bad"  # B
    UNKNOWN_VALIDITY = "unknown_validity"  # U
    EXPIRED = "expired"  # X
    EXPIRED_KEY = "expired_key"  # Y
    REVOKED_KEY = "revoked_key"  # R
    CANNOT_CHECK = "cannot_check"  # E
    UNSIGNED = "unsigned"  # N


class SignatureInfo(BaseModel):
    """Signature metadata for a commit. Not populated yet (security intelligence phase)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: SignatureStatus
    key_id: str | None = None
    signer: str | None = None
