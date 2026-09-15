"""Durable state for the GitHub App.

Interfaces (so PostgreSQL or another backend can replace SQLite later):

* :class:`DeliveryRepository`     - webhook delivery IDs (replay protection);
* :class:`InstallationRepository` - installations and the repositories they grant;
* :class:`ScanRepository`         - scan jobs, their states and Check Run ownership;
* :class:`~commitguard.audit.storage.AuditStorage` - audit events.

:class:`SqliteStateStore` implements all four with the standard-library
``sqlite3`` module (no new dependency). It is safe for many threads in one
process and for several processes on one host (WAL mode, ``BEGIN IMMEDIATE``
for read-modify-write). Deployments with several hosts need a shared database
implementation of the same interfaces.

Data minimisation: the store holds IDs, repository names, commit SHAs, states,
counts and rule IDs. It never stores commit messages, author identities, file
contents, tokens or keys. Every table has a timestamp used by
:meth:`SqliteStateStore.purge_expired` for retention.

Tenant isolation: all lookups that could be exposed later take the
installation ID (and repository ID) as mandatory filters.
"""

import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from commitguard.audit.models import AuditEvent
from commitguard.audit.storage import AuditStorage
from commitguard.ci.context import CIContext
from commitguard.exceptions.service import InfrastructureError
from commitguard.github.identifiers import AccountType, RepositoryRef

SCHEMA_VERSION = 1
DATABASE_FILENAME = "commitguard-app.sqlite3"
DEFAULT_MAX_ATTEMPTS = 3


def _ts(value: datetime) -> float:
    return value.timestamp()


def _dt(value: float) -> datetime:
    return datetime.fromtimestamp(value, UTC)


def _opt_dt(value: float | None) -> datetime | None:
    return None if value is None else _dt(value)


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #
class DeliveryStatus(StrEnum):
    NEW = "new"
    DUPLICATE = "duplicate"  # same delivery ID, same payload: already handled
    CONFLICT = "conflict"  # same delivery ID, different payload: never processed


