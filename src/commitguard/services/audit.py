"""AuditService: create audit events with correlation IDs and record them."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from commitguard.audit.logger import AuditLogger
from commitguard.audit.models import AuditEvent, AuditEventType, AuditValue
from commitguard.audit.storage import AuditStorage
from commitguard.core.decision import Action
from commitguard.observability.logging import current_correlation


class AuditService:
    def __init__(
        self,
        storages: Sequence[AuditStorage] = (),
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._logger = AuditLogger(storages)
        self._now = now

    def record(
        self,
        event_type: AuditEventType,
        *,
        installation_id: int | None = None,
        repository_id: int | None = None,
        repository: str | None = None,
        head_sha: str | None = None,
        action: Action | None = None,
        **data: AuditValue,
    ) -> AuditEvent:
        correlation = current_correlation()

        def _str(key: str) -> str | None:
            value = correlation.get(key)
            return str(value) if value is not None else None

        event = AuditEvent(
            type=event_type,
            occurred_at=self._now(),
            delivery_id=_str("delivery_id"),
            job_id=_str("job_id"),
            scan_id=_str("scan_id"),
            installation_id=installation_id,
            repository_id=repository_id,
            repository=repository,
            head_sha=head_sha,
            action=action,
            data=data,
        )
        self._logger.record(event)
        return event
