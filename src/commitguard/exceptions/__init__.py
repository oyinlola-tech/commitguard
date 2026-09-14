"""CommitGuard exception hierarchy.

Every error raised deliberately by CommitGuard derives from
:class:`CommitGuardError`, so callers (in particular the CLI and Git hooks) can
distinguish expected failures from programming errors and fail closed.
"""

from commitguard.exceptions.base import CommitGuardError, UnsafeInputError
from commitguard.exceptions.configuration import ConfigurationError, RulesError
from commitguard.exceptions.detection import DetectionError, DetectorRegistrationError
from commitguard.exceptions.git import (
    GitCommandError,
    GitError,
    GitNotFoundError,
    MalformedGitOutputError,
    NotAGitRepositoryError,
)

__all__ = [
    "CommitGuardError",
    "ConfigurationError",
    "DetectionError",
    "DetectorRegistrationError",
    "GitCommandError",
    "GitError",
    "GitNotFoundError",
    "MalformedGitOutputError",
    "NotAGitRepositoryError",
    "RulesError",
    "UnsafeInputError",
]
