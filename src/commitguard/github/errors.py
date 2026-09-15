"""Errors of the GitHub App integration.

Messages are safe to log and to show in a Check Run: they never contain
tokens, JWTs, the private key, the webhook secret or request headers, and
GitHub response text is sanitised, redacted and truncated before it is used.
"""

from enum import StrEnum

from commitguard.exceptions.base import CommitGuardError
from commitguard.exceptions.configuration import ConfigurationError
from commitguard.security.sanitization import sanitize_for_terminal
from commitguard.security.secrets import redact


def safe_text(text: str, limit: int = 300) -> str:
    """Untrusted or credential-adjacent text, made safe for messages."""
    return sanitize_for_terminal(redact(text), max_length=limit)


class GitHubAppError(CommitGuardError):
    """Base class for GitHub App integration errors."""


class AppConfigurationError(ConfigurationError):
    """The App's environment configuration is missing or invalid (names only, never values)."""


class AuthenticationError(GitHubAppError):
    """App credentials are invalid, or GitHub rejected the App / installation token."""


class AuthorizationError(GitHubAppError):
    """The installation is not allowed to access the repository (or no longer exists)."""


class InsufficientPermissionsError(AuthorizationError):
    def __init__(self, missing: dict[str, str]) -> None:
        self.missing = dict(sorted(missing.items()))
        wanted = ", ".join(f"{name}: {level}" for name, level in self.missing.items())
        super().__init__(f"GitHub App installation is missing required permissions ({wanted})")


class WebhookValidationError(GitHubAppError):
    """A webhook delivery was rejected. ``status`` is the HTTP status to return."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        self.status = status
        super().__init__(message)


class GitHubErrorCategory(StrEnum):
    UNAUTHORIZED = "unauthorized"  # 401
    FORBIDDEN = "forbidden"  # 403 (not rate limiting)
    NOT_FOUND = "not_found"  # 404
    CONFLICT = "conflict"  # 409
    VALIDATION = "validation"  # 422
    RATE_LIMITED = "rate_limited"  # 429 / 403 rate limit
    SERVER_ERROR = "server_error"  # 5xx
    UNAVAILABLE = "unavailable"  # timeout / network failure
    UNEXPECTED = "unexpected"  # redirects, malformed responses, other statuses


class GitHubAPIError(GitHubAppError):
    """A GitHub REST API call failed after bounded retries."""

    category = GitHubErrorCategory.UNEXPECTED

    def __init__(
        self,
        operation: str,
        *,
        status: int | None = None,
        detail: str = "",
        request_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.status = status
        self.request_id = safe_text(request_id, 80) if request_id else None
        self.detail = safe_text(detail) if detail else ""
        status_text = f"HTTP {status}" if status is not None else "no response"
        message = f"GitHub API {operation} failed: {status_text} ({self.category.value})"
        if self.detail:
            message += f": {self.detail}"
        super().__init__(message)


class GitHubUnauthorizedError(GitHubAPIError, AuthenticationError):
    category = GitHubErrorCategory.UNAUTHORIZED


class GitHubForbiddenError(GitHubAPIError, AuthorizationError):
    category = GitHubErrorCategory.FORBIDDEN


class GitHubNotFoundError(GitHubAPIError):
    category = GitHubErrorCategory.NOT_FOUND


class GitHubConflictError(GitHubAPIError):
    category = GitHubErrorCategory.CONFLICT


class GitHubValidationError(GitHubAPIError):
    category = GitHubErrorCategory.VALIDATION


class GitHubRateLimitError(GitHubAPIError):
    category = GitHubErrorCategory.RATE_LIMITED

    def __init__(
        self,
        operation: str,
        *,
        retry_after: float | None = None,
        status: int | None = None,
        detail: str = "",
        request_id: str | None = None,
    ) -> None:
        self.retry_after = retry_after
        super().__init__(operation, status=status, detail=detail, request_id=request_id)


class GitHubServerError(GitHubAPIError):
    category = GitHubErrorCategory.SERVER_ERROR


class GitHubUnavailableError(GitHubAPIError):
    category = GitHubErrorCategory.UNAVAILABLE
