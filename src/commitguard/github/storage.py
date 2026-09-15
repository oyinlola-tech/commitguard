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
import re
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

SCHEMA_VERSION = 4
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
    RETRY = "retry"  # same delivery ID and payload, earlier processing failed: process again


class EventProcessingStatus(StrEnum):
    """Processing state of a received webhook delivery (the event record)."""

    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    IGNORED = "ignored"


#: A delivery still "processing" after this long is treated as abandoned (process crash).
STUCK_DELIVERY_SECONDS = 600.0


class ScanTrigger(StrEnum):
    """Why a scan execution exists."""

    PUSH = "push"
    PULL_REQUEST = "pull_request"
    MERGE_GROUP = "merge_group"
    MANUAL = "manual"  # dashboard "Scan again"
    RERUN = "rerun"  # GitHub "Re-run" on the CommitGuard check
    RETRY = "retry"  # automatic recovery after an infrastructure failure


class MergeGroupState(StrEnum):
    CHECKS_REQUESTED = "checks_requested"
    DESTROYED = "destroyed"


class MergeGroupRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    installation_id: int
    repository_id: int
    head_sha: str
    head_ref: str
    base_sha: str
    base_ref: str
    pull_requests: tuple[int, ...]
    state: MergeGroupState
    destroyed_reason: str | None
    job_id: str | None
    created_at: datetime
    updated_at: datetime
    destroyed_at: datetime | None


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
    scan_key: str = ""
    execution: int = 1
    trigger: ScanTrigger = ScanTrigger.PUSH
    previous_job_id: str | None = None


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

    @property
    def trigger(self) -> ScanTrigger:
        return ScanTrigger(self.event)


class ClaimOutcome(BaseModel):
    """Result of claiming a job: the running job, or the job that ran out of attempts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job: ScanJob | None = None
    exhausted: ScanJob | None = None


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

# Version 3: scan executions, event processing, merge queue, policy history, notifications.
_SCHEMA_V3 = """
ALTER TABLE scan_jobs ADD COLUMN scan_key TEXT;
ALTER TABLE scan_jobs ADD COLUMN execution INTEGER NOT NULL DEFAULT 1;
ALTER TABLE scan_jobs ADD COLUMN trigger_kind TEXT;
ALTER TABLE scan_jobs ADD COLUMN previous_job_id TEXT;
UPDATE scan_jobs SET scan_key = job_key, trigger_kind = event;
UPDATE scan_jobs SET trigger_kind = 'manual', scan_key = COALESCE((
    SELECT o.job_key FROM scan_jobs o
    WHERE o.installation_id = scan_jobs.installation_id
      AND o.repository_id = scan_jobs.repository_id AND o.group_key = scan_jobs.group_key
      AND o.head_sha = scan_jobs.head_sha AND o.check_name = scan_jobs.check_name
      AND o.requested_by IS NULL
    ORDER BY o.sequence LIMIT 1), job_key)
    WHERE requested_by IS NOT NULL;
UPDATE scan_jobs SET execution = (
    SELECT COUNT(*) FROM scan_jobs o
    WHERE o.installation_id = scan_jobs.installation_id
      AND o.repository_id = scan_jobs.repository_id AND o.scan_key = scan_jobs.scan_key
      AND o.sequence <= scan_jobs.sequence);
CREATE INDEX scan_jobs_scan_key ON scan_jobs (installation_id, repository_id, scan_key, sequence);
CREATE INDEX scan_jobs_head ON scan_jobs (installation_id, repository_id, head_sha);
CREATE UNIQUE INDEX scan_jobs_rerun_event
    ON scan_jobs (installation_id, repository_id, scan_key, delivery_id)
    WHERE delivery_id IS NOT NULL AND trigger_kind = 'rerun';

ALTER TABLE deliveries ADD COLUMN provider TEXT NOT NULL DEFAULT 'github';
ALTER TABLE deliveries ADD COLUMN action TEXT;
ALTER TABLE deliveries ADD COLUMN status TEXT NOT NULL DEFAULT 'processed';
ALTER TABLE deliveries ADD COLUMN installation_id INTEGER;
ALTER TABLE deliveries ADD COLUMN repository_id INTEGER;
ALTER TABLE deliveries ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1;
ALTER TABLE deliveries ADD COLUMN updated_at REAL;
ALTER TABLE deliveries ADD COLUMN detail TEXT;
UPDATE deliveries SET updated_at = received_at;
CREATE UNIQUE INDEX deliveries_provider_event ON deliveries (provider, delivery_id);
CREATE INDEX deliveries_status ON deliveries (status, updated_at);

CREATE TABLE merge_groups (
    installation_id INTEGER NOT NULL,
    repository_id INTEGER NOT NULL,
    head_sha TEXT NOT NULL,
    head_ref TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    base_ref TEXT NOT NULL,
    pull_requests TEXT NOT NULL,
    state TEXT NOT NULL,
    destroyed_reason TEXT,
    job_id TEXT,
    delivery_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    destroyed_at REAL,
    PRIMARY KEY (installation_id, repository_id, head_sha)
);
CREATE INDEX merge_groups_recent ON merge_groups (installation_id, repository_id, created_at);
ALTER TABLE enforcement_status ADD COLUMN merge_queue TEXT;
ALTER TABLE enforcement_status ADD COLUMN merge_queue_detail TEXT;

