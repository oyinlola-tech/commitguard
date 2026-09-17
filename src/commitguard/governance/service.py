"""Wiring of the governance services and their background work.

Background work (run by the GitHub App's maintenance loop, every minute; each
task is isolated so one failure never stops the others):

=========================  ==============================================================
Task                       Work
=========================  ==============================================================
exception expiry           ACTIVE -> EXPIRED past ``expires_at``; audit, notification,
                           invalidation
exception warnings         one notification per configured threshold before expiry
propagation                resolve stale / never-resolved effective policies (bounded)
rollout safety             pause (or, when configured, roll back) rollouts over threshold
simulations                run queued policy simulations (read-only, leased)
bulk operations            process pending items in bounded batches (leased)
scan schedules             start due runs, queue scans within the per-tick budget
metrics snapshots          daily organization metric snapshot (at most hourly refresh)
=========================  ==============================================================
"""

from collections.abc import Callable
from datetime import UTC, datetime

from commitguard.controlplane.policies import OrganizationPolicyService
from commitguard.controlplane.queries import DashboardQueries
from commitguard.github.client import GitHubClient
from commitguard.github.installations import InstallationService
from commitguard.github.storage import SqliteStateStore
from commitguard.github.worker import ScanGovernance
from commitguard.governance.bulk import BulkOperationService
from commitguard.governance.exceptions import PolicyExceptionService
from commitguard.governance.groups import RepositoryGroupService
from commitguard.governance.inventory import RepositoryInventory
from commitguard.governance.posture import SecurityPostureService
from commitguard.governance.resolver import GovernanceResolver, scan_governance_record
from commitguard.governance.rollouts import PolicyRolloutService
from commitguard.governance.rules import OrganizationRuleService
from commitguard.governance.schedules import DefaultBranchScanner, ScanScheduleService
from commitguard.governance.settings import OrganizationSettingsService
from commitguard.governance.simulation import PolicySimulationService
from commitguard.governance.workflow import PolicyWorkflowService
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
        installations: InstallationService | None = None,
        client: GitHubClient | None = None,
        enqueue: Callable[[str], bool] | None = None,
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
        self.workflow = PolicyWorkflowService(store, audit, policies, now=now)
        self.rollouts = PolicyRolloutService(store, audit, policies, now=now)
        self.simulations = PolicySimulationService(store, audit, self.resolver, now=now)
        self.scanner = (
            DefaultBranchScanner(store, installations, client, self.resolver, enqueue, now=now)
            if installations is not None and client is not None and enqueue is not None
            else None
        )
        self.schedules = ScanScheduleService(store, audit, self.scanner, now=now)
        self.bulk = BulkOperationService(
            store, audit, self.groups, self.inventory, self.scanner, now=now
        )
        self.posture = SecurityPostureService(
            store, audit, DashboardQueries(store, now=now), now=now
        )
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
            "rollouts_paused": self.rollouts.evaluate,
            "propagation": lambda: self.resolver.propagate().get("resolved", 0),
            "simulations": self.simulations.run_pending,
            "bulk_items": self.bulk.run_pending,
            "scheduled_scans": lambda: self.schedules.run_due().get("queued", 0),
            "metric_snapshots": self.posture.snapshot_metrics,
        }
        for name, task in tasks.items():
            try:
                value = task()
                results[name] = int(value) if isinstance(value, int) else 0
            except Exception as exc:  # noqa: BLE001 - keep other tasks running
                log.error("governance_task_failed", task=name, error_type=type(exc).__name__)
                results[name] = -1
        return results
