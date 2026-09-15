"""Audit storage interface.

Storage is append-only for callers; the only deletion is retention purging.
Reads are always scoped to a tenant (installation, optionally repository), so a
future dashboard or API cannot list another organisation's events by mistake.
The GitHub App's SQLite state store implements this interface; a PostgreSQL
implementation can replace it without touching the services.
"""

import threading
from abc import ABC, abstractmethod
from datetime import datetime

from commitguard.audit.models import AuditEvent


class AuditStorage(ABC):
    @abstractmethod
    def append_audit_event(self, event: AuditEvent) -> None:
        """Persist ``event``. Existing events are never updated."""

    @abstractmethod
    def list_audit_events(
        self, *, installation_id: int, repository_id: int | None = None, limit: int = 100
    ) -> list[AuditEvent]:
        """Most recent events for one tenant, newest first."""

    @abstractmethod
    def purge_audit_events(self, before: datetime) -> int:
        """Delete events older than ``before`` (retention). Returns the count removed."""


class InMemoryAuditStorage(AuditStorage):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[AuditEvent] = []

    def append_audit_event(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def list_audit_events(
        self, *, installation_id: int, repository_id: int | None = None, limit: int = 100
    ) -> list[AuditEvent]:
        with self._lock:
            matching = [
                e
                for e in self._events
                if e.installation_id == installation_id
                and (repository_id is None or e.repository_id == repository_id)
            ]
        return list(reversed(matching))[:limit]

    def purge_audit_events(self, before: datetime) -> int:
        with self._lock:
            kept = [e for e in self._events if e.occurred_at >= before]
            removed = len(self._events) - len(kept)
            self._events = kept
        return removed