ALTER TABLE organization_policy_versions ADD COLUMN kind TEXT NOT NULL DEFAULT 'change';
ALTER TABLE organization_policy_versions ADD COLUMN rollback_of INTEGER;
ALTER TABLE organization_policy_versions ADD COLUMN restored_version INTEGER;
CREATE TRIGGER organization_policy_versions_immutable
    BEFORE UPDATE ON organization_policy_versions
    BEGIN SELECT RAISE(ABORT, 'policy versions are immutable'); END;
CREATE TRIGGER organization_policy_versions_permanent
    BEFORE DELETE ON organization_policy_versions
    BEGIN SELECT RAISE(ABORT, 'policy versions are immutable'); END;

CREATE TABLE notification_events (
    event_id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    severity TEXT NOT NULL,
    installation_id INTEGER,
    repository_id INTEGER,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    metadata TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    last_occurred_at REAL NOT NULL,
    dispatched_at REAL,
    request_id TEXT,
    delivery_id TEXT,
    job_id TEXT,
    UNIQUE (account_id, dedup_key)
);
CREATE INDEX notification_events_outbox ON notification_events (dispatched_at, created_at);
CREATE INDEX notification_events_created ON notification_events (created_at);

CREATE TABLE notifications (
    notification_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES notification_events (event_id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    state TEXT NOT NULL,
    severity TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    sort_at REAL NOT NULL,
    read_at REAL,
    archived_at REAL,
    UNIQUE (event_id, user_id)
);
CREATE INDEX notifications_user ON notifications (user_id, sort_at);
CREATE INDEX notifications_user_state ON notifications (user_id, state, sort_at);
CREATE INDEX notifications_event ON notifications (event_id);
CREATE INDEX notifications_user_severity ON notifications (user_id, severity, state);

CREATE TABLE notification_deliveries (
    delivery_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES notification_events (event_id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    destination TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    provider TEXT NOT NULL,
    provider_message_id TEXT,
    failure_code TEXT,
    last_attempt_at REAL,
    next_retry_at REAL,
    lease_expires_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX notification_deliveries_due ON notification_deliveries (status, next_retry_at);
CREATE INDEX notification_deliveries_account ON notification_deliveries (account_id, created_at);

CREATE TABLE notification_settings (
    account_id INTEGER PRIMARY KEY,
    version INTEGER NOT NULL,
    document TEXT NOT NULL,
    email_recipients TEXT NOT NULL,
    updated_at REAL NOT NULL,
    updated_by_login TEXT
);
CREATE TABLE notification_user_preferences (
    user_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    in_app INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, account_id, type)
);
CREATE TABLE notification_webhooks (
    endpoint_id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    created_at REAL NOT NULL,
    created_by_login TEXT,
    removed_at REAL
);
CREATE INDEX notification_webhooks_account ON notification_webhooks (account_id, removed_at);
"""

# Version 4: organization governance (Phase 8). Governance rows are keyed by the
# account (tenant) and GitHub's repository ID, which survives a reinstallation.
_SCHEMA_V4 = """
ALTER TABLE known_repositories ADD COLUMN private INTEGER;
ALTER TABLE known_repositories ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scan_jobs ADD COLUMN governance TEXT;
ALTER TABLE scan_jobs ADD COLUMN governance_fingerprint TEXT;
ALTER TABLE scan_jobs ADD COLUMN repository_policies TEXT;
ALTER TABLE scan_jobs ADD COLUMN schedule_id TEXT;
CREATE INDEX scan_jobs_repository_completed ON scan_jobs (repository_id, completed_at);
CREATE TRIGGER audit_events_immutable
    BEFORE UPDATE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit events are immutable'); END;

CREATE TABLE organization_settings (
    account_id INTEGER PRIMARY KEY,
    version INTEGER NOT NULL,
    document TEXT NOT NULL,
    updated_at REAL NOT NULL,
    updated_by_id INTEGER,
    updated_by_login TEXT
);

CREATE TABLE repository_governance (
    account_id INTEGER NOT NULL,
    repository_id INTEGER NOT NULL,
    onboarding TEXT NOT NULL CHECK (onboarding IN ('discovered', 'onboarded', 'excluded')),
    mode TEXT NOT NULL CHECK (mode IN ('monitor', 'enforce')),
    discovered_at REAL NOT NULL,
    onboarded_at REAL,
    onboarded_by TEXT,
    mode_changed_at REAL,
    mode_changed_by TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (account_id, repository_id)
);
CREATE INDEX repository_governance_state ON repository_governance (account_id, onboarding, mode);
INSERT OR IGNORE INTO repository_governance
    (account_id, repository_id, onboarding, mode, discovered_at, onboarded_at, onboarded_by,
     updated_at)
    SELECT i.account_id, k.repository_id, 'onboarded', 'enforce', MIN(k.first_seen_at),
           MIN(k.first_seen_at), 'migration', MIN(k.first_seen_at)
    FROM known_repositories k JOIN installations i ON i.installation_id = k.installation_id
    GROUP BY i.account_id, k.repository_id;

CREATE TABLE repository_groups (
    group_id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    description TEXT,
    created_at REAL NOT NULL,
    created_by TEXT,
    updated_at REAL NOT NULL,
    archived_at REAL,
    archived_by TEXT
);
CREATE UNIQUE INDEX repository_groups_name
    ON repository_groups (account_id, name_key) WHERE archived_at IS NULL;
CREATE UNIQUE INDEX repository_groups_tenant ON repository_groups (group_id, account_id);
CREATE TABLE repository_group_members (
    group_id TEXT NOT NULL,
    account_id INTEGER NOT NULL,
    repository_id INTEGER NOT NULL,
    added_at REAL NOT NULL,
    added_by TEXT,
    PRIMARY KEY (group_id, repository_id),
    FOREIGN KEY (group_id, account_id) REFERENCES repository_groups (group_id, account_id)
);
CREATE INDEX repository_group_members_repository
    ON repository_group_members (account_id, repository_id);

ALTER TABLE organization_policy_versions ADD COLUMN draft_id TEXT;
ALTER TABLE organization_policy_versions ADD COLUMN emergency INTEGER NOT NULL DEFAULT 0;
CREATE TABLE scoped_policy_versions (
    account_id INTEGER NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('group', 'repository')),
    target_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    document TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at REAL NOT NULL,
    created_by_id INTEGER,
    created_by_login TEXT,
    reason TEXT,
    kind TEXT NOT NULL DEFAULT 'change',
    rollback_of INTEGER,
    restored_version INTEGER,
    draft_id TEXT,
    emergency INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, target_type, target_id, version)
);
CREATE TRIGGER scoped_policy_versions_immutable
    BEFORE UPDATE ON scoped_policy_versions
    BEGIN SELECT RAISE(ABORT, 'policy versions are immutable'); END;
