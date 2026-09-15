"""ScanWorker: turns a stored scan job into a GitHub Check Run.

::

    claim job -> authorize installation + repository (down-scoped token, ID lookup)
      -> newest event for this pull request, PR still open? (else: cancelled, no check)
      -> claim the check slot (repository, SHA, name)   (a newer job wins; older jobs stop)
      -> create/refresh Check Run "queued"
      -> fetch commit metadata into the mirror          (no checkout, no code execution)
      -> plan commit range + trusted policy             (same code as the GitHub Action)
      -> verify planned head == check SHA               (TOCTOU guard)
      -> Check Run "in_progress" -> scan -> Check Run "completed"
      -> job state + audit events + metrics

Stale writes: every Check Run write happens under a per-slot lock after
verifying that this job still owns the slot. A job for the same commit that
started later takes ownership, so an older, slower scan can never overwrite a
newer result. Check Runs are always created with the exact SHA that was
scanned, so a result can never be attached to a different commit.

Failures are classified - authorization, configuration, infrastructure,
timeout, internal - recorded as job state ``error`` (distinct from a policy
``failed``) and, whenever a Check Run exists, published as a failing check:
CommitGuard never reports success for a commit it could not evaluate.

Executions: a job is one *execution* of a logical scan. Re-runs (GitHub's
"Re-run"), manual scans and automatic retries are new executions of the same
scan that publish to the same (repository, SHA, check name) slot; earlier
executions stay stored unchanged. Merge group jobs scan ``base..merge group``
and publish to the merge group commit, which is the SHA the merge queue waits
on; a merge group GitHub has already destroyed is not scanned.
"""

import sqlite3
import threading
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from commitguard.audit.models import AuditEventType
from commitguard.ci.context import CIEventKind
from commitguard.config.sources import MandatoryPolicy
from commitguard.controlplane.results import ScanResultRecorder, merge_queue_failure
from commitguard.core.decision import Action
from commitguard.core.result import Severity
from commitguard.exceptions.base import CommitGuardError
from commitguard.exceptions.configuration import ConfigurationError
from commitguard.exceptions.git import GitError
from commitguard.exceptions.service import InfrastructureError, ScanError, StaleScanError
from commitguard.git.repository import Repository
from commitguard.github.check_runs import (
    completed_output,
    error_output,
    in_progress_output,
    queued_output,
)
from commitguard.github.checks import (
    CheckRunConclusion,
    CheckRunOutput,
    CheckRunStatus,
    check_run_create_payload,
    check_run_update_payload,
)
from commitguard.github.client import GitHubClient
from commitguard.github.errors import (
    AuthenticationError,
    AuthorizationError,
    GitHubAPIError,
    safe_text,
)
from commitguard.github.installations import AuthorizedRepository, InstallationService
from commitguard.github.repositories import FetchTimeoutError, MirrorManager
from commitguard.github.storage import (
    JobState,
    MergeGroupState,
    ScanJob,
    ScanTrigger,
    SqliteStateStore,
)
from commitguard.notifications.deduplication import domain_key
from commitguard.notifications.models import NotificationEvent, NotificationType
from commitguard.notifications.outbox import account_for_installation, emit
from commitguard.observability.logging import correlation, get_logger
from commitguard.observability.metrics import (
    MERGE_GROUPS_FAILED,
    MERGE_GROUPS_SCANNED,
    POLICY_VIOLATIONS,
    SCANS_CANCELLED,
    SCANS_COMPLETED,
    SCANS_FAILED,
    SCANS_STARTED,
    Metrics,
)
from commitguard.services.audit import AuditService
from commitguard.services.ci import DEFAULT_CI_MAX_COMMITS
from commitguard.services.enforcement import FailureKind
from commitguard.services.scan import ScanRequest, ScanResult, ScanService

log = get_logger(__name__)

JOB_LEASE_SECONDS = 1800.0
MAX_JOB_ATTEMPTS = 3
_LOCK_STRIPES = 64
#: Cancellation reasons meaning a newer execution superseded this one (result ``stale``).
SUPERSEDED_NEWER_EVENT = "a newer event for this pull request is waiting to be scanned"
SUPERSEDED_CHECK_OWNER = "a newer scan owns the check for this commit"
MERGE_GROUP_GONE = "the merge queue no longer waits for this merge group"
_SUPERSEDED = frozenset({SUPERSEDED_NEWER_EVENT, SUPERSEDED_CHECK_OWNER, MERGE_GROUP_GONE})


