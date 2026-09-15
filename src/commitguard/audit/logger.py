"""Audit logger: structured log line + optional storage for every event."""

from collections.abc import Sequence

from commitguard.audit.models import AuditEvent
from commitguard.audit.storage import AuditStorage
from commitguard.observability.logging import get_logger

log = get_logger("commitguard.audit")


class AuditLogger:
    def __init__(self, storages: Sequence[AuditStorage] = ()) -> None:
        self._storages = tuple(storages)

    def record(self, event: AuditEvent) -> None:
        log.info(
            "audit",
            audit_type=event.type.value,
            audit_event_id=event.event_id,
            installation=event.installation_id,
            repository_id=event.repository_id,
            head_sha=event.head_sha,
            action=event.action.value if event.action else None,
            data=event.data,
        )
        for storage in self._storages:
            storage.append_audit_event(event)
