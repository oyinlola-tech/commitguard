"""Audit logger.

TODO(future): accept :class:`~commitguard.audit.models.AuditEvent` objects from
the CLI after a decision is made and forward them to a configured
:class:`~commitguard.audit.storage.AuditStorage`. Must be a no-op unless
auditing is explicitly enabled in configuration.
"""

from commitguard.audit.models import AuditEvent


class AuditLogger:
    """Records audit events. Not implemented yet."""

    def record(self, event: AuditEvent) -> None:
        raise NotImplementedError("audit logging is not implemented yet")
