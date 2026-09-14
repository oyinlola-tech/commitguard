"""Audit storage backends.

TODO(future): SQLite backend via the standard library ``sqlite3`` module
(no ORM dependency initially), stored outside the work tree by default so it is
never committed. PostgreSQL support later.
"""

from abc import ABC, abstractmethod

from commitguard.audit.models import AuditEvent


class AuditStorage(ABC):
    """Interface for append-only audit storage."""

    @abstractmethod
    def append(self, event: AuditEvent) -> None:
        """Persist ``event``. Implementations must never update or delete."""
