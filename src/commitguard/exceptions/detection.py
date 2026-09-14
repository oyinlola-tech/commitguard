"""Detection errors."""

from commitguard.exceptions.base import CommitGuardError


class DetectionError(CommitGuardError):
    """Raised when a detector misbehaves (e.g. emits an undeclared rule)."""


class DetectorRegistrationError(DetectionError):
    """Raised when a detector cannot be registered (duplicate or invalid name)."""
