"""AuditService: create audit events with correlation IDs and record them."""

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime

from commitguard.audit.logger import AuditLogger
from commitguard.audit.models import SYSTEM_ACTOR, Actor, AuditEvent, AuditEventType, AuditValue
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
        actor: Actor = SYSTEM_ACTOR,
        account_id: int | None = None,
        installation_id: int | None = None,
        repository_id: int | None = None,
        repository: str | None = None,
        head_sha: str | None = None,
        action: Action | None = None,
        extra: Mapping[str, AuditValue] | None = None,
        **data: AuditValue,
    ) -> AuditEvent:
        """Record an event. ``extra`` carries data built elsewhere (merged with ``data``)."""
        event = self.build(
            event_type,
            actor=actor,
            account_id=account_id,
            installation_id=installation_id,
            repository_id=repository_id,
            repository=repository,
            head_sha=head_sha,
            action=action,
            **{**(extra or {}), **data},
        )
        self._logger.record(event)
        return event

    def build(
        self,
        event_type: AuditEventType,
        *,
        actor: Actor = SYSTEM_ACTOR,
        account_id: int | None = None,
        installation_id: int | None = None,
        repository_id: int | None = None,
        repository: str | None = None,
        head_sha: str | None = None,
        action: Action | None = None,
        **data: AuditValue,
    ) -> AuditEvent:
        """Create an event without storing it (for writes inside a larger transaction)."""
        correlation = current_correlation()

        def _str(key: str) -> str | None:
            value = correlation.get(key)
            return str(value) if value is not None else None

        return AuditEvent(
            type=event_type,
            occurred_at=self._now(),
            delivery_id=_str("delivery_id"),
            job_id=_str("job_id"),
            scan_id=_str("scan_id"),
            actor_type=actor.type,
            actor_id=actor.id,
            actor_login=actor.login,
            account_id=account_id,
            installation_id=installation_id,
            repository_id=repository_id,
            repository=repository,
            head_sha=head_sha,
            action=action,
            data=data,
        )

    def log_stored(self, event: AuditEvent) -> None:
        """Emit the log line for an event another component stored transactionally."""
        self._logger.log(event)
