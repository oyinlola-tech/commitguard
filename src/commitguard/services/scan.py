"""ScanService: one entry point for "analyse these commits under this policy".

Every server-side adapter (``commitguard ci github`` in GitHub Actions and the
GitHub App worker) builds a :class:`ScanRequest` and gets a :class:`ScanResult`
containing the same :class:`~commitguard.services.reports.ScanReport` that
``commitguard scan --format json`` produces - there is no platform-specific
result format and no platform-specific detection.

A scan is deterministic: the same commits, trusted policy, mandatory policy,
rules and CommitGuard version produce the same result and the same
:attr:`ScanMetadata.scan_id`, which makes retries, duplicate webhooks and
Action/App comparisons checkable.
"""

from pydantic import BaseModel, ConfigDict, Field

from commitguard import __version__
from commitguard.ci.context import CIContext
from commitguard.config.sources import MandatoryPolicy
from commitguard.core.decision import Action
from commitguard.git.repository import Repository
from commitguard.policies.governance import EffectivePolicy, GovernanceInputs
from commitguard.policies.model import Policy
from commitguard.rules.loader import builtin_rules_fingerprint
from commitguard.rules.matcher import CompiledRules
from commitguard.security.hashing import fingerprint
from commitguard.security.validation import validate_repository_path
from commitguard.services.ci import DEFAULT_CI_MAX_COMMITS, CIPlan, execute_ci_plan, plan_ci
from commitguard.services.enforcement import EnforcementDecision, EnforcementService
from commitguard.services.reports import ScanReport

SCAN_ID_LENGTH = 32


class ScanRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    repository: Repository
    context: CIContext
    config_path: str | None = None
    max_commits: int = Field(default=DEFAULT_CI_MAX_COMMITS, ge=1)
    fail_on: Action = Action.BLOCK
    mandatory_policy: MandatoryPolicy | None = None
    #: Organization governance (GitHub App); replaces ``mandatory_policy`` when set.
    governance: GovernanceInputs | None = None
    rules: CompiledRules | None = None  # None: the bundled, trusted rules
    #: Version label of ``rules`` when they are not the bundled rules.
    rules_version: str | None = None

    def model_post_init(self, __context: object) -> None:
        if self.config_path is not None:
            validate_repository_path(self.config_path)
        if self.fail_on is Action.ALLOW:
            raise ValueError("fail_on must be block or warn")


class ScanStatistics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    commits_scanned: int
    violations: int  # commits whose result is BLOCK
    warnings: int  # commits whose result is WARN
    allowed: int
    findings: int
    detector_failures: int


class ScanMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scan_id: str
    tool_version: str
    rules_version: str
    policy_version: str
    policy_source: str
    provider: str
    event: str
    base_sha: str | None
    head_sha: str | None


class ScanResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report: ScanReport
    plan: CIPlan
    statistics: ScanStatistics
    metadata: ScanMetadata
    enforcement: EnforcementDecision
    policies: tuple[Policy, ...] = ()  # effective policies, for reproducible history
    repository_policies: tuple[Policy, ...] = ()  # before organization governance
    effective: EffectivePolicy | None = None

    @property
    def action(self) -> Action:
        return self.report.action


def statistics_for(report: ScanReport) -> ScanStatistics:
    commits = report.commits
    return ScanStatistics(
        commits_scanned=len(commits),
        violations=sum(1 for c in commits if c.action is Action.BLOCK),
        warnings=sum(1 for c in commits if c.action is Action.WARN),
        allowed=sum(1 for c in commits if c.action is Action.ALLOW),
        findings=sum(len(c.findings) for c in commits),
        detector_failures=sum(len(c.failures) for c in commits),
    )


class ScanService:
    def plan(self, request: ScanRequest) -> CIPlan:
        return plan_ci(
            request.repository,
            request.context,
            config_path=request.config_path,
            max_commits=request.max_commits,
        )

    def execute(self, request: ScanRequest, plan: CIPlan) -> ScanResult:
        run = execute_ci_plan(
            request.repository,
            request.context,
            plan,
            rules=request.rules,
            mandatory=request.mandatory_policy,
            governance=request.governance,
        )
        if request.rules is None:
            rules_version = builtin_rules_fingerprint()
        else:
            rules_version = request.rules_version or "custom"
        context = request.context
        scan_id = fingerprint(
            [
                __version__,
                rules_version,
                run.policy_fingerprint,
                context.provider.value,
                context.event.value,
                context.repository or "",
                str(context.pull_request_number or ""),
                plan.range.base or "",
                plan.range.head or "",
                ",".join(plan.range.exclude),
            ]
        )[:SCAN_ID_LENGTH]
        metadata = ScanMetadata(
            scan_id=scan_id,
            tool_version=__version__,
            rules_version=rules_version,
            policy_version=run.policy_fingerprint,
            policy_source=run.report.ci.policy_source if run.report.ci else "",
            provider=context.provider.value,
            event=context.event_name,
            base_sha=plan.range.base,
            head_sha=plan.range.head,
        )
        return ScanResult(
            report=run.report,
            plan=plan,
            statistics=statistics_for(run.report),
            metadata=metadata,
            enforcement=EnforcementService(request.fail_on).decide(run.report),
            policies=run.policies,
            repository_policies=run.repository_policies,
            effective=run.effective,
        )

    def run(self, request: ScanRequest) -> ScanResult:
        return self.execute(request, self.plan(request))