class InstallationState(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class InstallationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    installation_id: int
    account_id: int
    account_login: str
    account_type: AccountType
    repository_selection: str
    state: InstallationState
    permissions: dict[str, str] = {}
    created_at: datetime
    updated_at: datetime


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"  # scan completed, policy allowed (possibly with warnings)
    FAILED = "failed"  # scan completed, policy blocked
    ERROR = "error"  # scan could not be completed (system failure)
    CANCELLED = "cancelled"  # superseded, stale or no longer relevant

    @property
    def terminal(self) -> bool:
        return self not in (JobState.QUEUED, JobState.RUNNING)


class ScanJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    sequence: int
    job_key: str
    installation_id: int
    repository: RepositoryRef
    delivery_id: str | None
    event: str
    group_key: str
    head_sha: str
    check_name: str
    pull_request_number: int | None
    context: CIContext
    state: JobState
    attempts: int
    created_at: datetime
    updated_at: datetime
    lease_expires_at: datetime | None = None
    scan_id: str | None = None
    check_run_id: int | None = None
    result_action: str | None = None
    conclusion: str | None = None
    failure_kind: str | None = None
    message: str | None = None
    commits_scanned: int | None = None
    violations: int | None = None
    warnings: int | None = None


class NewScanJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_key: str
    installation_id: int
    repository: RepositoryRef
    delivery_id: str | None
    event: str
    group_key: str
    head_sha: str
    check_name: str
    pull_request_number: int | None
    context: CIContext


class CheckClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owned: bool
    owner_sequence: int
    check_run_id: int | None


# --------------------------------------------------------------------------- #
# Interfaces
# --------------------------------------------------------------------------- #
class DeliveryRepository(Protocol):
    def record_delivery(
        self, delivery_id: str, event: str, body_sha256: str, received_at: datetime
    ) -> DeliveryStatus: ...


class InstallationRepository(Protocol):
    def upsert_installation(self, record: InstallationRecord) -> None: ...
    def get_installation(self, installation_id: int) -> InstallationRecord | None: ...
    def set_installation_state(
        self, installation_id: int, state: InstallationState, now: datetime
    ) -> None: ...
    def replace_repositories(
        self, installation_id: int, repositories: Sequence[RepositoryRef], now: datetime
    ) -> None: ...
    def add_repositories(
        self, installation_id: int, repositories: Sequence[RepositoryRef], now: datetime
    ) -> None: ...
    def remove_repositories(self, installation_id: int, repository_ids: Sequence[int]) -> None: ...
    def repository_listed(self, installation_id: int, repository_id: int) -> bool: ...
    def list_repositories(self, installation_id: int) -> list[RepositoryRef]: ...


class ScanRepository(Protocol):
    def create_job(self, job: NewScanJob, now: datetime) -> tuple[ScanJob, bool]: ...
    def get_job(self, job_id: str) -> ScanJob | None: ...
    def list_jobs(
        self, *, installation_id: int, repository_id: int | None = None, limit: int = 100
    ) -> list[ScanJob]: ...
    def claim_job(
        self, job_id: str, now: datetime, lease_seconds: float, max_attempts: int
    ) -> ScanJob | None: ...
    def update_job(self, job_id: str, now: datetime, **fields: Any) -> None: ...
    def recoverable_jobs(
        self, now: datetime, *, queued_before: datetime, limit: int = 100
    ) -> list[str]: ...
    def latest_group_sequence(
        self, installation_id: int, repository_id: int, group_key: str
    ) -> int: ...
    def cancel_queued_group(
        self, installation_id: int, repository_id: int, group_key: str, now: datetime
    ) -> int: ...
    def claim_check(
        self,
        installation_id: int,
        repository_id: int,
        head_sha: str,
        check_name: str,
        sequence: int,
        now: datetime,
    ) -> CheckClaim: ...
    def set_check_run_id(
        self,
        installation_id: int,
        repository_id: int,
        head_sha: str,
        check_name: str,
        sequence: int,
        check_run_id: int,
    ) -> bool: ...
    def check_owner(
        self, installation_id: int, repository_id: int, head_sha: str, check_name: str
    ) -> int | None: ...


# --------------------------------------------------------------------------- #
# SQLite implementation
# --------------------------------------------------------------------------- #
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id TEXT PRIMARY KEY,
    event TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    received_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS deliveries_received ON deliveries (received_at);
CREATE TABLE IF NOT EXISTS installations (
    installation_id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    account_login TEXT NOT NULL,
    account_type TEXT NOT NULL,
    repository_selection TEXT NOT NULL,
    state TEXT NOT NULL,
    permissions TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS installation_repositories (
    installation_id INTEGER NOT NULL,
    repository_id INTEGER NOT NULL,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    added_at REAL NOT NULL,
    PRIMARY KEY (installation_id, repository_id)
);
CREATE TABLE IF NOT EXISTS scan_jobs (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE,
    job_key TEXT NOT NULL,
    installation_id INTEGER NOT NULL,
    repository_id INTEGER NOT NULL,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    delivery_id TEXT,
    event TEXT NOT NULL,
    group_key TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    check_name TEXT NOT NULL,
    pull_request_number INTEGER,
    context TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    lease_expires_at REAL,
    scan_id TEXT,
    check_run_id INTEGER,
    result_action TEXT,
    conclusion TEXT,
    failure_kind TEXT,
    message TEXT,
    commits_scanned INTEGER,
    violations INTEGER,
    warnings INTEGER
);
CREATE INDEX IF NOT EXISTS scan_jobs_key ON scan_jobs (installation_id, repository_id, job_key);
CREATE INDEX IF NOT EXISTS scan_jobs_group
    ON scan_jobs (installation_id, repository_id, group_key, sequence);
CREATE INDEX IF NOT EXISTS scan_jobs_state ON scan_jobs (state, updated_at);
CREATE TABLE IF NOT EXISTS check_runs (
    installation_id INTEGER NOT NULL,
    repository_id INTEGER NOT NULL,
    head_sha TEXT NOT NULL,
    check_name TEXT NOT NULL,
    owner_sequence INTEGER NOT NULL,
    check_run_id INTEGER,
    updated_at REAL NOT NULL,
    PRIMARY KEY (installation_id, repository_id, head_sha, check_name)
);
CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY,
    occurred_at REAL NOT NULL,
    type TEXT NOT NULL,
    installation_id INTEGER,
    repository_id INTEGER,
    document TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_tenant
    ON audit_events (installation_id, repository_id, occurred_at);
"""

# Static statements only: no SQL is ever assembled from field names at run time.
_JOB_UPDATES = {
    "state": "UPDATE scan_jobs SET state = ?, updated_at = ? WHERE job_id = ?",
    "lease_expires_at": (
        "UPDATE scan_jobs SET lease_expires_at = ?, updated_at = ? WHERE job_id = ?"
    ),
    "scan_id": "UPDATE scan_jobs SET scan_id = ?, updated_at = ? WHERE job_id = ?",
    "check_run_id": "UPDATE scan_jobs SET check_run_id = ?, updated_at = ? WHERE job_id = ?",
    "result_action": "UPDATE scan_jobs SET result_action = ?, updated_at = ? WHERE job_id = ?",
    "conclusion": "UPDATE scan_jobs SET conclusion = ?, updated_at = ? WHERE job_id = ?",
    "failure_kind": "UPDATE scan_jobs SET failure_kind = ?, updated_at = ? WHERE job_id = ?",
    "message": "UPDATE scan_jobs SET message = ?, updated_at = ? WHERE job_id = ?",
    "commits_scanned": "UPDATE scan_jobs SET commits_scanned = ?, updated_at = ? WHERE job_id = ?",
    "violations": "UPDATE scan_jobs SET violations = ?, updated_at = ? WHERE job_id = ?",
    "warnings": "UPDATE scan_jobs SET warnings = ?, updated_at = ? WHERE job_id = ?",
}


class SqliteStateStore(AuditStorage):
    """All GitHub App state in one SQLite database."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path) if str(path) != ":memory:" else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not self.path.exists():
                fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
                os.close(fd)
        self._lock = threading.RLock()
        try:
            self._db = sqlite3.connect(
                str(self.path) if self.path else ":memory:",
                check_same_thread=False,
                isolation_level=None,
                timeout=10.0,
            )
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA busy_timeout = 10000")
            if self.path is not None:
                self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.executescript(_SCHEMA)
            self._db.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            version = self._db.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.Error as exc:
            raise InfrastructureError(f"state store unavailable ({type(exc).__name__})") from None
        if version is None or version["value"] != str(SCHEMA_VERSION):
            raise InfrastructureError("state store has an unsupported schema version")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    yield self._db
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
                self._db.execute("COMMIT")
            except sqlite3.Error as exc:
                raise InfrastructureError(f"state store error ({type(exc).__name__})") from None

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                return self._db.execute(sql, params).fetchall()
            except sqlite3.Error as exc:
                raise InfrastructureError(f"state store error ({type(exc).__name__})") from None

    def ping(self) -> bool:
        try:
            self._query("SELECT 1")
        except InfrastructureError:
            return False
        return True

    # -- deliveries ----------------------------------------------------- #
    def record_delivery(
        self, delivery_id: str, event: str, body_sha256: str, received_at: datetime
    ) -> DeliveryStatus:
        with self._transaction() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO deliveries (delivery_id, event, body_sha256, received_at) "
                "VALUES (?, ?, ?, ?)",
                (delivery_id, event, body_sha256, _ts(received_at)),
            )
            if cursor.rowcount == 1:
                return DeliveryStatus.NEW
            row = db.execute(
                "SELECT event, body_sha256 FROM deliveries WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
        if row is not None and row["event"] == event and row["body_sha256"] == body_sha256:
            return DeliveryStatus.DUPLICATE
        return DeliveryStatus.CONFLICT

    # -- installations -------------------------------------------------- #
    def upsert_installation(self, record: InstallationRecord) -> None:
        with self._transaction() as db:
            db.execute(
                "INSERT INTO installations (installation_id, account_id, account_login, "
                "account_type, repository_selection, state, permissions, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (installation_id) DO UPDATE SET account_id = excluded.account_id, "
                "account_login = excluded.account_login, account_type = excluded.account_type, "
                "repository_selection = excluded.repository_selection, state = excluded.state, "
                "permissions = excluded.permissions, updated_at = excluded.updated_at",
                (
                    record.installation_id,
                    record.account_id,
                    record.account_login,
                    record.account_type.value,
                    record.repository_selection,
                    record.state.value,
                    json.dumps(record.permissions, sort_keys=True),
                    _ts(record.created_at),
                    _ts(record.updated_at),
                ),
            )

    def get_installation(self, installation_id: int) -> InstallationRecord | None:
        rows = self._query(
            "SELECT * FROM installations WHERE installation_id = ?", (int(installation_id),)
        )
        if not rows:
            return None
        row = rows[0]
        return InstallationRecord(
            installation_id=row["installation_id"],
            account_id=row["account_id"],
            account_login=row["account_login"],
            account_type=AccountType(row["account_type"]),
            repository_selection=row["repository_selection"],
            state=InstallationState(row["state"]),
            permissions=json.loads(row["permissions"]),
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    def set_installation_state(
        self, installation_id: int, state: InstallationState, now: datetime
    ) -> None:
        with self._transaction() as db:
            db.execute(
                "UPDATE installations SET state = ?, updated_at = ? WHERE installation_id = ?",
                (state.value, _ts(now), int(installation_id)),
            )
            if state is InstallationState.DELETED:
                db.execute(
                    "DELETE FROM installation_repositories WHERE installation_id = ?",
                    (int(installation_id),),
                )
                db.execute(
                    "UPDATE scan_jobs SET state = 'cancelled', message = 'installation removed', "
                    "updated_at = ? WHERE installation_id = ? AND state IN ('queued', 'running')",
                    (_ts(now), int(installation_id)),
                )

    def replace_repositories(
        self, installation_id: int, repositories: Sequence[RepositoryRef], now: datetime
    ) -> None:
        with self._transaction() as db:
            db.execute(
                "DELETE FROM installation_repositories WHERE installation_id = ?",
                (int(installation_id),),
            )
            self._insert_repositories(db, installation_id, repositories, now)

    def add_repositories(
        self, installation_id: int, repositories: Sequence[RepositoryRef], now: datetime
    ) -> None:
        with self._transaction() as db:
            self._insert_repositories(db, installation_id, repositories, now)

    @staticmethod
    def _insert_repositories(
        db: sqlite3.Connection,
        installation_id: int,
        repositories: Sequence[RepositoryRef],
        now: datetime,
    ) -> None:
        db.executemany(
            "INSERT INTO installation_repositories (installation_id, repository_id, owner, name, "
            "added_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT (installation_id, repository_id) "
            "DO UPDATE SET owner = excluded.owner, name = excluded.name",
            [(int(installation_id), r.id, r.owner, r.name, _ts(now)) for r in repositories],
        )

    def remove_repositories(self, installation_id: int, repository_ids: Sequence[int]) -> None:
        with self._transaction() as db:
            db.executemany(
                "DELETE FROM installation_repositories WHERE installation_id = ? "
                "AND repository_id = ?",
                [(int(installation_id), int(r)) for r in repository_ids],
            )
            db.executemany(
                "UPDATE scan_jobs SET state = 'cancelled', message = 'repository removed' "
                "WHERE installation_id = ? AND repository_id = ? "
                "AND state IN ('queued', 'running')",
                [(int(installation_id), int(r)) for r in repository_ids],
            )

    def repository_listed(self, installation_id: int, repository_id: int) -> bool:
        return bool(
            self._query(
                "SELECT 1 FROM installation_repositories WHERE installation_id = ? "
                "AND repository_id = ?",
                (int(installation_id), int(repository_id)),
            )
        )

    def list_repositories(self, installation_id: int) -> list[RepositoryRef]:
        rows = self._query(
            "SELECT repository_id, owner, name FROM installation_repositories "
            "WHERE installation_id = ? ORDER BY repository_id",
            (int(installation_id),),
        )
        return [
            RepositoryRef(id=r["repository_id"], owner=r["owner"], name=r["name"]) for r in rows
        ]

    # -- scan jobs ------------------------------------------------------ #
    def _job(self, row: sqlite3.Row) -> ScanJob:
        return ScanJob(
            job_id=row["job_id"],
            sequence=row["sequence"],
            job_key=row["job_key"],
            installation_id=row["installation_id"],
            repository=RepositoryRef(id=row["repository_id"], owner=row["owner"], name=row["name"]),
            delivery_id=row["delivery_id"],
            event=row["event"],
            group_key=row["group_key"],
            head_sha=row["head_sha"],
            check_name=row["check_name"],
            pull_request_number=row["pull_request_number"],
            context=CIContext.model_validate_json(row["context"]),
            state=JobState(row["state"]),
            attempts=row["attempts"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
            lease_expires_at=_opt_dt(row["lease_expires_at"]),
            scan_id=row["scan_id"],
            check_run_id=row["check_run_id"],
            result_action=row["result_action"],
            conclusion=row["conclusion"],
            failure_kind=row["failure_kind"],
            message=row["message"],
            commits_scanned=row["commits_scanned"],
            violations=row["violations"],
            warnings=row["warnings"],
        )

    def create_job(self, job: NewScanJob, now: datetime) -> tuple[ScanJob, bool]:
        """Create a job unless an equivalent one is queued, running or completed.

        An equivalent job that ended in ``error`` or ``cancelled`` does not block a
        new attempt (e.g. a pull request reopened after GitHub was unavailable).
        """
        with self._transaction() as db:
            existing = db.execute(
                "SELECT * FROM scan_jobs WHERE installation_id = ? AND repository_id = ? "
                "AND job_key = ? ORDER BY sequence DESC LIMIT 1",
                (job.installation_id, job.repository.id, job.job_key),
            ).fetchone()
            if existing is not None and existing["state"] not in ("error", "cancelled"):
                return self._job(existing), False
            job_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO scan_jobs (job_id, job_key, installation_id, repository_id, owner, "
                "name, delivery_id, event, group_key, head_sha, check_name, pull_request_number, "
                "context, state, attempts, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?)",
                (
                    job_id,
                    job.job_key,
                    job.installation_id,
                    job.repository.id,
                    job.repository.owner,
                    job.repository.name,
                    job.delivery_id,
                    job.event,
                    job.group_key,
                    job.head_sha,
                    job.check_name,
                    job.pull_request_number,
                    job.context.model_dump_json(),
                    _ts(now),
                    _ts(now),
                ),
            )
            row = db.execute("SELECT * FROM scan_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job(row), True

    def get_job(self, job_id: str) -> ScanJob | None:
        rows = self._query("SELECT * FROM scan_jobs WHERE job_id = ?", (job_id,))
        return self._job(rows[0]) if rows else None

    def list_jobs(
        self, *, installation_id: int, repository_id: int | None = None, limit: int = 100
    ) -> list[ScanJob]:
        if repository_id is None:
            rows = self._query(
                "SELECT * FROM scan_jobs WHERE installation_id = ? ORDER BY sequence DESC LIMIT ?",
                (int(installation_id), int(limit)),
            )
        else:
            rows = self._query(
                "SELECT * FROM scan_jobs WHERE installation_id = ? AND repository_id = ? "
                "ORDER BY sequence DESC LIMIT ?",
                (int(installation_id), int(repository_id), int(limit)),
            )
        return [self._job(r) for r in rows]

    def claim_job(
        self, job_id: str, now: datetime, lease_seconds: float, max_attempts: int
    ) -> ScanJob | None:
        """Atomically move a queued (or abandoned running) job to running."""
        with self._transaction() as db:
            row = db.execute("SELECT * FROM scan_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            state = row["state"]
            abandoned = state == "running" and (row["lease_expires_at"] or 0) < _ts(now)
            if state != "queued" and not abandoned:
                return None
            if row["attempts"] >= max_attempts:
                db.execute(
                    "UPDATE scan_jobs SET state = 'error', failure_kind = 'internal', "
                    "message = 'scan abandoned after repeated attempts', updated_at = ? "
                    "WHERE job_id = ?",
                    (_ts(now), job_id),
                )
                return None
            db.execute(
                "UPDATE scan_jobs SET state = 'running', attempts = attempts + 1, "
                "lease_expires_at = ?, updated_at = ? WHERE job_id = ?",
                (_ts(now) + lease_seconds, _ts(now), job_id),
            )
            row = db.execute("SELECT * FROM scan_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job(row)

    def update_job(self, job_id: str, now: datetime, **fields: Any) -> None:
        unknown = set(fields) - set(_JOB_UPDATES)
        if unknown:
            raise ValueError(f"cannot update job fields: {', '.join(sorted(unknown))}")
        with self._transaction() as db:
            for name, value in fields.items():
                if isinstance(value, StrEnum):
                    value = value.value
                db.execute(_JOB_UPDATES[name], (value, _ts(now), job_id))

    def recoverable_jobs(
        self, now: datetime, *, queued_before: datetime, limit: int = 100
    ) -> list[str]:
        """Jobs waiting since before ``queued_before``, and running jobs whose lease expired."""
        rows = self._query(
            "SELECT job_id FROM scan_jobs WHERE (state = 'queued' AND updated_at <= ?) "
            "OR (state = 'running' AND lease_expires_at < ?) ORDER BY sequence LIMIT ?",
            (_ts(queued_before), _ts(now), int(limit)),
        )
        return [r["job_id"] for r in rows]

    def latest_group_sequence(
        self, installation_id: int, repository_id: int, group_key: str
    ) -> int:
        rows = self._query(
            "SELECT MAX(sequence) AS latest FROM scan_jobs WHERE installation_id = ? "
            "AND repository_id = ? AND group_key = ?",
            (int(installation_id), int(repository_id), group_key),
        )
        return int(rows[0]["latest"] or 0)

    def cancel_queued_group(
        self, installation_id: int, repository_id: int, group_key: str, now: datetime
    ) -> int:
        with self._transaction() as db:
            cursor = db.execute(
                "UPDATE scan_jobs SET state = 'cancelled', message = 'no longer relevant', "
                "updated_at = ? WHERE installation_id = ? AND repository_id = ? "
                "AND group_key = ? AND state = 'queued'",
                (_ts(now), int(installation_id), int(repository_id), group_key),
            )
            return cursor.rowcount

    # -- check run ownership --------------------------------------------- #
    def claim_check(
        self,
        installation_id: int,
        repository_id: int,
        head_sha: str,
        check_name: str,
        sequence: int,
        now: datetime,
    ) -> CheckClaim:
        """Newer jobs take over a (repository, SHA, check name) slot; older ones lose it."""
        key = (int(installation_id), int(repository_id), head_sha, check_name)
        with self._transaction() as db:
            row = db.execute(
                "SELECT owner_sequence, check_run_id FROM check_runs WHERE installation_id = ? "
                "AND repository_id = ? AND head_sha = ? AND check_name = ?",
                key,
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO check_runs (installation_id, repository_id, head_sha, "
                    "check_name, owner_sequence, check_run_id, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, NULL, ?)",
                    (*key, sequence, _ts(now)),
                )
                return CheckClaim(owned=True, owner_sequence=sequence, check_run_id=None)
            if row["owner_sequence"] > sequence:
                return CheckClaim(
                    owned=False,
                    owner_sequence=row["owner_sequence"],
                    check_run_id=row["check_run_id"],
                )
            db.execute(
                "UPDATE check_runs SET owner_sequence = ?, updated_at = ? "
                "WHERE installation_id = ? AND repository_id = ? AND head_sha = ? "
                "AND check_name = ?",
                (sequence, _ts(now), *key),
            )
            return CheckClaim(owned=True, owner_sequence=sequence, check_run_id=row["check_run_id"])

    def set_check_run_id(
        self,
        installation_id: int,
        repository_id: int,
        head_sha: str,
        check_name: str,
        sequence: int,
        check_run_id: int,
    ) -> bool:
        with self._transaction() as db:
            cursor = db.execute(
                "UPDATE check_runs SET check_run_id = ? WHERE installation_id = ? "
                "AND repository_id = ? AND head_sha = ? AND check_name = ? AND owner_sequence = ?",
                (
                    int(check_run_id),
                    int(installation_id),
                    int(repository_id),
                    head_sha,
                    check_name,
                    sequence,
                ),
            )
            return cursor.rowcount == 1

    def check_owner(
        self, installation_id: int, repository_id: int, head_sha: str, check_name: str
    ) -> int | None:
        rows = self._query(
            "SELECT owner_sequence FROM check_runs WHERE installation_id = ? AND repository_id = ? "
            "AND head_sha = ? AND check_name = ?",
            (int(installation_id), int(repository_id), head_sha, check_name),
        )
        return int(rows[0]["owner_sequence"]) if rows else None

    # -- audit ----------------------------------------------------------- #
    def append_audit_event(self, event: AuditEvent) -> None:
        with self._transaction() as db:
            db.execute(
                "INSERT INTO audit_events (event_id, occurred_at, type, installation_id, "
                "repository_id, document) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    _ts(event.occurred_at),
                    event.type.value,
                    event.installation_id,
                    event.repository_id,
                    event.model_dump_json(),
                ),
            )

    def list_audit_events(
        self, *, installation_id: int, repository_id: int | None = None, limit: int = 100
    ) -> list[AuditEvent]:
        if repository_id is None:
            rows = self._query(
                "SELECT document FROM audit_events WHERE installation_id = ? "
                "ORDER BY occurred_at DESC LIMIT ?",
                (int(installation_id), int(limit)),
            )
        else:
            rows = self._query(
                "SELECT document FROM audit_events WHERE installation_id = ? AND repository_id = ? "
                "ORDER BY occurred_at DESC LIMIT ?",
                (int(installation_id), int(repository_id), int(limit)),
            )
        return [AuditEvent.model_validate_json(r["document"]) for r in rows]

    def purge_audit_events(self, before: datetime) -> int:
        with self._transaction() as db:
            return db.execute(
                "DELETE FROM audit_events WHERE occurred_at < ?", (_ts(before),)
            ).rowcount

    # -- retention ------------------------------------------------------- #
    def purge_expired(self, before: datetime) -> dict[str, int]:
        """Delete deliveries, finished jobs, check slots, audit events and removed
        installations older than ``before``."""
        cutoff = _ts(before)
        with self._transaction() as db:
            counts = {
                "deliveries": db.execute(
                    "DELETE FROM deliveries WHERE received_at < ?", (cutoff,)
                ).rowcount,
                "scan_jobs": db.execute(
                    "DELETE FROM scan_jobs WHERE updated_at < ? AND state NOT IN "
                    "('queued', 'running')",
                    (cutoff,),
                ).rowcount,
                "check_runs": db.execute(
                    "DELETE FROM check_runs WHERE updated_at < ?", (cutoff,)
                ).rowcount,
                "audit_events": db.execute(
                    "DELETE FROM audit_events WHERE occurred_at < ?", (cutoff,)
                ).rowcount,
                "installations": db.execute(
                    "DELETE FROM installations WHERE state = 'deleted' AND updated_at < ?",
                    (cutoff,),
                ).rowcount,
            }
        return counts
