"""Write operations the dashboard can request.

Each command resolves its target inside the caller's access scope first, so a
resource from another tenant is "not found" before any permission question is
asked; then it checks the permission for that resource's account; then it
changes state and records an audit event in the same transaction.

None of these commands can change a security decision already made: they cannot
mark a violation resolved, turn a failed GitHub check into a success, or touch
Git history. Pausing monitoring and weakening policy are the only operations
that can reduce enforcement; both need explicit confirmation and a recent
sign-in.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime

from commitguard.audit.models import Actor, AuditEventType
from commitguard.controlplane.access import Permission, Principal
from commitguard.controlplane.errors import (
    ConfirmationRequiredError,
    ConflictError,
    InputValidationError,
    NotFoundError,
    PermissionDeniedError,
    ReauthenticationRequiredError,
    UpstreamUnavailableError,
)
from commitguard.controlplane.policies import REAUTHENTICATION_WINDOW
from commitguard.controlplane.queries import DashboardQueries
from commitguard.controlplane.results import clean_text
from commitguard.controlplane.views import SyncResult
from commitguard.exceptions.base import CommitGuardError
from commitguard.github.enforcement_status import EnforcementProbe
from commitguard.github.errors import AuthorizationError, GitHubAPIError
from commitguard.github.identifiers import RepositoryRef
from commitguard.github.installations import InstallationService
from commitguard.github.storage import ScanTrigger, SqliteStateStore
from commitguard.observability.logging import get_logger
from commitguard.services.audit import AuditService

log = get_logger(__name__)

MAX_NOTE_CHARS = 500


def _actor(principal: Principal) -> Actor:
    return Actor.user(principal.user_id, principal.login)


def _note(value: object, field: str, *, required: bool = False) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise InputValidationError("A reason is required.", field=field)
        return None
    if not isinstance(value, str) or len(value) > MAX_NOTE_CHARS:
        raise InputValidationError(
            f"{field} must be text of at most {MAX_NOTE_CHARS} characters", field=field
        )
    return clean_text(value.strip(), MAX_NOTE_CHARS)


class ControlPlaneCommands:
    def __init__(
        self,
        store: SqliteStateStore,
        queries: DashboardQueries,
        audit: AuditService,
        installations: InstallationService,
        probe: EnforcementProbe,
        *,
        enqueue: Callable[[str], bool],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._queries = queries
        self._audit = audit
        self._installations = installations
        self._probe = probe
        self._enqueue = enqueue
        self._now = now

    # ------------------------------------------------------------------ #
    # Violations
    # ------------------------------------------------------------------ #
    def acknowledge_violation(self, principal: Principal, violation_id: str, note: object) -> None:
        scope = principal.scope(Permission.VIOLATIONS_READ)
        row = self._queries.violation_row(scope, violation_id)
        if row is None:
            raise NotFoundError()
        account_id = int(row["account_id"])
        if not principal.can(Permission.VIOLATIONS_MANAGE, account_id):
            raise PermissionDeniedError()
        text = _note(note, "note")
        now = self._now().timestamp()
        with self._store.transaction() as db:
            cursor = db.execute(
                "UPDATE violations SET acknowledged_at = ?, acknowledged_by_id = ?, "
                "acknowledged_by_login = ?, acknowledgement_note = ?, updated_at = ? "
                "WHERE violation_id = ? AND status = 'open'",
                (now, principal.user_id, principal.login, text, now, violation_id),
            )
            if cursor.rowcount != 1:
                raise ConflictError("Only open violations can be acknowledged.")
            event = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.VIOLATION_ACKNOWLEDGED,
                    actor=_actor(principal),
                    installation_id=row["installation_id"],
                    repository_id=row["repository_id"],
                    violation=violation_id,
                    rule=row["rule_id"],
                    note=text,
                ),
            )
        self._audit.log_stored(event)

    def remove_acknowledgement(self, principal: Principal, violation_id: str) -> None:
        scope = principal.scope(Permission.VIOLATIONS_READ)
        row = self._queries.violation_row(scope, violation_id)
        if row is None:
            raise NotFoundError()
        if not principal.can(Permission.VIOLATIONS_MANAGE, int(row["account_id"])):
            raise PermissionDeniedError()
        with self._store.transaction() as db:
            cursor = db.execute(
                "UPDATE violations SET acknowledged_at = NULL, acknowledged_by_id = NULL, "
                "acknowledged_by_login = NULL, acknowledgement_note = NULL, updated_at = ? "
                "WHERE violation_id = ? AND acknowledged_at IS NOT NULL",
                (self._now().timestamp(), violation_id),
            )
            if cursor.rowcount != 1:
                raise ConflictError("This violation is not acknowledged.")
            event = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.VIOLATION_ACKNOWLEDGEMENT_REMOVED,
                    actor=_actor(principal),
                    installation_id=row["installation_id"],
                    repository_id=row["repository_id"],
                    violation=violation_id,
                    rule=row["rule_id"],
                ),
            )
        self._audit.log_stored(event)

    # ------------------------------------------------------------------ #
    # Repositories
    # ------------------------------------------------------------------ #
    def _manageable_repository(self, principal: Principal, repository_id: int):  # type: ignore[no-untyped-def]
        row = self._queries.repository_ref(
            principal.scope(Permission.REPOSITORIES_READ), repository_id
        )
        if row is None:
            raise NotFoundError()
        if not principal.can(Permission.REPOSITORIES_MANAGE, int(row["account_id"])):
            raise PermissionDeniedError()
        return row

    def set_monitoring(
        self,
        principal: Principal,
        repository_id: int,
        *,
        enabled: object,
        confirm: object,
        reason: object,
    ) -> None:
        if not isinstance(enabled, bool):
            raise InputValidationError("enabled must be true or false", field="enabled")
        row = self._manageable_repository(principal, repository_id)
        installation_id = int(row["installation_id"])
        now = self._now()
        text = _note(reason, "reason", required=not enabled)
        if not enabled:
            if confirm is not True:
                raise ConfirmationRequiredError(
                    "Pausing monitoring stops CommitGuard checks for this repository and must be "
                    "confirmed."
                )
            if now - principal.authenticated_at > REAUTHENTICATION_WINDOW:
                raise ReauthenticationRequiredError()
        with self._store.transaction() as db:
            current = db.execute(
                "SELECT monitoring_enabled FROM repository_settings WHERE installation_id = ? "
                "AND repository_id = ?",
                (installation_id, repository_id),
            ).fetchone()
            if (current is None and enabled) or (
                current is not None and bool(current["monitoring_enabled"]) == enabled
            ):
                raise ConflictError(
                    "Monitoring is already " + ("enabled." if enabled else "paused.")
                )
            db.execute(
                "INSERT INTO repository_settings (installation_id, repository_id, "
                "monitoring_enabled, updated_at, updated_by_login) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (installation_id, repository_id) DO UPDATE SET "
                "monitoring_enabled = excluded.monitoring_enabled, "
                "updated_at = excluded.updated_at, updated_by_login = excluded.updated_by_login",
                (installation_id, repository_id, int(enabled), now.timestamp(), principal.login),
            )
            if not enabled:
                db.execute(
                    "UPDATE scan_jobs SET state = 'cancelled', message = 'monitoring paused', "
                    "updated_at = ?, completed_at = ? WHERE installation_id = ? "
                    "AND repository_id = ? AND state = 'queued'",
                    (now.timestamp(), now.timestamp(), installation_id, repository_id),
                )
            event = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.REPOSITORY_MONITORING_ENABLED
                    if enabled
                    else AuditEventType.REPOSITORY_MONITORING_DISABLED,
                    actor=_actor(principal),
                    installation_id=installation_id,
                    repository_id=repository_id,
                    repository=f"{row['owner']}/{row['name']}",
                    reason=text,
                ),
            )
        self._audit.log_stored(event)

    def refresh_enforcement(self, principal: Principal, repository_id: int) -> None:
        row = self._manageable_repository(principal, repository_id)
        installation_id = int(row["installation_id"])
        repository = RepositoryRef(id=repository_id, owner=row["owner"], name=row["name"])
        try:
            authorized = self._installations.authorize(installation_id, repository)
            evidence = self._probe.probe(
                authorized.token.token, authorized.repository, authorized.default_branch
            )
        except AuthorizationError as exc:
            raise ConflictError(f"GitHub denied access: {exc}") from None
        except GitHubAPIError:
            raise UpstreamUnavailableError(
                "GitHub could not be reached. Try again later."
            ) from None
        except CommitGuardError as exc:
            log.warning("enforcement_probe_failed", error_type=type(exc).__name__)
            raise UpstreamUnavailableError("The enforcement status could not be checked.") from None
        with self._store.transaction() as db:
            previous = db.execute(
                "SELECT branch_protection FROM enforcement_status WHERE installation_id = ? "
                "AND repository_id = ?",
                (installation_id, repository_id),
            ).fetchone()
            db.execute(
                "INSERT INTO enforcement_status (installation_id, repository_id, checked_at, "
                "branch, actions, actions_detail, branch_protection, required_checks, "
                "branch_protection_detail, merge_queue, merge_queue_detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (installation_id, repository_id) DO UPDATE SET "
                "checked_at = excluded.checked_at, branch = excluded.branch, "
                "actions = excluded.actions, actions_detail = excluded.actions_detail, "
                "branch_protection = excluded.branch_protection, "
                "required_checks = excluded.required_checks, "
                "branch_protection_detail = excluded.branch_protection_detail, "
                "merge_queue = excluded.merge_queue, "
                "merge_queue_detail = excluded.merge_queue_detail",
                (
                    installation_id,
                    repository_id,
                    evidence.checked_at.timestamp(),
                    clean_text(evidence.branch, 256) if evidence.branch else None,
                    evidence.actions,
                    clean_text(evidence.actions_detail, 500),
                    evidence.branch_protection,
                    json.dumps([clean_text(c, 200) for c in evidence.required_checks]),
                    clean_text(evidence.branch_protection_detail, 500),
                    evidence.merge_queue,
                    clean_text(evidence.merge_queue_detail, 500),
                ),
            )
            event = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.ENFORCEMENT_STATUS_CHECKED,
                    actor=_actor(principal),
                    installation_id=installation_id,
                    repository_id=repository_id,
                    repository=authorized.repository.full_name,
                    actions=evidence.actions,
                    branch_protection=evidence.branch_protection,
                    merge_queue=evidence.merge_queue,
                ),
            )
            was_required = previous is not None and previous["branch_protection"] == "required"
            account_id = account_for_installation(db, installation_id)
            if was_required and evidence.branch_protection != "required" and account_id:
                emit(
                    db,
                    NotificationEvent(
                        type=NotificationType.REPOSITORY_UNPROTECTED,
                        account_id=account_id,
                        severity=Severity.HIGH,
                        installation_id=installation_id,
                        repository_id=repository_id,
                        resource_type="repository",
                        resource_id=str(repository_id),
                        dedup_key=domain_key(
                            NotificationType.REPOSITORY_UNPROTECTED, account_id, repository_id
                        ),
                        title=f"Repository no longer protected: {authorized.repository.full_name}",
                        body=(
                            f"GitHub no longer requires a CommitGuard check on "
                            f"{authorized.repository.full_name}"
                            f"{' (' + evidence.branch + ')' if evidence.branch else ''}. "
                            "Failing CommitGuard checks no longer block merges. "
                            f"{clean_text(evidence.branch_protection_detail, 300)}"
                        ),
                        metadata={"branch_protection": evidence.branch_protection},
                    ),
                    self._now(),
                )
        self._audit.log_stored(event)

    # ------------------------------------------------------------------ #
    # GitHub installations
    # ------------------------------------------------------------------ #
    def sync_installation(self, principal: Principal, installation_id: int) -> SyncResult:
        scope = principal.scope(Permission.REPOSITORIES_READ)
        if installation_id not in scope.installation_ids:
            raise NotFoundError()
        account_id = principal.installations[installation_id]
        if not principal.can(Permission.GITHUB_MANAGE, account_id):
            raise PermissionDeniedError()
        try:
            sync = self._installations.sync_repositories(installation_id, _actor(principal))
        except AuthorizationError as exc:
            raise ConflictError(f"GitHub denied access: {exc}") from None
        except GitHubAPIError:
            raise UpstreamUnavailableError(
                "GitHub could not be reached. Try again later."
            ) from None
        return SyncResult(
            installation_id=installation_id,
            repositories=len(sync.repositories),
            added=tuple(r.full_name for r in sync.added),
            removed=tuple(r.full_name for r in sync.removed),
            synced_at=sync.synced_at,
        )

    # ------------------------------------------------------------------ #
    # Scans
    # ------------------------------------------------------------------ #
    def request_rescan(self, principal: Principal, scan_id: str) -> str:
        """Queue a new execution of exactly the commits a stored scan covered.

        The repository, commits and event come from the stored scan - never from
        the request - and the new execution goes through the same worker path as a
        webhook (authorization against GitHub, current effective policy, check
        ownership). It is recorded as execution N+1 of the same scan with trigger
        ``manual``; earlier executions are unchanged. Callers cannot choose a
        historical policy version.
        """
        row = self._queries.scan_row_for_command(principal.scope(Permission.SCANS_READ), scan_id)
        if row is None:
            raise NotFoundError()
        allowed, reason = self._queries.rescan_eligibility(row, principal)
        if not allowed:
            if not principal.can(Permission.SCANS_TRIGGER, int(row["account_id"] or 0)):
                raise PermissionDeniedError()
            raise ConflictError(reason or "This scan cannot be repeated.")
        job = self._store.get_job(scan_id)
        if job is None:  # pragma: no cover - the row was just read
            raise NotFoundError()
        new_job, created = self._store.create_execution(
            job, trigger=ScanTrigger.MANUAL, now=self._now(), requested_by=principal.login
        )
        if not created:
            raise ConflictError("A scan of these commits is already queued or running.")
        self._audit.record(
            AuditEventType.SCAN_REQUESTED,
            actor=_actor(principal),
            installation_id=job.installation_id,
            repository_id=job.repository.id,
            repository=job.repository.full_name,
            head_sha=job.head_sha,
            job=new_job.job_id,
            previous_scan=scan_id,
            execution=new_job.execution,
            trigger=ScanTrigger.MANUAL.value,
        )
        self._enqueue(new_job.job_id)  # if the queue is full, recovery picks it up
        return new_job.job_id
