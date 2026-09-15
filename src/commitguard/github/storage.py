"""Durable state for the GitHub App.

Interfaces (so PostgreSQL or another backend can replace SQLite later):

* :class:`DeliveryRepository`     - webhook delivery IDs (replay protection);
* :class:`InstallationRepository` - installations and the repositories they grant;
* :class:`ScanRepository`         - scan jobs, their states and Check Run ownership;
* :class:`~commitguard.audit.storage.AuditStorage` - audit events.

The dashboard's control plane (:mod:`commitguard.controlplane.store`) keeps its
tables - findings, violations, users, sessions, memberships, organisation
policy versions - in the same database, through :meth:`SqliteStateStore.transaction`
and :meth:`SqliteStateStore.query`.

Schema changes are ordered migrations (:data:`_MIGRATIONS`); a database written
by an older version is upgraded in place inside one transaction, and a database
from a newer version is refused.

:class:`SqliteStateStore` implements all four with the standard-library
``sqlite3`` module (no new dependency). It is safe for many threads in one
process and for several processes on one host (WAL mode, ``BEGIN IMMEDIATE``
for read-modify-write). Deployments with several hosts need a shared database
implementation of the same interfaces.

Data minimisation: the store holds IDs, repository names, commit SHAs, states,
counts, rule IDs and - for commits with a finding only - the finding's evidence
and the commit's author and committer identity. It never stores commit
messages, file contents, tokens or keys. Every table has a timestamp used by
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

SCHEMA_VERSION = 2
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
    base_sha: str | None = None
    ref: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    tool_version: str | None = None
    rules_version: str | None = None
    policy_version: str | None = None
    policy_source: str | None = None
    organization_policy_version: int | None = None
    effective_policies: tuple[dict[str, str | bool], ...] = ()
    findings_count: int | None = None
    detector_failures: int | None = None
    notices: tuple[str, ...] = ()
    requested_by: str | None = None


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
    requested_by: str | None = None  # dashboard login for a manual re-scan


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
    def remove_repositories(
        self, installation_id: int, repository_ids: Sequence[int], now: datetime | None = None
    ) -> None: ...
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
_SCHEMA_V1 = """
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