CREATE TRIGGER scoped_policy_versions_permanent
    BEFORE DELETE ON scoped_policy_versions
    BEGIN SELECT RAISE(ABORT, 'policy versions are immutable'); END;

CREATE TABLE policy_drafts (
    draft_id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('organization', 'group', 'repository')),
    target_id TEXT NOT NULL,
    base_version INTEGER NOT NULL,
    document TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    title TEXT NOT NULL,
    reason TEXT,
    state TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    created_by_id INTEGER,
    created_by_login TEXT,
    updated_at REAL NOT NULL,
    submitted_at REAL,
    submitted_by_id INTEGER,
    submitted_by_login TEXT,
    published_version INTEGER,
    published_at REAL,
    published_by_login TEXT,
    emergency INTEGER NOT NULL DEFAULT 0,
    rollout_id TEXT
);
CREATE INDEX policy_drafts_account ON policy_drafts (account_id, state, updated_at);
CREATE TABLE policy_approvals (
    approval_id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL REFERENCES policy_drafts (draft_id),
    account_id INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    requested_by_id INTEGER,
    requested_by_login TEXT,
    requested_at REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
    decided_by_id INTEGER,
    decided_by_login TEXT,
    decided_at REAL,
    reason TEXT
);
CREATE UNIQUE INDEX policy_approvals_pending
    ON policy_approvals (draft_id) WHERE status = 'pending';
CREATE INDEX policy_approvals_account ON policy_approvals (account_id, status, requested_at);

CREATE TABLE policy_exceptions (
    exception_id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    rule_id TEXT NOT NULL,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('organization', 'group', 'repository')),
    scope_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('warn', 'allow')),
    severity TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('requested', 'active', 'rejected', 'cancelled', 'revoked', 'expired')),
    requires_approval INTEGER NOT NULL,
    permanent INTEGER NOT NULL DEFAULT 0,
    expires_at REAL,
    requested_at REAL NOT NULL,
    requested_by_id INTEGER,
    requested_by_login TEXT,
    decided_at REAL,
    decided_by_id INTEGER,
    decided_by_login TEXT,
    decision_note TEXT,
    activated_at REAL,
    revoked_at REAL,
    revoked_by_login TEXT,
    revoke_reason TEXT,
    expired_at REAL,
    warnings_sent TEXT NOT NULL DEFAULT '[]',
    updated_at REAL NOT NULL,
    CHECK (permanent = 1 OR expires_at IS NOT NULL)
);
CREATE INDEX policy_exceptions_status ON policy_exceptions (account_id, status, expires_at);
CREATE INDEX policy_exceptions_due ON policy_exceptions (status, expires_at);
CREATE UNIQUE INDEX policy_exceptions_open
    ON policy_exceptions (account_id, rule_id, scope_type, scope_id)
    WHERE status IN ('requested', 'active');
CREATE TRIGGER policy_exceptions_permanent
    BEFORE DELETE ON policy_exceptions
    BEGIN SELECT RAISE(ABORT, 'policy exceptions are kept as history'); END;

