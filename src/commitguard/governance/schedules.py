"""Scheduled scans: re-evaluate default branches on a daily or weekly schedule.

A schedule targets the organization, a repository group or one repository and
runs at a local time in a time zone (the organization's by default)::

    scan_schedules            "Production nightly", group Production, daily 02:00 UTC
        │ due (next_run_at <= now)
        ▼
    scan_schedule_runs        one row per (schedule, slot) - a slot runs at most once
        │ bounded batches per maintenance tick
        ▼
    DefaultBranchScanner      GitHub reports the default branch head (installation token)
        │
        ▼
    scan_jobs (trigger "scheduled")  ->  the normal worker: authorization, check ownership,
                                         current effective policy, fail-closed errors

Scheduled scans use the existing scan service and worker; there is no other
scanner. They are:

* **idempotent** - a run is unique per schedule and slot, and each repository's
  job key includes the schedule and slot, so a restarted or concurrent
  maintenance loop never queues the same scan twice;
* **bounded** - at most :data:`JOBS_PER_TICK` repositories are processed per
  maintenance pass, and nothing is queued while more than :data:`MAX_BACKLOG`
  scans wait in the queue, so a schedule over thousands of repositories never
  floods the workers or GitHub's API;
* **observable** - each run records repositories covered, queued, skipped (with
  reasons) and failed, and ends ``completed``, ``partial`` or ``failed``.

Skipped, not scanned: archived repositories, repositories the GitHub App can no
longer access, paused monitoring, and a default branch head that was already
scanned with the current effective policy. The first scheduled scan of a
branch has no earlier trusted commit, so - like a push of a new default branch -
its repository configuration comes from built-in defaults while organization
governance still applies (see :mod:`commitguard.services.ci`).
"""

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict

from commitguard.audit.models import SYSTEM_ACTOR, Actor, AuditEventType
from commitguard.ci.context import CIContext, CIEventKind, CIProvider
from commitguard.controlplane.access import Permission, Principal
from commitguard.controlplane.errors import ConflictError, InputValidationError, NotFoundError
from commitguard.controlplane.policies import PolicyTarget, PolicyTargetType, target_label
from commitguard.controlplane.views import PolicyTargetView
from commitguard.exceptions.base import CommitGuardError
from commitguard.github.checks import APP_PUSH_CHECK_NAME
from commitguard.github.client import GitHubClient
from commitguard.github.identifiers import RepositoryRef
from commitguard.github.installations import InstallationService
from commitguard.github.pull_requests import branch_group_key
from commitguard.github.storage import NewScanJob, SqliteStateStore
from commitguard.governance.cache import group_member_ids
from commitguard.governance.common import (
    MAX_NAME_CHARS,
    account_repositories,
    dt,
    is_hex_id,
    new_id,
    req_dt,
    require,
    text,
    ts,
    visible_repository_ids,
)
from commitguard.governance.inventory import governance_state
from commitguard.governance.resolver import GovernanceResolver
from commitguard.governance.settings import load_settings
from commitguard.governance.workflow import parse_target
from commitguard.observability.logging import get_logger
from commitguard.security.hashing import fingerprint
from commitguard.services.audit import AuditService

log = get_logger(__name__)

JOBS_PER_TICK = 50
MAX_BACKLOG = 200
MAX_SCHEDULES_PER_ORGANIZATION = 100
CADENCES = ("daily", "weekly")


class ScheduleRunView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    slot: datetime
    state: str
    started_at: datetime
    completed_at: datetime | None
    repositories: int
    queued: int
    skipped: int
    failed: int
    detail: dict[str, int]


class ScanScheduleView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    organization_id: int
    name: str
    target: PolicyTargetView
    cadence: str
    hour: int
    minute: int
    weekday: int | None
    timezone: str
    enabled: bool
    next_run_at: datetime | None
    revision: int
    repositories_covered: int
    last_run: ScheduleRunView | None
    created_by: str | None
    created_at: datetime
    updated_by: str | None
    updated_at: datetime
    can_manage: bool