# Version 2: the dashboard control plane.
_SCHEMA_V2 = """
ALTER TABLE scan_jobs ADD COLUMN base_sha TEXT;
ALTER TABLE scan_jobs ADD COLUMN ref TEXT;
ALTER TABLE scan_jobs ADD COLUMN started_at REAL;
ALTER TABLE scan_jobs ADD COLUMN completed_at REAL;
ALTER TABLE scan_jobs ADD COLUMN tool_version TEXT;
ALTER TABLE scan_jobs ADD COLUMN rules_version TEXT;
ALTER TABLE scan_jobs ADD COLUMN policy_version TEXT;
ALTER TABLE scan_jobs ADD COLUMN policy_source TEXT;
ALTER TABLE scan_jobs ADD COLUMN organization_policy_version INTEGER;
ALTER TABLE scan_jobs ADD COLUMN effective_policies TEXT;
ALTER TABLE scan_jobs ADD COLUMN findings_count INTEGER;
ALTER TABLE scan_jobs ADD COLUMN detector_failures INTEGER;
ALTER TABLE scan_jobs ADD COLUMN notices TEXT;
ALTER TABLE scan_jobs ADD COLUMN requested_by TEXT;
CREATE INDEX scan_jobs_repository_sequence
    ON scan_jobs (installation_id, repository_id, sequence);
CREATE INDEX scan_jobs_installation_created ON scan_jobs (installation_id, created_at);

CREATE TABLE known_repositories (
    installation_id INTEGER NOT NULL,
    repository_id INTEGER NOT NULL,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    default_branch TEXT,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    removed_at REAL,
    PRIMARY KEY (installation_id, repository_id)
);
INSERT OR IGNORE INTO known_repositories
    (installation_id, repository_id, owner, name, first_seen_at, last_seen_at)
    SELECT installation_id, repository_id, owner, name, added_at, added_at
    FROM installation_repositories;
INSERT OR IGNORE INTO known_repositories
    (installation_id, repository_id, owner, name, first_seen_at, last_seen_at, removed_at)
    SELECT installation_id, repository_id, owner, name, MIN(created_at), MAX(updated_at),
           MAX(updated_at)
    FROM scan_jobs GROUP BY installation_id, repository_id;

ALTER TABLE audit_events ADD COLUMN account_id INTEGER;
ALTER TABLE audit_events ADD COLUMN actor_login TEXT;
UPDATE audit_events SET account_id = (
    SELECT account_id FROM installations i WHERE i.installation_id = audit_events.installation_id
);
CREATE INDEX audit_account ON audit_events (account_id, occurred_at);

CREATE TABLE findings (
    finding_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    installation_id INTEGER NOT NULL,
    repository_id INTEGER NOT NULL,
    violation_id TEXT,
    fingerprint TEXT NOT NULL,
    commit_sha TEXT,
    rule_id TEXT NOT NULL,
    detector TEXT NOT NULL,
    severity TEXT NOT NULL,
    severity_rank INTEGER NOT NULL,
    confidence TEXT NOT NULL,
    action TEXT NOT NULL,
    policy_id TEXT,
    reason TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    remediation TEXT NOT NULL,
    evidence TEXT NOT NULL,
    author TEXT,
    committer TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX findings_job ON findings (job_id, finding_id);
CREATE INDEX findings_violation ON findings (violation_id, finding_id);
CREATE INDEX findings_rule ON findings (installation_id, rule_id, job_id);
CREATE INDEX findings_created ON findings (created_at);

CREATE TABLE violations (
    violation_id TEXT PRIMARY KEY,
    installation_id INTEGER NOT NULL,
    repository_id INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    detector TEXT NOT NULL,
    severity TEXT NOT NULL,
    severity_rank INTEGER NOT NULL,
    action TEXT NOT NULL,
    title TEXT NOT NULL,
    commit_sha TEXT,
    author TEXT,
    status TEXT NOT NULL,
    first_detected_at REAL NOT NULL,
    last_detected_at REAL NOT NULL,
    first_job_id TEXT NOT NULL,
    last_job_id TEXT NOT NULL,
    detections INTEGER NOT NULL,
    resolved_at REAL,
    resolution TEXT,
    acknowledged_at REAL,
    acknowledged_by_id INTEGER,
    acknowledged_by_login TEXT,
    acknowledgement_note TEXT,
    updated_at REAL NOT NULL,
    UNIQUE (installation_id, repository_id, fingerprint)
);
CREATE INDEX violations_detected ON violations (installation_id, last_detected_at);
CREATE INDEX violations_repository ON violations (installation_id, repository_id, status);
CREATE INDEX violations_status ON violations (installation_id, status, severity_rank);
CREATE INDEX violations_rule ON violations (installation_id, rule_id);

CREATE TABLE violation_exposures (
    violation_id TEXT NOT NULL,
    installation_id INTEGER NOT NULL,
    repository_id INTEGER NOT NULL,
    group_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    active INTEGER NOT NULL,
    first_job_id TEXT,
    last_job_id TEXT,
    opened_at REAL NOT NULL,
    closed_at REAL,
    closed_reason TEXT,
    PRIMARY KEY (violation_id, group_key)
);
CREATE INDEX exposures_group
    ON violation_exposures (installation_id, repository_id, group_key, active);

CREATE TABLE users (
    user_id INTEGER PRIMARY KEY,
    login TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_login_at REAL
);
CREATE TABLE memberships (
    account_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (account_id, user_id)
);
CREATE INDEX memberships_user ON memberships (user_id);
CREATE TABLE sessions (
    session_hash TEXT PRIMARY KEY,
    public_id TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    created_at REAL NOT NULL,
    authenticated_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    user_agent TEXT NOT NULL
);
CREATE INDEX sessions_user ON sessions (user_id);
CREATE INDEX sessions_expiry ON sessions (expires_at);
CREATE TABLE session_installations (
    session_hash TEXT NOT NULL REFERENCES sessions (session_hash) ON DELETE CASCADE,
    installation_id INTEGER NOT NULL,
    PRIMARY KEY (session_hash, installation_id)
);
CREATE TABLE session_repositories (
    session_hash TEXT NOT NULL REFERENCES sessions (session_hash) ON DELETE CASCADE,
    installation_id INTEGER NOT NULL,
    repository_id INTEGER NOT NULL,
    PRIMARY KEY (session_hash, installation_id, repository_id)
);
CREATE TABLE oauth_states (
    state_hash TEXT PRIMARY KEY,
    verifier TEXT NOT NULL,
    return_to TEXT NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE organization_policy_versions (
    account_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    document TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at REAL NOT NULL,
    created_by_id INTEGER,
    created_by_login TEXT,
    reason TEXT,
    PRIMARY KEY (account_id, version)
);

CREATE TABLE repository_settings (
    installation_id INTEGER NOT NULL,
    repository_id INTEGER NOT NULL,
    monitoring_enabled INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    updated_by_login TEXT,
    PRIMARY KEY (installation_id, repository_id)
);
CREATE TABLE enforcement_status (
    installation_id INTEGER NOT NULL,
    repository_id INTEGER NOT NULL,
    checked_at REAL NOT NULL,
    branch TEXT,
    actions TEXT NOT NULL,
    actions_detail TEXT NOT NULL,
    branch_protection TEXT NOT NULL,
    required_checks TEXT NOT NULL,
    branch_protection_detail TEXT NOT NULL,
    PRIMARY KEY (installation_id, repository_id)
);
"""

