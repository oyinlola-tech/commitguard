"""Control plane errors. The API layer maps each to one HTTP status and error code.

Messages are written for the person using the dashboard and never contain
secrets, SQL, stack traces or data from another tenant.
"""

from commitguard.exceptions.base import CommitGuardError


class ControlPlaneError(CommitGuardError):
    code = "INTERNAL_ERROR"
    status = 500


class NotFoundError(ControlPlaneError):
    """Missing, or belongs to a tenant the caller cannot see (never distinguished)."""

    code = "NOT_FOUND"
    status = 404

    def __init__(self, message: str = "The requested resource was not found.") -> None:
        super().__init__(message)


class PermissionDeniedError(ControlPlaneError):
    """The caller can see the resource but their role does not allow the operation."""

    code = "FORBIDDEN"
    status = 403

    def __init__(self, message: str = "You do not have permission to perform this action.") -> None:
        super().__init__(message)


class InputValidationError(ControlPlaneError):
    code = "VALIDATION_ERROR"
    status = 400

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class ConflictError(ControlPlaneError):
    code = "CONFLICT"
    status = 409


class ConfirmationRequiredError(ControlPlaneError):
    """A change weakens enforcement and was not explicitly confirmed."""

    code = "CONFIRMATION_REQUIRED"
    status = 409


class ReauthenticationRequiredError(ControlPlaneError):
    code = "REAUTHENTICATION_REQUIRED"
    status = 401

    def __init__(
        self, message: str = "Sign in again to confirm your identity before this change."
    ) -> None:
        super().__init__(message)


class UpstreamUnavailableError(ControlPlaneError):
    """GitHub could not be reached or refused the request."""

    code = "GITHUB_UNAVAILABLE"
    status = 502