type PolicyResolver = Callable[[int], tuple[MandatoryPolicy | None, int | None]]


@dataclass
class _Progress:
    auth: AuthorizedRepository | None = None
    check_run_id: int | None = None


class ScanWorker:
    def __init__(
        self,
        *,
        store: SqliteStateStore,
        installations: InstallationService,
        client: GitHubClient,
        mirrors: MirrorManager,
        audit: AuditService,
        metrics: Metrics,
        scan_service: ScanService | None = None,
        mandatory_policy: MandatoryPolicy | None = None,
        policy_resolver: PolicyResolver | None = None,
        recorder: ScanResultRecorder | None = None,
        max_commits: int = DEFAULT_CI_MAX_COMMITS,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._installations = installations
        self._client = client
        self._mirrors = mirrors
        self._audit = audit
        self._metrics = metrics
        self._scans = scan_service or ScanService()
        # The floor for an installation: service policy + its organisation's policy.
        self._policy_resolver: PolicyResolver = policy_resolver or (
            lambda _installation_id: (mandatory_policy, None)
        )
        self._recorder = recorder or ScanResultRecorder(store, audit, now=now)
        self._max_commits = max_commits
        self._now = now
        self._locks = tuple(threading.Lock() for _ in range(_LOCK_STRIPES))

    # ------------------------------------------------------------------ #
    def process(self, job_id: str) -> JobState | None:
        """Run one job. Returns its final state, or None if it was not claimable."""
        outcome = self._store.claim_job_outcome(
            job_id, self._now(), JOB_LEASE_SECONDS, MAX_JOB_ATTEMPTS
        )
        if outcome.exhausted is not None:
            self._exhausted(outcome.exhausted)
            return None
        job = outcome.job
        if job is None:
            return None
        with correlation(
            job_id=job.job_id,
            delivery_id=job.delivery_id,
            installation_id=job.installation_id,
            repository=job.repository.full_name,
        ):
            self._metrics.increment(SCANS_STARTED)
            progress = _Progress()
            try:
                return self._run(job, progress)
            except StaleScanError as exc:
                return self._cancel(job, str(exc))
            except (AuthorizationError, AuthenticationError) as exc:
                return self._fail(job, progress, FailureKind.AUTHORIZATION, exc)
            except ConfigurationError as exc:
                return self._fail(job, progress, FailureKind.CONFIGURATION, exc)
            except FetchTimeoutError as exc:
                return self._fail(job, progress, FailureKind.TIMEOUT, exc)
            except (InfrastructureError, GitHubAPIError, GitError, ScanError) as exc:
                return self._fail(job, progress, FailureKind.INFRASTRUCTURE, exc)
            except Exception as exc:  # noqa: BLE001 - any failure must fail closed
                return self._fail(job, progress, FailureKind.INTERNAL, exc)

    # ------------------------------------------------------------------ #
    def _slot(self, job: ScanJob, repository_id: int) -> tuple[int, int, str, str]:
        return (job.installation_id, repository_id, job.head_sha, job.check_name)

    def _lock_for(self, slot: tuple[int, int, str, str]) -> threading.Lock:
        index = zlib.crc32(repr(slot).encode("utf-8")) % _LOCK_STRIPES
        return self._locks[index]

    def _run(self, job: ScanJob, progress: _Progress) -> JobState:
        if not self._store.monitoring_enabled(job.installation_id, job.repository.id):
            raise StaleScanError("CommitGuard monitoring is paused for this repository")
        if job.event == "merge_group":
            group = self._store.get_merge_group(
                job.installation_id, job.repository.id, job.head_sha
            )
            if group is None or group.state is MergeGroupState.DESTROYED:
                raise StaleScanError(MERGE_GROUP_GONE)
        if job.trigger in (ScanTrigger.MANUAL, ScanTrigger.RERUN, ScanTrigger.RETRY):
            self._audit_job(
                AuditEventType.SCAN_STARTED,
                job,
                trigger=job.trigger.value,
                execution=job.execution,
                requested_by=job.requested_by,
            )
        auth = self._installations.authorize(job.installation_id, job.repository)
        progress.auth = auth
        repository = auth.repository
        token = auth.token.token

        if job.pull_request_number is not None:
            latest = self._store.latest_group_sequence(
                job.installation_id, repository.id, job.group_key
            )
            if latest > job.sequence:
                raise StaleScanError(SUPERSEDED_NEWER_EVENT)
            pull_request = self._client.get_pull_request(token, repository, job.pull_request_number)
            if pull_request.state != "open":
                raise StaleScanError("the pull request is no longer open")
            if pull_request.head.sha != job.head_sha:
                # The API can lag behind webhooks, so this alone does not cancel the scan:
                # a newer head arrives as its own event, and this result is attached to
                # exactly the commit that was scanned.
                log.info("pull_request_head_differs", commit=job.head_sha[:12])

        slot = self._slot(job, repository.id)
        with self._lock_for(slot):
            claim = self._store.claim_check(*slot, job.sequence, self._now())
            if not claim.owned:
                raise StaleScanError(SUPERSEDED_CHECK_OWNER)
            queued = queued_output(self._describe(job))
            if claim.check_run_id is None:
                created = self._client.create_check_run(
                    token,
                    repository,
                    check_run_create_payload(
                        name=job.check_name,
                        head_sha=job.head_sha,
                        external_id=job.job_id,
                        output=queued,
                    ),
                )
                if created.head_sha != job.head_sha:
                    raise ScanError("GitHub attached the check run to a different commit")
                if not self._store.set_check_run_id(*slot, job.sequence, created.id):
                    raise StaleScanError(SUPERSEDED_CHECK_OWNER)
                progress.check_run_id = created.id
            else:
                progress.check_run_id = claim.check_run_id
                self._client.update_check_run(
                    token,
                    repository,
                    claim.check_run_id,
                    check_run_update_payload(CheckRunStatus.QUEUED, queued),
                )
        self._store.update_job(job.job_id, self._now(), check_run_id=progress.check_run_id)

        context = job.context
        if context.event in (CIEventKind.PULL_REQUEST, CIEventKind.MERGE_GROUP):
            # Merge groups: base..merge group commit, policy from the base (the trusted
            # target branch), results on the merge group commit the queue waits on.
            required = [s for s in (context.base_sha, context.head_sha) if s]
            optional: list[str] = []
            branches: list[str] = []
        else:
            required = [context.after_sha] if context.after_sha else []
            optional = [context.before_sha] if context.before_sha else []
            branches = [auth.default_branch] if auth.default_branch else []
            # The default branch used to limit a new branch's range comes from the API.
            context = context.model_copy(update={"default_branch": auth.default_branch})
        mirror = self._mirrors.prepare(
            job.installation_id,
            repository,
            token,
            required=required,
            optional=optional,
            branches=branches,
        )

        mandatory, organization_policy_version = self._policy_resolver(job.installation_id)
        request = ScanRequest(
            repository=mirror,
            context=context,
            max_commits=self._max_commits,
            mandatory_policy=mandatory,
        )
        plan = self._scans.plan(request)
        if plan.range.head is not None and plan.range.head != job.head_sha:
            raise ScanError("the planned commit does not match the commit the check belongs to")
        self._publish(
            job,
            progress,
            CheckRunStatus.IN_PROGRESS,
            in_progress_output(len(plan.range.commits), str(plan.policy_source)),
        )
        result = self._scans.execute(request, plan)
        with correlation(scan_id=result.metadata.scan_id):
            conclusion, output = completed_output(result)
            self._publish(job, progress, CheckRunStatus.COMPLETED, output, conclusion)
            return self._record_result(job, result, conclusion, mirror, organization_policy_version)

    def _describe(self, job: ScanJob) -> str:
        if job.event == "merge_group":
            return f"the merge queue's merge group at {job.head_sha[:12]}"
        if job.pull_request_number is not None:
            return f"pull request #{job.pull_request_number} at {job.head_sha[:12]}"
        return f"the push of {job.head_sha[:12]}"

    def _publish(
        self,
        job: ScanJob,
        progress: _Progress,
        status: CheckRunStatus,
        output: CheckRunOutput,
        conclusion: CheckRunConclusion | None = None,
    ) -> None:
        if progress.auth is None or progress.check_run_id is None:
            raise ScanError("no check run to update")
        slot = self._slot(job, progress.auth.repository.id)
        with self._lock_for(slot):
            if self._store.check_owner(*slot) != job.sequence:
                raise StaleScanError(SUPERSEDED_CHECK_OWNER)
            self._client.update_check_run(
                progress.auth.token.token,
                progress.auth.repository,
                progress.check_run_id,
                check_run_update_payload(status, output, conclusion),
            )

    def _record_result(
        self,
        job: ScanJob,
        result: ScanResult,
        conclusion: CheckRunConclusion,
        repository: Repository,
        organization_policy_version: int | None,
    ) -> JobState:
        stats = result.statistics
        state = JobState.PASSED if result.enforcement.allowed else JobState.FAILED
        # Job state, findings and the violation lifecycle change in one transaction.
        self._recorder.record_completed(
            job,
            result,
            state=state,
            conclusion=conclusion.value,
            repository=repository,
            organization_policy_version=organization_policy_version,
        )
        self._audit_job(
            AuditEventType.REPOSITORY_SCANNED,
            job,
            action=result.action,
            commits_scanned=stats.commits_scanned,
            rules_version=result.metadata.rules_version[:16],
            policy_version=result.metadata.policy_version[:16],
            organization_policy_version=organization_policy_version,
            trigger=job.trigger.value,
            execution=job.execution,
        )
        self._audit_job(
            AuditEventType.SCAN_PASSED if state is JobState.PASSED else AuditEventType.SCAN_FAILED,
            job,
            action=result.action,
            violations=stats.violations,
            warnings=stats.warnings,
            conclusion=conclusion.value,
            trigger=job.trigger.value,
            execution=job.execution,
            organization_policy_version=organization_policy_version,
        )
        if job.event == "merge_group":
            self._metrics.increment(MERGE_GROUPS_SCANNED, result=result.action.value)
            self._audit_job(
                AuditEventType.MERGE_GROUP_PASSED
                if state is JobState.PASSED
                else AuditEventType.MERGE_GROUP_BLOCKED,
                job,
                action=result.action,
                violations=stats.violations,
                pull_requests=self._merge_group_prs(job),
            )
            if state is JobState.FAILED:
                self._metrics.increment(MERGE_GROUPS_FAILED, reason="blocked")
        blocked = [
            f
            for commit in result.report.commits
            for f in commit.findings
            if f.action is Action.BLOCK
        ]
        if blocked:
            self._metrics.increment(POLICY_VIOLATIONS, len(blocked))
            self._audit_job(
                AuditEventType.POLICY_VIOLATION,
                job,
                action=Action.BLOCK,
                rules=",".join(sorted({f.finding.rule_id for f in blocked})),
                findings=len(blocked),
                fingerprints=",".join(sorted(f.fingerprint[:16] for f in blocked)[:20]),
            )
        if result.plan.policy_weakenings:
            self._audit_job(
                AuditEventType.POLICY_MODIFICATION,
                job,
                changes="; ".join(result.plan.policy_weakenings),
            )
        self._metrics.increment(SCANS_COMPLETED, result=result.action.value)
        log.info(
            "scan_completed",
            commit=job.head_sha[:12],
            result=result.action.value,
            conclusion=conclusion.value,
            commits=stats.commits_scanned,
            violations=stats.violations,
            warnings=stats.warnings,
        )
        return state

    def _merge_group_prs(self, job: ScanJob) -> str | None:
        group = self._store.get_merge_group(job.installation_id, job.repository.id, job.head_sha)
        if group is None or not group.pull_requests:
            return None
        return ",".join(f"#{n}" for n in group.pull_requests)

    def _cancel(self, job: ScanJob, reason: str) -> JobState:
        self._store.update_job(
            job.job_id,
            self._now(),
            state=JobState.CANCELLED,
            message=safe_text(reason),
            failure_kind="stale" if reason in _SUPERSEDED else None,
            lease_expires_at=None,
            completed_at=self._now().timestamp(),
        )
        self._metrics.increment(SCANS_CANCELLED)
        self._audit_job(AuditEventType.SCAN_CANCELLED, job, reason=reason)
        log.info("scan_cancelled", commit=job.head_sha[:12], reason=reason)
        return JobState.CANCELLED

    def _fail(
        self, job: ScanJob, progress: _Progress, kind: FailureKind, exc: BaseException
    ) -> JobState:
        if isinstance(exc, CommitGuardError):
            reason = safe_text(str(exc), 500)
        else:
            reason = f"unexpected error ({type(exc).__name__})"
        now = self._now()
        with self._store.transaction() as db:
            self._store.update_job_in(
                db,
                job.job_id,
                now,
                state=JobState.ERROR,
                failure_kind=kind.value,
                message=reason,
                lease_expires_at=None,
                completed_at=now.timestamp(),
            )
            self._emit_failure_notifications(db, job, reason, now)
        self._metrics.increment(SCANS_FAILED, kind=kind.value)
        if job.event == "merge_group":
            self._metrics.increment(MERGE_GROUPS_FAILED, reason="error")
            self._audit_job(
                AuditEventType.MERGE_GROUP_SCAN_FAILED,
                job,
                failure_kind=kind.value,
                reason=reason,
                pull_requests=self._merge_group_prs(job),
            )
        log.warning(
            "scan_error",
            exc=exc if isinstance(exc, CommitGuardError) else None,
            failure_kind=kind.value,
            error_type=type(exc).__name__,
            commit=job.head_sha[:12],
        )
        event_type = {
            FailureKind.AUTHORIZATION: AuditEventType.AUTHORIZATION_DENIED,
            FailureKind.CONFIGURATION: AuditEventType.CONFIGURATION_ERROR,
        }.get(kind, AuditEventType.SCAN_ERROR)
        self._audit_job(event_type, job, failure_kind=kind.value, reason=reason)
        if progress.auth is not None and progress.check_run_id is not None:
            conclusion, output = error_output(kind, reason)
            try:
                self._publish(job, progress, CheckRunStatus.COMPLETED, output, conclusion)
            except StaleScanError:
                pass  # a newer scan owns the check and will publish its own result
            except Exception as publish_error:  # noqa: BLE001 - best effort; already failed closed
                log.error(
                    "check_run_failure_not_published",
                    error_type=type(publish_error).__name__,
                    commit=job.head_sha[:12],
                )
        return JobState.ERROR

    def _emit_failure_notifications(
        self, db: sqlite3.Connection, job: ScanJob, reason: str, now: datetime
    ) -> None:
        """Operational failures that someone is waiting on (never a security decision)."""
        account_id = account_for_installation(db, job.installation_id)
        if account_id is None:
            return
        if job.event == "merge_group":
            emit(
                db,
                merge_queue_failure(
                    job,
                    account_id,
                    f"CommitGuard could not validate the merge group ({reason}). The check "
                    "failed closed, so the merge queue cannot merge it.",
                ),
                now,
            )
        elif job.trigger is ScanTrigger.RERUN:
            emit(
                db,
                NotificationEvent(
                    type=NotificationType.CHECK_RERUN_FAILED,
                    account_id=account_id,
                    severity=Severity.MEDIUM,
                    installation_id=job.installation_id,
                    repository_id=job.repository.id,
                    resource_type="scan",
                    resource_id=job.job_id,
                    dedup_key=domain_key(NotificationType.CHECK_RERUN_FAILED, job.job_id),
                    title=f"Check re-run failed in {job.repository.full_name}",
                    body=(
                        f"The re-run of {job.check_name} at {job.head_sha[:12]} could not be "
                        f"completed: {reason}. The check failed closed."
                    ),
                    metadata={"scan": job.job_id, "execution": job.execution},
                ),
                now,
            )

    def _exhausted(self, job: ScanJob) -> None:
        """A job that crashed or timed out on every attempt: audit it and fail its check."""
        reason = "scan abandoned after repeated attempts"
        with correlation(
            job_id=job.job_id,
            installation_id=job.installation_id,
            repository=job.repository.full_name,
        ):
            self._metrics.increment(SCANS_FAILED, kind=FailureKind.INTERNAL.value)
            self._audit_job(
                AuditEventType.SCAN_ERROR,
                job,
                failure_kind=FailureKind.INTERNAL.value,
                reason=reason,
                attempts=job.attempts,
            )
            with self._store.transaction() as db:
                self._emit_failure_notifications(db, job, reason, self._now())
            if job.check_run_id is None:
                return
            try:
                auth = self._installations.authorize(job.installation_id, job.repository)
                progress = _Progress(auth=auth, check_run_id=job.check_run_id)
                conclusion, output = error_output(FailureKind.INTERNAL, reason)
                self._publish(job, progress, CheckRunStatus.COMPLETED, output, conclusion)
            except StaleScanError:
                pass  # a newer execution owns the check
            except Exception as exc:  # noqa: BLE001 - best effort; the job is already an error
                log.error("check_run_failure_not_published", error_type=type(exc).__name__)

    def _audit_job(
        self,
        event_type: AuditEventType,
        job: ScanJob,
        *,
        action: Action | None = None,
        **data: str | int | bool | None,
    ) -> None:
        self._audit.record(
            event_type,
            installation_id=job.installation_id,
            repository_id=job.repository.id,
            repository=job.repository.full_name,
            head_sha=job.head_sha,
            action=action,
            extra=data,
        )