_MIGRATIONS: tuple[str, ...] = (_SCHEMA_V1, _SCHEMA_V2)


_ORPHAN_DELETES = (
    "DELETE FROM violation_exposures WHERE installation_id NOT IN "
    "(SELECT installation_id FROM installations)",
    "DELETE FROM violations WHERE installation_id NOT IN "
    "(SELECT installation_id FROM installations)",
    "DELETE FROM findings WHERE installation_id NOT IN "
    "(SELECT installation_id FROM installations)",
    "DELETE FROM repository_settings WHERE installation_id NOT IN "
    "(SELECT installation_id FROM installations)",
    "DELETE FROM enforcement_status WHERE installation_id NOT IN "
    "(SELECT installation_id FROM installations)",
)


def _statements(script: str) -> list[str]:
    """Split a migration into statements (migrations contain no string literals with ';')."""
    return [part.strip() for part in script.split(";") if part.strip()]


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
    "base_sha": "UPDATE scan_jobs SET base_sha = ?, updated_at = ? WHERE job_id = ?",
    "completed_at": "UPDATE scan_jobs SET completed_at = ?, updated_at = ? WHERE job_id = ?",
    "tool_version": "UPDATE scan_jobs SET tool_version = ?, updated_at = ? WHERE job_id = ?",
    "rules_version": "UPDATE scan_jobs SET rules_version = ?, updated_at = ? WHERE job_id = ?",
    "policy_version": "UPDATE scan_jobs SET policy_version = ?, updated_at = ? WHERE job_id = ?",
    "policy_source": "UPDATE scan_jobs SET policy_source = ?, updated_at = ? WHERE job_id = ?",
    "organization_policy_version": (
        "UPDATE scan_jobs SET organization_policy_version = ?, updated_at = ? WHERE job_id = ?"
    ),
    "effective_policies": (
        "UPDATE scan_jobs SET effective_policies = ?, updated_at = ? WHERE job_id = ?"
    ),
    "findings_count": "UPDATE scan_jobs SET findings_count = ?, updated_at = ? WHERE job_id = ?",
    "detector_failures": (
        "UPDATE scan_jobs SET detector_failures = ?, updated_at = ? WHERE job_id = ?"
    ),
    "notices": "UPDATE scan_jobs SET notices = ?, updated_at = ? WHERE job_id = ?",
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
            self._migrate()
        except sqlite3.Error as exc:
            raise InfrastructureError(f"state store unavailable ({type(exc).__name__})") from None

    def _migrate(self) -> None:
        """Apply pending migrations in one transaction; refuse unknown newer schemas."""
        db = self._db
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            current = int(row["value"]) if row is not None and row["value"].isdigit() else 0
            if row is not None and not row["value"].isdigit():
                raise InfrastructureError("state store has an unsupported schema version")
            if current > SCHEMA_VERSION:
                raise InfrastructureError("state store has an unsupported schema version")
            for version in range(current + 1, SCHEMA_VERSION + 1):
                for statement in _statements(_MIGRATIONS[version - 1]):
                    db.execute(statement)
            db.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """A write transaction (``BEGIN IMMEDIATE``) for multi-statement changes."""
        with self._transaction() as db:
            yield db

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Run a read-only, parameterised statement."""
        return self._query(sql, params)

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
                    "UPDATE known_repositories SET removed_at = ? "
                    "WHERE installation_id = ? AND removed_at IS NULL",
                    (_ts(now), int(installation_id)),
                )
                db.execute(
                    "UPDATE scan_jobs SET state = 'cancelled', message = 'installation removed', "
                    "updated_at = ?, completed_at = ? WHERE installation_id = ? "
                    "AND state IN ('queued', 'running')",
                    (_ts(now), _ts(now), int(installation_id)),
                )

    def replace_repositories(
        self, installation_id: int, repositories: Sequence[RepositoryRef], now: datetime
    ) -> None:
        with self._transaction() as db:
            db.execute(
                "DELETE FROM installation_repositories WHERE installation_id = ?",
                (int(installation_id),),
            )
            db.execute(
                "UPDATE known_repositories SET removed_at = ? "
                "WHERE installation_id = ? AND removed_at IS NULL",
                (_ts(now), int(installation_id)),
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
        db.executemany(
            "INSERT INTO known_repositories (installation_id, repository_id, owner, name, "
            "first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (installation_id, repository_id) DO UPDATE SET owner = excluded.owner, "
            "name = excluded.name, last_seen_at = excluded.last_seen_at, removed_at = NULL",
            [
                (int(installation_id), r.id, r.owner, r.name, _ts(now), _ts(now))
                for r in repositories
            ],
        )

    def remove_repositories(
        self, installation_id: int, repository_ids: Sequence[int], now: datetime | None = None
    ) -> None:
        removed_at = _ts(now or datetime.now(UTC))
        with self._transaction() as db:
            db.executemany(
                "UPDATE known_repositories SET removed_at = ? WHERE installation_id = ? "
                "AND repository_id = ?",
                [(removed_at, int(installation_id), int(r)) for r in repository_ids],
            )
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

    def set_default_branch(
        self, installation_id: int, repository_id: int, default_branch: str | None
    ) -> None:
        with self._transaction() as db:
            db.execute(
                "UPDATE known_repositories SET default_branch = ? WHERE installation_id = ? "
                "AND repository_id = ?",
                (default_branch, int(installation_id), int(repository_id)),
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
            base_sha=row["base_sha"],
            ref=row["ref"],
            started_at=_opt_dt(row["started_at"]),
            completed_at=_opt_dt(row["completed_at"]),
            tool_version=row["tool_version"],
            rules_version=row["rules_version"],
            policy_version=row["policy_version"],
            policy_source=row["policy_source"],
            organization_policy_version=row["organization_policy_version"],
            effective_policies=tuple(json.loads(row["effective_policies"] or "[]")),
            findings_count=row["findings_count"],
            detector_failures=row["detector_failures"],
            notices=tuple(json.loads(row["notices"] or "[]")),
            requested_by=row["requested_by"],
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
            db.execute(
                "INSERT INTO known_repositories (installation_id, repository_id, owner, name, "
                "first_seen_at, last_seen_at, removed_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (installation_id, repository_id) DO UPDATE SET "
                "last_seen_at = excluded.last_seen_at",
                (
                    job.installation_id,
                    job.repository.id,
                    job.repository.owner,
                    job.repository.name,
                    _ts(now),
                    _ts(now),
                    None,
                ),
            )
            job_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO scan_jobs (job_id, job_key, installation_id, repository_id, owner, "
                "name, delivery_id, event, group_key, head_sha, check_name, pull_request_number, "
                "context, state, attempts, created_at, updated_at, base_sha, ref, requested_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?)",
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
                    job.context.base_sha or job.context.before_sha,
                    job.context.ref,
                    job.requested_by,
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
                    "message = 'scan abandoned after repeated attempts', updated_at = ?, "
                    "completed_at = ? WHERE job_id = ?",
                    (_ts(now), _ts(now), job_id),
                )
                return None
            db.execute(
                "UPDATE scan_jobs SET state = 'running', attempts = attempts + 1, "
                "lease_expires_at = ?, updated_at = ?, started_at = ? WHERE job_id = ?",
                (_ts(now) + lease_seconds, _ts(now), _ts(now), job_id),
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
            self.insert_audit_event(db, event)

    @staticmethod
    def insert_audit_event(db: sqlite3.Connection, event: AuditEvent) -> AuditEvent:
        """Store ``event`` inside the caller's transaction.

        The tenant (``account_id``) is taken from the installation when the event
        does not name one, so every event about an installation is visible to -
        and only to - that account.
        """
        if event.account_id is None and event.installation_id is not None:
            row = db.execute(
                "SELECT account_id FROM installations WHERE installation_id = ?",
                (event.installation_id,),
            ).fetchone()
            if row is not None:
                event = event.model_copy(update={"account_id": row["account_id"]})
        db.execute(
            "INSERT INTO audit_events (event_id, occurred_at, type, installation_id, "
            "repository_id, account_id, actor_login, document) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                _ts(event.occurred_at),
                event.type.value,
                event.installation_id,
                event.repository_id,
                event.account_id,
                event.actor_login,
                event.model_dump_json(),
            ),
        )
        return event

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
            # Control plane: history older than the cutoff, except anything an open
            # violation still depends on.
            findings = db.execute(
                "DELETE FROM findings WHERE created_at < ? AND (violation_id IS NULL OR "
                "violation_id NOT IN (SELECT violation_id FROM violations WHERE status = 'open'))",
                (cutoff,),
            ).rowcount
            db.execute(
                "DELETE FROM violation_exposures WHERE violation_id IN (SELECT violation_id FROM "
                "violations WHERE status = 'resolved' AND updated_at < ?)",
                (cutoff,),
            )
            violations = db.execute(
                "DELETE FROM violations WHERE status = 'resolved' AND updated_at < ?", (cutoff,)
            ).rowcount
            counts = {
                "findings": findings,
                "violations": violations,
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
            # Data whose installation record is gone can no longer be attributed to a
            # tenant: remove it rather than keep it unreachable.
            for statement in _ORPHAN_DELETES:
                db.execute(statement)
            counts["repositories"] = db.execute(
                "DELETE FROM known_repositories WHERE removed_at IS NOT NULL AND removed_at < ? "
                "AND NOT EXISTS (SELECT 1 FROM scan_jobs j WHERE j.installation_id = "
                "known_repositories.installation_id AND j.repository_id = "
                "known_repositories.repository_id)",
                (cutoff,),
            ).rowcount
        return counts
