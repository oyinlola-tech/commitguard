"""Root exception types shared by every CommitGuard layer."""


class CommitGuardError(Exception):
    """Base class for all CommitGuard errors."""


class UnsafeInputError(CommitGuardError, ValueError):
    """Raised when untrusted input fails a security validation check."""
