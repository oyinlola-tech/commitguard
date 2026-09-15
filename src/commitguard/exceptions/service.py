"""Errors raised by long-running services (scan workers, integrations).

The split matters for enforcement: a *policy violation* is a successful scan
with a BLOCK result, while these errors mean the scan could not be completed.
Both fail the check, but they are recorded and reported differently.
"""

from commitguard.exceptions.base import CommitGuardError
from commitguard.exceptions.configuration import ConfigurationError


class PolicyError(ConfigurationError):
    """A policy (e.g. a mandatory organisation policy) is invalid or cannot be applied."""


class ScanError(CommitGuardError):
    """A scan could not be completed (missing commits, too many commits, stale scan...)."""


class StaleScanError(ScanError):
    """A newer scan owns the result; this scan must not publish anything."""


class InfrastructureError(CommitGuardError):
    """A dependency the service needs (Git, storage, network) failed."""
