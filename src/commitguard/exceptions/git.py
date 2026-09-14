"""Git integration errors."""

from collections.abc import Sequence

from commitguard.exceptions.base import CommitGuardError


class GitError(CommitGuardError):
    """Base class for Git-related failures."""


class GitNotFoundError(GitError):
    """Raised when the ``git`` executable cannot be located."""


class NotAGitRepositoryError(GitError):
    """Raised when an operation requires a Git repository and none was found."""


class MalformedGitOutputError(GitError):
    """Raised when Git output does not match the expected, validated shape.

    Commit metadata is untrusted; a crafted object can produce output that a
    naive parser would mis-assign. We refuse to guess.
    """


class GitCommandError(GitError):
    """Raised when a Git command exits with a non-zero status."""

    def __init__(self, args: Sequence[str], returncode: int, stderr: str) -> None:
        self.command = tuple(args)
        self.returncode = returncode
        self.stderr = stderr
        # Only the git sub-command is echoed; arguments may contain user data.
        subcommand = next((a for a in self.command[1:] if not a.startswith("-")), "?")
        super().__init__(f"git {subcommand} failed with exit code {returncode}: {stderr}")


class HookInstallError(GitError):
    """Raised when hooks cannot be installed or removed safely."""