def next_occurrence(
    cadence: str, hour: int, minute: int, weekday: int | None, timezone: str, after: datetime
) -> datetime:
    """The first local ``hour:minute`` (on ``weekday`` for weekly) strictly after ``after``."""
    zone = ZoneInfo(timezone)
    local = after.astimezone(zone)
    for offset in range(0, 15):
        day = (local + timedelta(days=offset)).date()
        if cadence == "weekly" and day.weekday() != weekday:
            continue
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
        if candidate > local:
            return candidate.astimezone(UTC)
    raise ValueError("no occurrence found")  # pragma: no cover - 15 days cover any week


def _schedule_fields(
    cadence: object, hour: object, minute: object, weekday: object, timezone: object
) -> tuple[str, int, int, int | None, str]:
    if cadence not in CADENCES:
        raise InputValidationError("cadence must be daily or weekly", field="cadence")
    assert isinstance(cadence, str)  # noqa: S101 - checked above

    def bounded(name: str, value: object, upper: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= upper:
            raise InputValidationError(f"{name} must be between 0 and {upper}", field=name)
        return value

    clean_hour = bounded("hour", hour, 23)
    clean_minute = bounded("minute", minute, 59)
    clean_weekday = bounded("weekday", weekday, 6) if cadence == "weekly" else None
    if not isinstance(timezone, str) or len(timezone) > 64:
        raise InputValidationError("timezone must be an IANA time zone", field="timezone")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise InputValidationError("unknown time zone", field="timezone") from None
    return cadence, clean_hour, clean_minute, clean_weekday, timezone


class DefaultBranchScanner:
    """Queue a scan of a repository's current default branch head."""

    def __init__(
        self,
        store: SqliteStateStore,
        installations: InstallationService,
        client: GitHubClient,
        resolver: GovernanceResolver,
        enqueue: Callable[[str], bool],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._installations = installations
        self._client = client
        self._resolver = resolver
        self._enqueue = enqueue
        self._now = now

    def queue(
        self,
        account_id: int,
        repository_id: int,
        *,
        key: tuple[str, ...],
        requested_by: str | None,
        schedule_id: str | None = None,
    ) -> str:
        """``queued``, ``skipped:<reason>``; raises when GitHub cannot be reached."""
        repository = account_repositories(self._store, account_id).get(repository_id)
        if repository is None:
            return "skipped:not_found"
        if repository.archived:
            return "skipped:archived"
        if not repository.connected:
            return "skipped:disconnected"
        if not self._store.monitoring_enabled(repository.installation_id, repository_id):
            return "skipped:monitoring_paused"
        if governance_state(self._store, account_id, repository_id).onboarding == "excluded":
            return "skipped:excluded"
        ref = RepositoryRef(id=repository_id, owner=repository.owner, name=repository.name)
        authorized = self._installations.authorize(repository.installation_id, ref)
        branch = authorized.default_branch
        if not branch:
            return "skipped:no_default_branch"
        info = self._client.get_branch(authorized.token.token, authorized.repository, branch)
        if info.commit is None:
            return "skipped:no_default_branch"
        head = info.commit.sha
        branch_ref = f"refs/heads/{branch}"
        group = branch_group_key(branch_ref)
        previous = self._store.query(
            "SELECT head_sha, governance_fingerprint FROM scan_jobs WHERE installation_id = ? "
            "AND repository_id = ? AND group_key = ? AND state IN ('passed', 'failed') "
            "ORDER BY sequence DESC LIMIT 1",
            (repository.installation_id, repository_id, group),
        )
        before = str(previous[0]["head_sha"]) if previous else None
        if previous and before == head:
            current = self._resolver.for_repository(account_id, repository_id).fingerprint
            if previous[0]["governance_fingerprint"] == current:
                return "skipped:unchanged"
            before = None  # re-evaluate the branch head under the changed policy
        context = CIContext(
            provider=CIProvider.GITHUB,
            event=CIEventKind.PUSH,
            event_name="schedule",
            repository=authorized.repository.full_name,
            ref=branch_ref,
            default_branch=branch,
            before_sha=before if before != head else None,
            after_sha=head,
        )
        job, created = self._store.create_job(
            NewScanJob(
                job_key=fingerprint(["scheduled", *key, str(repository_id), head]),
                installation_id=repository.installation_id,
                repository=authorized.repository,
                delivery_id=None,
                event="scheduled",
                group_key=group,
                head_sha=head,
                check_name=APP_PUSH_CHECK_NAME,
                pull_request_number=None,
                context=context,
                requested_by=requested_by,
            ),
            self._now(),
        )
        if not created:
            return "skipped:already_queued"
        if schedule_id is not None:
            with self._store.transaction() as db:
                db.execute(
                    "UPDATE scan_jobs SET schedule_id = ? WHERE job_id = ?",
                    (schedule_id, job.job_id),
                )
        self._enqueue(job.job_id)  # a full queue is recovered by maintenance
        return "queued"


class ScanScheduleService:
    def __init__(
        self,
        store: SqliteStateStore,
        audit: AuditService,
        scanner: DefaultBranchScanner | None,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._audit = audit
        self._scanner = scanner
        self._now = now

    # -- scope ------------------------------------------------------------ #
    def _scope(self, db: sqlite3.Connection, account_id: int, target: PolicyTarget) -> list[int]:
        repositories = sorted(account_repositories(db, account_id))
        if target.type is PolicyTargetType.GROUP:
            members = set(group_member_ids(db, account_id, target.id))
            return [r for r in repositories if r in members]
        if target.type is PolicyTargetType.REPOSITORY:
            return [r for r in repositories if r == int(target.id)]
        return repositories

    # -- views ------------------------------------------------------------ #
    def _row(self, schedule_id: str) -> sqlite3.Row:
        if not is_hex_id(schedule_id):
            raise NotFoundError()
        rows = self._store.query(
            "SELECT * FROM scan_schedules WHERE schedule_id = ?", (schedule_id,)
        )
        if not rows:
            raise NotFoundError()
        return rows[0]

    @staticmethod
    def _run_view(row: sqlite3.Row) -> ScheduleRunView:
        detail = json.loads(str(row["detail"] or "{}"))
        return ScheduleRunView(
            id=str(row["run_id"]),
            slot=req_dt(row["slot"]),
            state=str(row["state"]),
            started_at=req_dt(row["started_at"]),
            completed_at=dt(row["completed_at"]),
            repositories=int(row["repositories"]),
            queued=int(row["queued"]),
            skipped=int(row["skipped"]),
            failed=int(row["failed"]),
            detail={str(k): int(v) for k, v in detail.items() if isinstance(v, int)},
        )

    def _view(self, row: sqlite3.Row, principal: Principal) -> ScanScheduleView:
        account_id = int(row["account_id"])
        target = PolicyTarget(PolicyTargetType(row["target_type"]), str(row["target_id"]))
        with self._store.transaction() as db:
            label = target_label(db, account_id, target) or "(removed)"
            covered = len(self._scope(db, account_id, target))
        runs = self._store.query(
            "SELECT * FROM scan_schedule_runs WHERE schedule_id = ? ORDER BY slot DESC LIMIT 1",
            (row["schedule_id"],),
        )
        return ScanScheduleView(
            id=str(row["schedule_id"]),
            organization_id=account_id,
            name=str(row["name"]),
            target=PolicyTargetView(type=target.type.value, id=target.id, label=label),
            cadence=str(row["cadence"]),
            hour=int(row["hour"]),
            minute=int(row["minute"]),
            weekday=row["weekday"],
            timezone=str(row["timezone"]),
            enabled=bool(row["enabled"]),
            next_run_at=dt(row["next_run_at"]) if row["enabled"] else None,
            revision=int(row["revision"]),
            repositories_covered=covered,
            last_run=self._run_view(runs[0]) if runs else None,
            created_by=row["created_by_login"],
            created_at=req_dt(row["created_at"]),
            updated_by=row["updated_by_login"],
            updated_at=req_dt(row["updated_at"]),
            can_manage=principal.can(Permission.SECURITY_MANAGE, account_id),
        )

    def list_schedules(self, principal: Principal, account_id: int) -> list[ScanScheduleView]:
        require(principal, Permission.SECURITY_READ, account_id)
        rows = self._store.query(
            "SELECT * FROM scan_schedules WHERE account_id = ? ORDER BY created_at LIMIT ?",
            (account_id, MAX_SCHEDULES_PER_ORGANIZATION),
        )
        return [self._view(row, principal) for row in rows]

    def get(
        self, principal: Principal, schedule_id: str
    ) -> tuple[ScanScheduleView, list[ScheduleRunView]]:
        row = self._row(schedule_id)
        require(principal, Permission.SECURITY_READ, int(row["account_id"]))
        runs = self._store.query(
            "SELECT * FROM scan_schedule_runs WHERE schedule_id = ? ORDER BY slot DESC LIMIT 20",
            (schedule_id,),
        )
        return self._view(row, principal), [self._run_view(r) for r in runs]

    # -- writes ----------------------------------------------------------- #
    def create(
        self, principal: Principal, account_id: int, body: dict[str, object]
    ) -> ScanScheduleView:
        require(principal, Permission.SECURITY_MANAGE, account_id)
        target = parse_target(body.get("target_type"), body.get("target_id"))
        with self._store.transaction() as db:
            if target_label(db, account_id, target) is None:
                raise NotFoundError()
        if target.type is PolicyTargetType.REPOSITORY and int(target.id) not in (
            visible_repository_ids(self._store, principal, account_id)
        ):
            raise NotFoundError()
        name = text(body.get("name"), "name", limit=MAX_NAME_CHARS, required=True)
        timezone = body.get("timezone") or load_settings(self._store, account_id).settings.timezone
        cadence, hour, minute, weekday, zone = _schedule_fields(
            body.get("cadence"),
            body.get("hour"),
            body.get("minute", 0),
            body.get("weekday"),
            timezone,
        )
        enabled = body.get("enabled", True)
        if not isinstance(enabled, bool):
            raise InputValidationError("enabled must be true or false", field="enabled")
        now = self._now()
        schedule_id = new_id()
        next_run = next_occurrence(cadence, hour, minute, weekday, zone, now)
        with self._store.transaction() as db:
            count = db.execute(
                "SELECT COUNT(*) AS n FROM scan_schedules WHERE account_id = ?", (account_id,)
            ).fetchone()["n"]
            if int(count) >= MAX_SCHEDULES_PER_ORGANIZATION:
                raise ConflictError(
                    f"An organization can have at most {MAX_SCHEDULES_PER_ORGANIZATION} schedules."
                )
            db.execute(
                "INSERT INTO scan_schedules (schedule_id, account_id, target_type, target_id, "
                "name, cadence, hour, minute, weekday, timezone, enabled, next_run_at, created_at, "
                "created_by_login, updated_at, updated_by_login) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    schedule_id,
                    account_id,
                    target.type.value,
                    target.id,
                    name,
                    cadence,
                    hour,
                    minute,
                    weekday,
                    zone,
                    1 if enabled else 0,
                    ts(next_run),
                    ts(now),
                    principal.login,
                    ts(now),
                    principal.login,
                ),
            )
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.SCAN_SCHEDULE_CREATED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=account_id,
                    schedule=schedule_id,
                    name=name,
                    target_type=target.type.value,
                    target_id=target.id or None,
                    cadence=cadence,
                    time=f"{hour:02d}:{minute:02d} {zone}",
                    enabled=enabled,
                ),
            )
        self._audit.log_stored(stored)
        return self._view(self._row(schedule_id), principal)

    def update(
        self, principal: Principal, schedule_id: str, body: dict[str, object]
    ) -> ScanScheduleView:
        row = self._row(schedule_id)
        account_id = int(row["account_id"])
        require(principal, Permission.SECURITY_MANAGE, account_id)
        expected = body.get("expected_revision")
        if not isinstance(expected, int) or isinstance(expected, bool):
            raise InputValidationError(
                "expected_revision must be an integer", field="expected_revision"
            )
        name = (
            text(body.get("name"), "name", limit=MAX_NAME_CHARS, required=True)
            if "name" in body
            else row["name"]
        )
        cadence, hour, minute, weekday, zone = _schedule_fields(
            body.get("cadence", row["cadence"]),
            body.get("hour", row["hour"]),
            body.get("minute", row["minute"]),
            body.get("weekday", row["weekday"]),
            body.get("timezone", row["timezone"]),
        )
        enabled = body.get("enabled", bool(row["enabled"]))
        if not isinstance(enabled, bool):
            raise InputValidationError("enabled must be true or false", field="enabled")
        now = self._now()
        next_run = next_occurrence(cadence, hour, minute, weekday, zone, now)
        with self._store.transaction() as db:
            changed = db.execute(
                "UPDATE scan_schedules SET name = ?, cadence = ?, hour = ?, minute = ?, "
                "weekday = ?, timezone = ?, enabled = ?, next_run_at = ?, revision = revision + 1, "
                "updated_at = ?, updated_by_login = ? WHERE schedule_id = ? AND revision = ?",
                (
                    name,
                    cadence,
                    hour,
                    minute,
                    weekday,
                    zone,
                    1 if enabled else 0,
                    ts(next_run),
                    ts(now),
                    principal.login,
                    schedule_id,
                    expected,
                ),
            ).rowcount
            if changed != 1:
                raise ConflictError("The schedule was changed by someone else. Reload first.")
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.SCAN_SCHEDULE_DISABLED
                    if bool(row["enabled"]) and not enabled
                    else AuditEventType.SCAN_SCHEDULE_CHANGED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=account_id,
                    schedule=schedule_id,
                    name=str(name),
                    cadence=cadence,
                    time=f"{hour:02d}:{minute:02d} {zone}",
                    enabled=enabled,
                ),
            )
        self._audit.log_stored(stored)
        return self._view(self._row(schedule_id), principal)

    def disable(self, principal: Principal, schedule_id: str) -> ScanScheduleView:
        row = self._row(schedule_id)
        return self.update(
            principal,
            schedule_id,
            {"expected_revision": int(row["revision"]), "enabled": False},
        )

    # -- background ------------------------------------------------------- #
    def run_due(self) -> dict[str, int]:
        """Start due runs and process running ones within the per-tick budget."""
        now = self._now()
        started = 0
        for row in self._store.query(
            "SELECT * FROM scan_schedules WHERE enabled = 1 AND next_run_at <= ? "
            "ORDER BY next_run_at LIMIT 50",
            (ts(now),),
        ):
            slot = float(row["next_run_at"])
            following = next_occurrence(
                row["cadence"], row["hour"], row["minute"], row["weekday"], row["timezone"], now
            )
            with self._store.transaction() as db:
                db.execute(
                    "UPDATE scan_schedules SET next_run_at = ? WHERE schedule_id = ? "
                    "AND next_run_at = ?",
                    (ts(following), row["schedule_id"], slot),
                )
                target = PolicyTarget(PolicyTargetType(row["target_type"]), str(row["target_id"]))
                repositories = self._scope(db, int(row["account_id"]), target)
                cursor = db.execute(
                    "INSERT OR IGNORE INTO scan_schedule_runs (run_id, schedule_id, account_id, "
                    "slot, state, started_at, repositories, detail) "
                    "VALUES (?, ?, ?, ?, 'running', ?, ?, '{}')",
                    (
                        new_id(),
                        row["schedule_id"],
                        row["account_id"],
                        slot,
                        ts(now),
                        len(repositories),
                    ),
                )
                started += cursor.rowcount
        processed = self._process_runs()
        return {"started": started, **processed}

    def _process_runs(self) -> dict[str, int]:
        totals = {"queued": 0, "skipped": 0, "failed": 0}
        backlog = int(
            self._store.query("SELECT COUNT(*) AS n FROM scan_jobs WHERE state = 'queued'")[0]["n"]
        )
        budget = JOBS_PER_TICK if backlog < MAX_BACKLOG else 0
        if budget == 0:
            log.info("scheduled_scans_deferred", backlog=backlog)
        for run in self._store.query(
            "SELECT r.*, s.target_type, s.target_id, s.name FROM scan_schedule_runs r JOIN "
            "scan_schedules s ON s.schedule_id = r.schedule_id WHERE r.state = 'running' "
            "ORDER BY r.started_at LIMIT 20"
        ):
            account_id = int(run["account_id"])
            target = PolicyTarget(PolicyTargetType(run["target_type"]), str(run["target_id"]))
            with self._store.transaction() as db:
                repositories = self._scope(db, account_id, target)
            position = int(run["cursor"])
            detail: dict[str, int] = json.loads(str(run["detail"] or "{}"))
            counts = {k: int(run[k]) for k in ("queued", "skipped", "failed")}
            while position < len(repositories) and budget > 0:
                repository_id = repositories[position]
                position += 1
                budget -= 1
                try:
                    outcome = (
                        self._scanner.queue(
                            account_id,
                            repository_id,
                            key=(str(run["schedule_id"]), str(run["slot"])),
                            requested_by=f"schedule:{run['name']}"[:64],
                            schedule_id=str(run["schedule_id"]),
                        )
                        if self._scanner is not None
                        else "skipped:no_scanner"
                    )
                except (CommitGuardError, OSError) as exc:
                    log.warning(
                        "scheduled_scan_failed",
                        repository_id=repository_id,
                        error_type=type(exc).__name__,
                    )
                    outcome = "failed"
                if outcome == "queued":
                    counts["queued"] += 1
                elif outcome == "failed":
                    counts["failed"] += 1
                else:
                    counts["skipped"] += 1
                    reason = outcome.split(":", 1)[-1]
                    detail[reason] = detail.get(reason, 0) + 1
            finished = position >= len(repositories)
            state = "running"
            if finished:
                if counts["failed"] and not counts["queued"] and not counts["skipped"]:
                    state = "failed"
                elif counts["failed"]:
                    state = "partial"
                else:
                    state = "completed"
            with self._store.transaction() as db:
                db.execute(
                    "UPDATE scan_schedule_runs SET cursor = ?, queued = ?, skipped = ?, "
                    "failed = ?, "
                    "detail = ?, state = ?, completed_at = ? WHERE run_id = ?",
                    (
                        position,
                        counts["queued"],
                        counts["skipped"],
                        counts["failed"],
                        json.dumps(detail, sort_keys=True),
                        state,
                        ts(self._now()) if finished else None,
                        run["run_id"],
                    ),
                )
                if finished:
                    stored = self._store.insert_audit_event(
                        db,
                        self._audit.build(
                            AuditEventType.SCHEDULED_SCANS_QUEUED,
                            actor=SYSTEM_ACTOR,
                            account_id=account_id,
                            schedule=str(run["schedule_id"]),
                            run=str(run["run_id"]),
                            result=state,
                            repositories=len(repositories),
                            queued=counts["queued"],
                            skipped=counts["skipped"],
                            failed=counts["failed"],
                        ),
                    )
            if finished:
                self._audit.log_stored(stored)
            for key in totals:
                totals[key] += counts[key] - int(run[key])
            if budget <= 0:
                break
        return totals
