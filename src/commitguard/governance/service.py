"""Wiring of the governance services and their background work."""

from collections.abc import Callable
from datetime import UTC, datetime

from commitguard.controlplane.policies import OrganizationPolicyService
from commitguard.github.storage import SqliteStateStore
from commitguard.github.worker import ScanGovernance
from commitguard.governance.exceptions import PolicyExceptionService
from commitguard.governance.groups import RepositoryGroupService
from commitguard.governance.inventory import RepositoryInventory
from commitguard.governance.resolver import GovernanceResolver, scan_governance_record
from commitguard.governance.rules import OrganizationRuleService
from commitguard.governance.settings import OrganizationSettingsService
from commitguard.observability.logging import get_logger
from commitguard.services.audit import AuditService

log = get_logger(__name__)


class GovernanceServices:
    def __init__(
        self,
        store: SqliteStateStore,
        audit: AuditService,
        policies: OrganizationPolicyService,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.policies = policies
        self.settings = OrganizationSettingsService(store, audit, now=now)
        self.inventory = RepositoryInventory(store, audit, now=now)
        self.groups = RepositoryGroupService(store, audit, now=now)
        self.exceptions = PolicyExceptionService(store, audit, now=now)
        self.resolver = GovernanceResolver(store, policies, audit, now=now)
        self.rules = OrganizationRuleService(store, audit, now=now)
        self.settings.add_hook(self.resolver.on_settings_saved)
        self._now = now

    def scan_governance(self, installation_id: int, repository_id: int) -> ScanGovernance | None:
        resolved = self.resolver.for_scan(installation_id, repository_id)
        if resolved is None:
            return None
        rules, rules_version = self.rules.compiled(resolved.account_id)
        return ScanGovernance(
            inputs=resolved.inputs,
            organization_policy_version=resolved.versions.organization_policy,
            fingerprint=resolved.fingerprint,
            record=lambda effective: scan_governance_record(resolved, effective),
            rules=rules,
            rules_version=rules_version if rules is not None else None,
        )

    def run_maintenance(self) -> dict[str, int]:
        """One pass of governance background work. Each task is isolated."""
        results: dict[str, int] = {}
        tasks: dict[str, Callable[[], object]] = {
            "exceptions_expired": self.exceptions.expire_due,
            "exception_warnings": self.exceptions.warn_expiring,
            "propagation": lambda: self.resolver.propagate().get("resolved", 0),
        }
        for name, task in tasks.items():
            try:
                value = task()
                results[name] = int(value) if isinstance(value, int) else 0
            except Exception as exc:  # noqa: BLE001 - keep other tasks running
                log.error("governance_task_failed", task=name, error_type=type(exc).__name__)
                results[name] = -1
        return results
