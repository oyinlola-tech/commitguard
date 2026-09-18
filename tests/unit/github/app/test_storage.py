"""State store: replay protection, job idempotency, check ownership, tenancy, retention."""

import os
import stat
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from commitguard.audit.models import AuditEvent, AuditEventType
from commitguard.ci.context import CIContext, CIEventKind, CIProvider
from commitguard.github.identifiers import AccountType, RepositoryRef
from commitguard.github.storage import (
    DeliveryStatus,
    InstallationRecord,
    InstallationState,
    JobState,
    NewScanJob,
    SqliteStateStore,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
REPO = RepositoryRef(id=5001, owner="octo-org", name="project")
A, B = "a" * 40, "b" * 40


@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    return SqliteStateStore(tmp_path / "state" / "app.sqlite3")


def new_job(
    key: str = "k1", *, installation: int = 42, repo: RepositoryRef = REPO, head: str = B
) -> NewScanJob:
    context = CIContext(
        provider=CIProvider.GITHUB,
        event=CIEventKind.PULL_REQUEST,
        event_name="pull_request",
        repository=repo.full_name,
        base_sha=A,
        head_sha=head,
        pull_request_number=7,
    )
    return NewScanJob(
        job_key=key,
        installation_id=installation,
        repository=repo,
        delivery_id="d-1",
        event="pull_request",
        group_key="pull_request:7",
        head_sha=head,
        check_name="commitguard-app",
        pull_request_number=7,
        context=context,
    )


def test_database_file_is_private(tmp_path: Path) -> None:
    SqliteStateStore(tmp_path / "db" / "app.sqlite3")
    if os.name == "posix":
        mode = (tmp_path / "db" / "app.sqlite3").stat().st_mode
        assert not mode & (stat.S_IRWXG | stat.S_IRWXO)


def test_delivery_replay_protection(store: SqliteStateStore) -> None:
    assert store.record_delivery("d-1", "push", "digest-1", NOW) is DeliveryStatus.NEW
    assert store.record_delivery("d-1", "push", "digest-1", NOW) is DeliveryStatus.DUPLICATE
    assert store.record_delivery("d-1", "push", "digest-2", NOW) is DeliveryStatus.CONFLICT
    assert store.record_delivery("d-1", "pull_request", "digest-1", NOW) is DeliveryStatus.CONFLICT
    assert store.record_delivery("d-2", "push", "digest-1", NOW) is DeliveryStatus.NEW


def test_concurrent_deliveries_are_recorded_once(store: SqliteStateStore) -> None:
    results: list[DeliveryStatus] = []
    lock = threading.Lock()

    def deliver() -> None:
        status = store.record_delivery("same", "push", "digest", NOW)
        with lock:
            results.append(status)

    threads = [threading.Thread(target=deliver) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(DeliveryStatus.NEW) == 1
    assert results.count(DeliveryStatus.DUPLICATE) == 15


def test_equivalent_jobs_are_deduplicated_unless_they_errored(store: SqliteStateStore) -> None:
    first, created = store.create_job(new_job(), NOW)
    assert created
    assert first.state is JobState.QUEUED
    assert first.attempts == 0
    again, created = store.create_job(new_job(), NOW)
    assert not created
    assert again.job_id == first.job_id
    store.update_job(first.job_id, NOW, state=JobState.ERROR)
    retry, created = store.create_job(new_job(), NOW)
    assert created
    assert retry.sequence > first.sequence


def test_claim_is_atomic_and_bounded(store: SqliteStateStore) -> None:
    job, _ = store.create_job(new_job(), NOW)
    claimed = store.claim_job(job.job_id, NOW, lease_seconds=60, max_attempts=2)
    assert claimed is not None
    assert claimed.state is JobState.RUNNING
    assert claimed.attempts == 1
    assert store.claim_job(job.job_id, NOW, lease_seconds=60, max_attempts=2) is None
    later = NOW + timedelta(seconds=61)  # lease expired: a crashed worker's job is recoverable
    assert store.recoverable_jobs(later, queued_before=later) == [job.job_id]
    assert store.claim_job(job.job_id, later, lease_seconds=60, max_attempts=2) is not None
    muchlater = later + timedelta(seconds=61)
    assert store.claim_job(job.job_id, muchlater, lease_seconds=60, max_attempts=2) is None
    final = store.get_job(job.job_id)
    assert final is not None
    assert final.state is JobState.ERROR
    assert final.message == "scan abandoned after repeated attempts"


def test_recoverable_jobs_skip_recently_queued(store: SqliteStateStore) -> None:
    job, _ = store.create_job(new_job(), NOW)
    assert store.recoverable_jobs(NOW, queued_before=NOW - timedelta(minutes=5)) == []
    assert store.recoverable_jobs(NOW + timedelta(minutes=6), queued_before=NOW) == [job.job_id]


def test_update_job_rejects_unknown_columns(store: SqliteStateStore) -> None:
    job, _ = store.create_job(new_job(), NOW)
    with pytest.raises(ValueError, match="cannot update"):
        store.update_job(job.job_id, NOW, installation_id=99)
    with pytest.raises(ValueError, match="cannot update"):
        store.update_job(job.job_id, NOW, **{"state = 'passed' --": "x"})


def test_check_slot_ownership_prevents_stale_writes(store: SqliteStateStore) -> None:
    slot = (42, REPO.id, B, "commitguard-app")
    older = store.claim_check(*slot, 1, NOW)
    assert older.owned
    assert older.check_run_id is None
    assert store.set_check_run_id(*slot, 1, 777)
    newer = store.claim_check(*slot, 2, NOW)
    assert newer.owned
    assert newer.check_run_id == 777  # reuses the run, no duplicate
    assert store.check_owner(*slot) == 2
    assert not store.set_check_run_id(*slot, 1, 888)  # the older job lost ownership
    stale = store.claim_check(*slot, 1, NOW)
    assert not stale.owned
    assert stale.owner_sequence == 2


def test_group_sequence_and_cancellation(store: SqliteStateStore) -> None:
    first, _ = store.create_job(new_job("k1"), NOW)
    second, _ = store.create_job(new_job("k2", head="c" * 40), NOW)
    assert store.latest_group_sequence(42, REPO.id, "pull_request:7") == second.sequence
    assert store.cancel_queued_group(42, REPO.id, "pull_request:7", NOW) == 2
    assert {store.get_job(j.job_id).state for j in (first, second)} == {JobState.CANCELLED}  # type: ignore[union-attr]


def test_tenant_scoped_listing(store: SqliteStateStore) -> None:
    other_repo = RepositoryRef(id=6001, owner="other-org", name="secret")
    store.create_job(new_job("k1"), NOW)
    store.create_job(new_job("k2", installation=77, repo=other_repo), NOW)
    assert [j.repository.id for j in store.list_jobs(installation_id=42)] == [REPO.id]
    assert store.list_jobs(installation_id=42, repository_id=other_repo.id) == []
    assert [j.installation_id for j in store.list_jobs(installation_id=77)] == [77]
    for installation, repo_id in ((42, REPO.id), (77, other_repo.id)):
        store.append_audit_event(
            AuditEvent(
                type=AuditEventType.SCAN_PASSED,
                occurred_at=NOW,
                installation_id=installation,
                repository_id=repo_id,
            )
        )
    assert [e.installation_id for e in store.list_audit_events(installation_id=42)] == [42]


def test_installation_lifecycle_state(store: SqliteStateStore) -> None:
    record = InstallationRecord(
        installation_id=42,
        account_id=1,
        account_login="octo-org",
        account_type=AccountType.ORGANIZATION,
        repository_selection="selected",
        state=InstallationState.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    store.upsert_installation(record)
    store.replace_repositories(42, [REPO], NOW)
    assert store.repository_listed(42, REPO.id)
    job, _ = store.create_job(new_job(), NOW)
    store.remove_repositories(42, [REPO.id])
    assert not store.repository_listed(42, REPO.id)
    assert store.get_job(job.job_id).state is JobState.CANCELLED  # type: ignore[union-attr]
    store.set_installation_state(42, InstallationState.DELETED, NOW)
    installation = store.get_installation(42)
    assert installation is not None
    assert installation.state is InstallationState.DELETED


def test_retention_purges_old_data_but_keeps_active_jobs(store: SqliteStateStore) -> None:
    old = NOW - timedelta(days=40)
    store.record_delivery("old", "push", "x", old)
    store.record_delivery("new", "push", "x", NOW)
    finished, _ = store.create_job(new_job("k1"), old)
    store.update_job(finished.job_id, old, state=JobState.PASSED)
    running, _ = store.create_job(new_job("k2", head="c" * 40), old)
    store.append_audit_event(
        AuditEvent(type=AuditEventType.SCAN_PASSED, occurred_at=old, installation_id=42)
    )
    counts = store.purge_expired(NOW - timedelta(days=30))
    assert counts["deliveries"] == 1
    assert counts["scan_jobs"] == 1
    assert counts["audit_events"] == 1
    assert store.get_job(finished.job_id) is None
    assert store.get_job(running.job_id) is not None
    assert store.record_delivery("old", "push", "x", NOW) is DeliveryStatus.NEW


def test_retention_purges_finished_governance_work_but_keeps_history(
    store: SqliteStateStore,
) -> None:
    old, cutoff = (NOW - timedelta(days=40)).timestamp(), NOW - timedelta(days=30)
    with store.transaction() as db:
        for simulation, state in (("s-old", "completed"), ("s-run", "running")):
            db.execute(
                "INSERT INTO policy_simulations (simulation_id, account_id, target_type, "
                "target_id, current_version, document, parameters, state, requested_at, "
                "completed_at) VALUES (?, 1, 'organization', '1', 1, '{}', '{}', ?, ?, ?)",
                (simulation, state, old, old),
            )
        for operation, status in (("b-old", "completed"), ("b-open", "running")):
            db.execute(
                "INSERT INTO bulk_operations (operation_id, account_id, type, parameters, "
                "idempotency_key, created_at, status, total, updated_at) "
                "VALUES (?, 1, 'onboard', '{}', ?, ?, ?, 1, ?)",
                (operation, operation, old, status, old),
            )
            db.execute(
                "INSERT INTO bulk_operation_items (operation_id, repository_id, status, "
                "updated_at) VALUES (?, 5001, 'completed', ?)",
                (operation, old),
            )
    counts = store.purge_expired(cutoff)
    assert (counts["simulations"], counts["bulk_operations"]) == (1, 1)
    remaining = store.query("SELECT simulation_id FROM policy_simulations")
    assert [r["simulation_id"] for r in remaining] == ["s-run"]
    items = store.query("SELECT operation_id FROM bulk_operation_items")
    assert [r["operation_id"] for r in items] == ["b-open"]


def test_store_never_holds_commit_messages_or_identities(
    store: SqliteStateStore, tmp_path: Path
) -> None:
    job, _ = store.create_job(new_job(), NOW)
    store.update_job(job.job_id, NOW, state=JobState.FAILED, message="policy violation")
    raw = (tmp_path / "state" / "app.sqlite3").read_bytes()
    for wal in (tmp_path / "state").glob("*-wal"):
        raw += wal.read_bytes()
    assert b"Co-authored-by" not in raw
    assert b"@example.com" not in raw


# --------------------------------------------------------------------------- #
# Retention: nothing outlives the installation it belongs to
# --------------------------------------------------------------------------- #
#: Tables keyed by installation whose rows are cleaned up somewhere other than
#: ``_ORPHAN_DELETES``, with the reason.
_CLEANED_ELSEWHERE = {
    "deliveries": "purged by received_at",
    "check_runs": "purged by updated_at",
    "scan_jobs": "purged by updated_at once finished",
    "merge_groups": "purged once destroyed",
    "notification_events": "purged by the notification service's own retention",
    "session_installations": "ON DELETE CASCADE from sessions",
    "session_repositories": "ON DELETE CASCADE from sessions",
}


def _tenant_tables(store: SqliteStateStore) -> list[str]:
    names = [
        row["name"]
        for row in store.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return [
        name
        for name in names
        if name not in ("installations", "audit_events")
        and any(
            column["name"] == "installation_id"
            for column in store.query(f"PRAGMA table_info({name})")
        )
    ]


def test_purging_a_removed_installation_leaves_no_row_behind(store: SqliteStateStore) -> None:
    """A repository inventory used to survive its installation forever.

    ``known_repositories`` is purged only once a repository has been *removed*
    from a live installation, and ``installation_repositories`` and
    ``installation_sync_status`` had no purge at all, so uninstalling left an
    organisation's repository names in the database with nothing able to
    attribute or delete them.
    """
    old = NOW - timedelta(days=400)
    with store.transaction() as db:
        db.execute(
            "INSERT INTO installations VALUES (42, 1001, 'octo-org', 'Organization', "
            "'selected', 'deleted', '{}', ?, ?)",
            (old.timestamp(), old.timestamp()),
        )
        db.execute(
            "INSERT INTO installation_repositories VALUES (42, 5001, 'octo-org', 'project', ?)",
            (old.timestamp(),),
        )
        db.execute(
            "INSERT INTO known_repositories (installation_id, repository_id, owner, name, "
            "first_seen_at, last_seen_at, removed_at) VALUES (42, 5001, 'octo-org', 'project', "
            "?, ?, NULL)",
            (old.timestamp(), old.timestamp()),
        )
        db.execute(
            "INSERT INTO installation_sync_status (installation_id, account_id, state, "
            "started_at, updated_at) VALUES (42, 1001, 'healthy', ?, ?)",
            (old.timestamp(), old.timestamp()),
        )

    store.purge_expired(NOW)

    assert store.query("SELECT 1 FROM installations") == []
    for table in ("installation_repositories", "known_repositories", "installation_sync_status"):
        assert store.query(f"SELECT 1 FROM {table}") == [], f"{table} kept an orphaned row"


def test_every_tenant_table_has_a_documented_cleanup_path(store: SqliteStateStore) -> None:
    """A new installation-scoped table must say how its rows are ever removed."""
    from commitguard.github.storage import _ORPHAN_DELETES

    covered = {
        table
        for table in _tenant_tables(store)
        if any(f"DELETE FROM {table} " in statement for statement in _ORPHAN_DELETES)
    }
    unexplained = sorted(set(_tenant_tables(store)) - covered - set(_CLEANED_ELSEWHERE))
    assert not unexplained, (
        f"these tables are keyed by installation but nothing removes their rows when the "
        f"installation is purged: {', '.join(unexplained)}"
    )