CREATE TABLE policy_rollouts (
    rollout_id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('organization', 'group')),
    target_id TEXT NOT NULL,
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pilot', 'rollout', 'paused', 'active', 'rolled_back')),
    stages TEXT NOT NULL,
    current_stage INTEGER NOT NULL,
    thresholds TEXT NOT NULL,
    auto_pause INTEGER NOT NULL,
    auto_rollback INTEGER NOT NULL,
    created_at REAL NOT NULL,
    created_by_id INTEGER,
    created_by_login TEXT,
    updated_at REAL NOT NULL,
    stage_started_at REAL NOT NULL,
    paused_at REAL,
    paused_reason TEXT,
    paused_from TEXT,
    completed_at REAL,
    rolled_back_at REAL,
    rollback_version INTEGER
);
CREATE UNIQUE INDEX policy_rollouts_in_progress
    ON policy_rollouts (account_id, target_type, target_id)
    WHERE state IN ('pilot', 'rollout', 'paused');
CREATE UNIQUE INDEX policy_rollouts_version
    ON policy_rollouts (account_id, target_type, target_id, to_version);
CREATE TABLE policy_rollout_repositories (
    rollout_id TEXT NOT NULL REFERENCES policy_rollouts (rollout_id),
    repository_id INTEGER NOT NULL,
    stage INTEGER NOT NULL,
    enrolled_at REAL NOT NULL,
    PRIMARY KEY (rollout_id, repository_id)
);

CREATE TABLE policy_simulations (
    simulation_id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    draft_id TEXT,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    current_version INTEGER NOT NULL,
    document TEXT NOT NULL,
    parameters TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'completed', 'failed')),
    requested_by_id INTEGER,
    requested_by_login TEXT,
    requested_at REAL NOT NULL,
    started_at REAL,
    completed_at REAL,
    lease_expires_at REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    result TEXT,
    error TEXT
);
CREATE INDEX policy_simulations_account ON policy_simulations (account_id, requested_at);
CREATE INDEX policy_simulations_state ON policy_simulations (state, requested_at);

CREATE TABLE bulk_operations (
    operation_id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    parameters TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    requested_by_id INTEGER,
    requested_by_login TEXT,
    created_at REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('queued', 'running', 'completed', 'partial', 'failed', 'cancelled')),
    total INTEGER NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    started_at REAL,
    completed_at REAL,
    updated_at REAL NOT NULL,
    lease_expires_at REAL,
    cancelled_by_login TEXT
);
CREATE UNIQUE INDEX bulk_operations_idempotency ON bulk_operations (account_id, idempotency_key);
CREATE INDEX bulk_operations_status ON bulk_operations (status, created_at);
CREATE INDEX bulk_operations_account ON bulk_operations (account_id, created_at);
CREATE TABLE bulk_operation_items (
    operation_id TEXT NOT NULL REFERENCES bulk_operations (operation_id),
    repository_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('pending', 'completed', 'failed', 'skipped', 'cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    detail TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (operation_id, repository_id)
);
CREATE INDEX bulk_operation_items_status ON bulk_operation_items (operation_id, status);

CREATE TABLE scan_schedules (
    schedule_id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('organization', 'group', 'repository')),
    target_id TEXT NOT NULL,
    name TEXT NOT NULL,
    cadence TEXT NOT NULL CHECK (cadence IN ('daily', 'weekly')),
    hour INTEGER NOT NULL,
    minute INTEGER NOT NULL,
    weekday INTEGER,
    timezone TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    next_run_at REAL,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    created_by_login TEXT,
    updated_at REAL NOT NULL,
    updated_by_login TEXT
);
CREATE INDEX scan_schedules_due ON scan_schedules (enabled, next_run_at);
CREATE INDEX scan_schedules_account ON scan_schedules (account_id, created_at);
CREATE TABLE scan_schedule_runs (
    run_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL REFERENCES scan_schedules (schedule_id),
    account_id INTEGER NOT NULL,
    slot REAL NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('running', 'completed', 'partial', 'failed')),
    started_at REAL NOT NULL,
    completed_at REAL,
    repositories INTEGER NOT NULL DEFAULT 0,
    queued INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    cursor INTEGER NOT NULL DEFAULT 0,
    detail TEXT,
    UNIQUE (schedule_id, slot)
);
CREATE INDEX scan_schedule_runs_state ON scan_schedule_runs (state, started_at);

CREATE TABLE repository_effective_policies (
    account_id INTEGER NOT NULL,
    repository_id INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('up_to_date', 'stale', 'syncing', 'error')),
    fingerprint TEXT,
    document TEXT,
    computed_at REAL,
    invalidated_at REAL,
    valid_until REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    PRIMARY KEY (account_id, repository_id)
);
CREATE INDEX repository_effective_policies_state
    ON repository_effective_policies (state, invalidated_at);

