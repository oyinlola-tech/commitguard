"""Configuration errors.

Invalid configuration is always an error, never a warning: a typo in a policy
file must not silently weaken enforcement.
"""

from pathlib import Path

from commitguard.exceptions.base import CommitGuardError


class ConfigurationError(CommitGuardError):
    """Raised when a configuration file is missing, unreadable, or invalid."""

    def __init__(self, message: str, *, path: Path | None = None) -> None:
        self.path = path
        prefix = f"{path}: " if path is not None else ""
        super().__init__(f"{prefix}{message}")


class RulesError(ConfigurationError):
    """Raised when detection rule files are missing or invalid."""