CREATE TABLE organization_rule_versions (
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
CREATE TRIGGER organization_rule_versions_immutable
    BEFORE UPDATE ON organization_rule_versions
    BEGIN SELECT RAISE(ABORT, 'rule versions are immutable'); END;
CREATE TRIGGER organization_rule_versions_permanent
    BEFORE DELETE ON organization_rule_versions
    BEGIN SELECT RAISE(ABORT, 'rule versions are immutable'); END;

CREATE TABLE installation_sync_status (
    installation_id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('healthy', 'syncing', 'degraded', 'failed')),
    started_at REAL,
    completed_at REAL,
    last_success_at REAL,
    repositories INTEGER,
    added INTEGER,
    removed INTEGER,
    error TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE security_metric_snapshots (
    account_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    computed_at REAL NOT NULL,
    document TEXT NOT NULL,
    PRIMARY KEY (account_id, day)
);

CREATE TABLE notification_acknowledgements (
    event_id TEXT PRIMARY KEY REFERENCES notification_events (event_id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL,
    acknowledged_by_id INTEGER NOT NULL,
    acknowledged_by_login TEXT NOT NULL,
    acknowledged_at REAL NOT NULL,
    note TEXT
);
"""

_MIGRATIONS: tuple[str, ...] = (_SCHEMA_V1, _SCHEMA_V2, _SCHEMA_V3, _SCHEMA_V4)


_ORPHAN_DELETES = (
    "DELETE FROM violation_exposures WHERE installation_id NOT IN "
    "(SELECT installation_id FROM installations)",
    "DELETE FROM violations WHERE installation_id NOT IN "
    "(SELECT installation_id FROM installations)",
    "DELETE FROM findings WHERE installation_id NOT IN (SELECT installation_id FROM installations)",
    "DELETE FROM repository_settings WHERE installation_id NOT IN "
    "(SELECT installation_id FROM installations)",
    "DELETE FROM enforcement_status WHERE installation_id NOT IN "
    "(SELECT installation_id FROM installations)",
)


def _statements(script: str) -> list[str]:
    """Split a migration into statements.

    A statement ends with ``;`` at the end of a line, so a trigger body whose
    statements end mid-line (``BEGIN SELECT ...; END;``) stays one statement.
    Migrations contain no string literals with a line-final ``;``.
    """
    return [part.strip() for part in re.split(r";[ \t]*(?:\n|\Z)", script) if part.strip()]


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
            db.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
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
        self,
        delivery_id: str,
        event: str,
        body_sha256: str,
        received_at: datetime,
        *,
        action: str | None = None,
    ) -> DeliveryStatus:
        """Record a verified delivery (the event record) before it is processed.

        The delivery ID is GitHub's unique event identifier; GitHub reuses it when a
        delivery is redelivered. A known ID with the same payload is a duplicate,
        unless its earlier processing failed or was abandoned - then it is processed
        again (``RETRY``). A known ID with a different payload is never processed.
        """
        now = _ts(received_at)
        with self._transaction() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO deliveries (delivery_id, event, body_sha256, received_at, "
                "provider, action, status, attempts, updated_at) "
                "VALUES (?, ?, ?, ?, 'github', ?, 'processing', 1, ?)",
                (delivery_id, event, body_sha256, now, action, now),
            )
            if cursor.rowcount == 1:
                return DeliveryStatus.NEW
            row = db.execute(
                "SELECT event, body_sha256, status, updated_at FROM deliveries "
                "WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            if row is None or row["event"] != event or row["body_sha256"] != body_sha256:
                return DeliveryStatus.CONFLICT
            stuck = (
                row["status"] == EventProcessingStatus.PROCESSING.value
                and (row["updated_at"] or 0) < now - STUCK_DELIVERY_SECONDS
            )
            if row["status"] == EventProcessingStatus.FAILED.value or stuck:
                db.execute(
                    "UPDATE deliveries SET status = 'processing', attempts = attempts + 1, "
                    "updated_at = ?, detail = NULL WHERE delivery_id = ?",
                    (now, delivery_id),
                )
                return DeliveryStatus.RETRY
        return DeliveryStatus.DUPLICATE

    def finish_delivery(
        self,
        delivery_id: str,
        status: EventProcessingStatus,
        now: datetime,
        *,
        installation_id: int | None = None,
        repository_id: int | None = None,
        detail: str | None = None,
    ) -> None:
        with self._transaction() as db:
            db.execute(
                "UPDATE deliveries SET status = ?, updated_at = ?, "
                "installation_id = COALESCE(?, installation_id), "
                "repository_id = COALESCE(?, repository_id), detail = ? WHERE delivery_id = ?",
                (
                    status.value,
                    _ts(now),
                    installation_id,
                    repository_id,
                    (detail or "")[:500] or None,
                    delivery_id,
                ),
            )

    def delivery_status(self, delivery_id: str) -> EventProcessingStatus | None:
        rows = self._query("SELECT status FROM deliveries WHERE delivery_id = ?", (delivery_id,))
        return EventProcessingStatus(rows[0]["status"]) if rows else None

    def fail_stuck_deliveries(self, now: datetime) -> int:
        """Mark deliveries abandoned mid-processing as failed, so a redelivery is processed."""
        with self._transaction() as db:
            return db.execute(
                "UPDATE deliveries SET status = 'failed', detail = 'processing abandoned', "
                "updated_at = ? WHERE status = 'processing' AND updated_at < ?",
                (_ts(now), _ts(now) - STUCK_DELIVERY_SECONDS),
            ).rowcount

    # -- installations -------------------------------------------------- #
    def upsert_installation(self, record: InstallationRecord) -> None:
        with self._transaction() as db:
            self.upsert_installation_in(db, record)

    @staticmethod
    def upsert_installation_in(db: sqlite3.Connection, record: InstallationRecord) -> None:
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
            self.set_installation_state_in(db, installation_id, state, now)

    @staticmethod
    def set_installation_state_in(
        db: sqlite3.Connection, installation_id: int, state: InstallationState, now: datetime
    ) -> None:
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

    def set_repository_details(
        self,
        installation_id: int,
        repository_id: int,
        *,
        default_branch: str | None,
        private: bool,
        archived: bool,
    ) -> None:
        """Record GitHub's authoritative repository details (from the API, never a client)."""
        with self._transaction() as db:
            db.execute(
                "UPDATE known_repositories SET default_branch = ?, private = ?, archived = ? "
                "WHERE installation_id = ? AND repository_id = ?",
                (
                    default_branch,
                    1 if private else 0,
                    1 if archived else 0,
                    int(installation_id),
                    int(repository_id),
                ),
            )

    def monitoring_enabled(self, installation_id: int, repository_id: int) -> bool:
        """False when an administrator paused CommitGuard for this repository."""
        rows = self._query(
            "SELECT monitoring_enabled FROM repository_settings WHERE installation_id = ? "
            "AND repository_id = ?",
            (int(installation_id), int(repository_id)),
        )
        return not rows or bool(rows[0]["monitoring_enabled"])

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
            scan_key=row["scan_key"] or row["job_key"],
            execution=row["execution"] or 1,
            trigger=ScanTrigger(row["trigger_kind"] or row["event"]),
            previous_job_id=row["previous_job_id"],
        )

    def create_job(self, job: NewScanJob, now: datetime) -> tuple[ScanJob, bool]:
        """Create a job unless an equivalent one is queued, running or completed.

        An equivalent job that ended in ``error`` or ``cancelled`` does not block a
        new attempt (e.g. a pull request reopened after GitHub was unavailable): the
        new job is the next *execution* of the same logical scan.
        """
        with self._transaction() as db:
            existing = db.execute(
                "SELECT * FROM scan_jobs WHERE installation_id = ? AND repository_id = ? "
                "AND job_key = ? ORDER BY sequence DESC LIMIT 1",
                (job.installation_id, job.repository.id, job.job_key),
            ).fetchone()
            if existing is not None and existing["state"] not in ("error", "cancelled"):
                return self._job(existing), False
            row = self._insert_job(
                db,
                job,
                now,
                scan_key=job.job_key,
                trigger=job.trigger,
                previous_job_id=existing["job_id"] if existing is not None else None,
            )
        return self._job(row), True

    def create_execution(
        self,
        previous: ScanJob,
        *,
        trigger: ScanTrigger,
        now: datetime,
        delivery_id: str | None = None,
        requested_by: str | None = None,
    ) -> tuple[ScanJob, bool]:
        """A new execution of the logical scan ``previous`` belongs to (re-run, manual, retry).

        The repository, commits, event and check name are copied from the stored
        execution - never taken from a request. If an execution of the same scan is
        already queued or running, that execution is returned instead of starting
        another one, so repeated requests cannot pile up duplicate scans.
        """
        with self._transaction() as db:
            active = db.execute(
                "SELECT * FROM scan_jobs WHERE installation_id = ? AND repository_id = ? "
                "AND scan_key = ? AND state IN ('queued', 'running') ORDER BY sequence DESC "
                "LIMIT 1",
                (previous.installation_id, previous.repository.id, previous.scan_key),
            ).fetchone()
            if active is not None:
                return self._job(active), False
            if delivery_id is not None:
                same_delivery = db.execute(
                    "SELECT * FROM scan_jobs WHERE installation_id = ? AND repository_id = ? "
                    "AND scan_key = ? AND delivery_id = ?",
                    (
                        previous.installation_id,
                        previous.repository.id,
                        previous.scan_key,
                        delivery_id,
                    ),
                ).fetchone()
                if same_delivery is not None:
                    return self._job(same_delivery), False
            new_job = NewScanJob(
                job_key=f"{trigger.value}:{uuid.uuid4().hex}",
                installation_id=previous.installation_id,
                repository=previous.repository,
                delivery_id=delivery_id,
                event=previous.event,
                group_key=previous.group_key,
                head_sha=previous.head_sha,
                check_name=previous.check_name,
                pull_request_number=previous.pull_request_number,
                context=previous.context,
                requested_by=requested_by,
            )
            row = self._insert_job(
                db,
                new_job,
                now,
                scan_key=previous.scan_key,
                trigger=trigger,
                previous_job_id=previous.job_id,
            )
        return self._job(row), True

    def _insert_job(
        self,
        db: sqlite3.Connection,
        job: NewScanJob,
        now: datetime,
        *,
        scan_key: str,
        trigger: ScanTrigger,
        previous_job_id: str | None,
    ) -> sqlite3.Row:
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
        execution = db.execute(
            "SELECT COALESCE(MAX(execution), 0) + 1 AS next FROM scan_jobs "
            "WHERE installation_id = ? AND repository_id = ? AND scan_key = ?",
            (job.installation_id, job.repository.id, scan_key),
        ).fetchone()["next"]
        job_id = uuid.uuid4().hex
        db.execute(
            "INSERT INTO scan_jobs (job_id, job_key, installation_id, repository_id, owner, "
            "name, delivery_id, event, group_key, head_sha, check_name, pull_request_number, "
            "context, state, attempts, created_at, updated_at, base_sha, ref, requested_by, "
            "scan_key, execution, trigger_kind, previous_job_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, "
            "?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                scan_key,
                int(execution),
                trigger.value,
                previous_job_id,
            ),
        )
        row: sqlite3.Row | None = db.execute(
            "SELECT * FROM scan_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        assert row is not None  # noqa: S101 - inserted above
        return row

    def list_executions(
        self, installation_id: int, repository_id: int, scan_key: str, *, limit: int = 100
    ) -> list[ScanJob]:
        rows = self._query(
            "SELECT * FROM scan_jobs WHERE installation_id = ? AND repository_id = ? "
            "AND scan_key = ? ORDER BY sequence DESC LIMIT ?",
            (int(installation_id), int(repository_id), scan_key, int(limit)),
        )
        return [self._job(r) for r in rows]

    def latest_group_job(
        self, installation_id: int, repository_id: int, group_key: str
    ) -> ScanJob | None:
        rows = self._query(
            "SELECT * FROM scan_jobs WHERE installation_id = ? AND repository_id = ? "
            "AND group_key = ? ORDER BY sequence DESC LIMIT 1",
            (int(installation_id), int(repository_id), group_key),
        )
        return self._job(rows[0]) if rows else None

    def latest_jobs_for_commit(
        self, installation_id: int, repository_id: int, head_sha: str
    ) -> list[ScanJob]:
        """The newest execution per check name for one commit."""
        rows = self._query(
            "SELECT * FROM scan_jobs j WHERE installation_id = ? AND repository_id = ? "
            "AND head_sha = ? AND sequence = (SELECT MAX(sequence) FROM scan_jobs o WHERE "
            "o.installation_id = j.installation_id AND o.repository_id = j.repository_id "
            "AND o.head_sha = j.head_sha AND o.check_name = j.check_name) ORDER BY check_name",
            (int(installation_id), int(repository_id), head_sha),
        )
        return [self._job(r) for r in rows]

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
        return self.claim_job_outcome(job_id, now, lease_seconds, max_attempts).job

    def claim_job_outcome(
        self, job_id: str, now: datetime, lease_seconds: float, max_attempts: int
    ) -> ClaimOutcome:
        """Claim a job; a job that used up its attempts is ended as ``error`` and returned."""
        with self._transaction() as db:
            row = db.execute("SELECT * FROM scan_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return ClaimOutcome()
            state = row["state"]
            abandoned = state == "running" and (row["lease_expires_at"] or 0) < _ts(now)
            if state != "queued" and not abandoned:
                return ClaimOutcome()
            if row["attempts"] >= max_attempts:
                db.execute(
                    "UPDATE scan_jobs SET state = 'error', failure_kind = 'internal', "
                    "message = 'scan abandoned after repeated attempts', updated_at = ?, "
                    "lease_expires_at = NULL, completed_at = ? WHERE job_id = ?",
                    (_ts(now), _ts(now), job_id),
                )
                row = db.execute("SELECT * FROM scan_jobs WHERE job_id = ?", (job_id,)).fetchone()
                return ClaimOutcome(exhausted=self._job(row))
            db.execute(
                "UPDATE scan_jobs SET state = 'running', attempts = attempts + 1, "
                "lease_expires_at = ?, updated_at = ?, started_at = ? WHERE job_id = ?",
                (_ts(now) + lease_seconds, _ts(now), _ts(now), job_id),
            )
            row = db.execute("SELECT * FROM scan_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return ClaimOutcome(job=self._job(row))

    def update_job(self, job_id: str, now: datetime, **fields: Any) -> None:
        unknown = set(fields) - set(_JOB_UPDATES)
        if unknown:
            raise ValueError(f"cannot update job fields: {', '.join(sorted(unknown))}")
        with self._transaction() as db:
            self.update_job_in(db, job_id, now, **fields)

    @staticmethod
    def update_job_in(db: sqlite3.Connection, job_id: str, now: datetime, **fields: Any) -> None:
        """:meth:`update_job` inside the caller's transaction."""
        unknown = set(fields) - set(_JOB_UPDATES)
        if unknown:
            raise ValueError(f"cannot update job fields: {', '.join(sorted(unknown))}")
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

    # -- merge queue ----------------------------------------------------- #
    @staticmethod
    def _merge_group(row: sqlite3.Row) -> MergeGroupRecord:
        return MergeGroupRecord(
            installation_id=row["installation_id"],
            repository_id=row["repository_id"],
            head_sha=row["head_sha"],
            head_ref=row["head_ref"],
            base_sha=row["base_sha"],
            base_ref=row["base_ref"],
            pull_requests=tuple(json.loads(row["pull_requests"] or "[]")),
            state=MergeGroupState(row["state"]),
            destroyed_reason=row["destroyed_reason"],
            job_id=row["job_id"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
            destroyed_at=_opt_dt(row["destroyed_at"]),
        )

    def record_merge_group(
        self,
        *,
        installation_id: int,
        repository_id: int,
        head_sha: str,
        head_ref: str,
        base_sha: str,
        base_ref: str,
        pull_requests: Sequence[int],
        delivery_id: str | None,
        now: datetime,
    ) -> tuple[MergeGroupRecord, bool]:
        """Store a merge group GitHub requested checks for.

        Returns ``(record, requested)``; ``requested`` is False when the group is
        already known - in particular when its ``destroyed`` event arrived first, so
        an out-of-order request can never revive a destroyed merge group.
        """
        with self._transaction() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO merge_groups (installation_id, repository_id, head_sha, "
                "head_ref, base_sha, base_ref, pull_requests, state, delivery_id, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'checks_requested', ?, ?, ?)",
                (
                    int(installation_id),
                    int(repository_id),
                    head_sha,
                    head_ref,
                    base_sha,
                    base_ref,
                    json.dumps(sorted({int(n) for n in pull_requests})),
                    delivery_id,
                    _ts(now),
                    _ts(now),
                ),
            )
            row = db.execute(
                "SELECT * FROM merge_groups WHERE installation_id = ? AND repository_id = ? "
                "AND head_sha = ?",
                (int(installation_id), int(repository_id), head_sha),
            ).fetchone()
        return self._merge_group(row), cursor.rowcount == 1

    def destroy_merge_group(
        self,
        *,
        installation_id: int,
        repository_id: int,
        head_sha: str,
        head_ref: str,
        base_sha: str,
        base_ref: str,
        pull_requests: Sequence[int],
        reason: str | None,
        now: datetime,
    ) -> tuple[MergeGroupRecord, bool]:
        """Mark a merge group destroyed (inserting it if its request was never seen).

        Returns ``(record, changed)``; repeated ``destroyed`` events change nothing.
        """
        with self._transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO merge_groups (installation_id, repository_id, head_sha, "
                "head_ref, base_sha, base_ref, pull_requests, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'checks_requested', ?, ?)",
                (
                    int(installation_id),
                    int(repository_id),
                    head_sha,
                    head_ref,
                    base_sha,
                    base_ref,
                    json.dumps(sorted({int(n) for n in pull_requests})),
                    _ts(now),
                    _ts(now),
                ),
            )
            cursor = db.execute(
                "UPDATE merge_groups SET state = 'destroyed', destroyed_reason = ?, "
                "destroyed_at = ?, updated_at = ? WHERE installation_id = ? "
                "AND repository_id = ? AND head_sha = ? AND state != 'destroyed'",
                (reason, _ts(now), _ts(now), int(installation_id), int(repository_id), head_sha),
            )
            row = db.execute(
                "SELECT * FROM merge_groups WHERE installation_id = ? AND repository_id = ? "
                "AND head_sha = ?",
                (int(installation_id), int(repository_id), head_sha),
            ).fetchone()
        return self._merge_group(row), cursor.rowcount == 1

    def set_merge_group_job(
        self, installation_id: int, repository_id: int, head_sha: str, job_id: str, now: datetime
    ) -> None:
        with self._transaction() as db:
            db.execute(
                "UPDATE merge_groups SET job_id = ?, updated_at = ? WHERE installation_id = ? "
                "AND repository_id = ? AND head_sha = ?",
                (job_id, _ts(now), int(installation_id), int(repository_id), head_sha),
            )

    def get_merge_group(
        self, installation_id: int, repository_id: int, head_sha: str
    ) -> MergeGroupRecord | None:
        rows = self._query(
            "SELECT * FROM merge_groups WHERE installation_id = ? AND repository_id = ? "
            "AND head_sha = ?",
            (int(installation_id), int(repository_id), head_sha),
        )
        return self._merge_group(rows[0]) if rows else None

    def list_merge_groups(
        self, installation_id: int, repository_id: int, *, limit: int = 10
    ) -> list[MergeGroupRecord]:
        rows = self._query(
            "SELECT * FROM merge_groups WHERE installation_id = ? AND repository_id = ? "
            "ORDER BY created_at DESC, head_sha LIMIT ?",
            (int(installation_id), int(repository_id), int(limit)),
        )
        return [self._merge_group(r) for r in rows]

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
                "merge_groups": db.execute(
                    "DELETE FROM merge_groups WHERE updated_at < ? AND state = 'destroyed'",
                    (cutoff,),
                ).rowcount,
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
