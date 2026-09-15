"""Phase 7 webhook normalisation and storage: merge groups, re-runs, event records, migration."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from commitguard.ci.context import CIContext, CIEventKind, CIProvider
from commitguard.exceptions.service import InfrastructureError
from commitguard.github.errors import WebhookValidationError
from commitguard.github.events import (
    CheckRunRerequestedEvent,
    CheckSuiteRerequestedEvent,
    IgnoredEvent,
    MergeGroupAction,
    MergeGroupEvent,
    normalize_webhook,
)
from commitguard.github.identifiers import RepositoryRef
from commitguard.github.storage import (
    _SCHEMA_V1,
    _SCHEMA_V2,
    SCHEMA_VERSION,
    DeliveryStatus,
    EventProcessingStatus,
    JobState,
    MergeGroupState,
    NewScanJob,
    ScanTrigger,
    SqliteStateStore,
    _statements,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
REPO = RepositoryRef(id=5001, owner="octo-org", name="project")
A, B, C = "a" * 40, "b" * 40, "c" * 40


def _repository() -> dict[str, Any]:
    return {
        "id": 5001,
        "name": "project",
        "full_name": "octo-org/project",
        "owner": {"login": "octo-org"},
    }


def merge_group(action: str = "checks_requested", **group: Any) -> dict[str, Any]:
    payload = {
        "action": action,
        "merge_group": {
            "head_sha": B,
            "head_ref": f"refs/heads/gh-readonly-queue/main/pr-12-{C}",
            "base_sha": A,
            "base_ref": "refs/heads/main",
            "head_commit": {"id": B},
            **group,
        },
        "repository": _repository(),
        "installation": {"id": 42},
    }
    return payload


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
def test_merge_group_events_follow_github_payload_semantics() -> None:
    event = normalize_webhook("merge_group", merge_group())
    assert isinstance(event, MergeGroupEvent)
    assert (event.action, event.head_sha, event.base_sha, event.pull_requests) == (
        MergeGroupAction.CHECKS_REQUESTED,
        B,
        A,
        (12,),
    )
    assert event.context.event is CIEventKind.MERGE_GROUP
    destroyed = normalize_webhook(
        "merge_group", {**merge_group("destroyed"), "reason": "invalidated"}
    )
    assert isinstance(destroyed, MergeGroupEvent)
    assert destroyed.reason == "invalidated"
    weird_reason = normalize_webhook("merge_group", {**merge_group("destroyed"), "reason": "<x>"})
    assert isinstance(weird_reason, MergeGroupEvent)
    assert weird_reason.reason is None
    assert isinstance(normalize_webhook("merge_group", merge_group("renamed")), IgnoredEvent)


@pytest.mark.parametrize(
    "group",
    [
        {"head_ref": "refs/heads/feature"},
        {"head_ref": "refs/heads/gh-readonly-queue/release/pr-1-" + C},
        {"base_ref": "main"},
        {"head_sha": "not-a-sha"},
        {"base_sha": None},
        {"head_commit": {"id": A}},
        {"head_ref": "refs/heads/gh-readonly-queue/main/pr-1-\x1b[2K"},
    ],
)
def test_forged_or_malformed_merge_groups_are_rejected(group: dict[str, Any]) -> None:
    with pytest.raises(WebhookValidationError):
        normalize_webhook("merge_group", merge_group(**group))


def test_rerun_events_are_normalised_and_other_actions_ignored() -> None:
    run = {
        "action": "rerequested",
        "check_run": {
            "id": 9001,
            "name": "commitguard-app",
            "head_sha": B,
            "external_id": "d" * 32,
            "app": {"id": 4242},
        },
        "repository": _repository(),
        "installation": {"id": 42},
    }
    event = normalize_webhook("check_run", run)
    assert isinstance(event, CheckRunRerequestedEvent)
    assert (event.check_run_id, event.external_id, event.app_id) == (9001, "d" * 32, 4242)
    forged_external = {**run, "check_run": {**run["check_run"], "external_id": "../../etc/passwd"}}
    assert normalize_webhook("check_run", forged_external).external_id is None  # type: ignore[union-attr]
    assert isinstance(normalize_webhook("check_run", {**run, "action": "completed"}), IgnoredEvent)
    with pytest.raises(WebhookValidationError):
        normalize_webhook("check_run", {**run, "check_run": {**run["check_run"], "head_sha": "zz"}})
    suite = {
        "action": "rerequested",
        "check_suite": {"id": 1, "head_sha": B, "app": {"id": 4242}},
        "repository": _repository(),
        "installation": {"id": 42},
    }
    assert isinstance(normalize_webhook("check_suite", suite), CheckSuiteRerequestedEvent)
    assert isinstance(
        normalize_webhook("check_suite", {**suite, "action": "requested"}), IgnoredEvent
    )
    with pytest.raises(WebhookValidationError):
        normalize_webhook("check_suite", {**suite, "check_suite": {"id": 1, "head_sha": B}})


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
@pytest.fixture
def store(tmp_path: Path) -> SqliteStateStore:
    return SqliteStateStore(tmp_path / "state.sqlite3")


def _job(key: str = "k1", head: str = B, delivery: str | None = "delivery-1") -> NewScanJob:
    return NewScanJob(
        job_key=key,
        installation_id=42,
        repository=REPO,
        delivery_id=delivery,
        event="pull_request",
        group_key="pull_request:7",
        head_sha=head,
        check_name="commitguard-app",
        pull_request_number=7,
        context=CIContext(
            provider=CIProvider.GITHUB,
            event=CIEventKind.PULL_REQUEST,
            event_name="pull_request",
            repository="octo-org/project",
            base_sha=A,
            head_sha=head,
            pull_request_number=7,
        ),
    )


def test_event_records_track_processing_and_allow_retry_after_failure(
    store: SqliteStateStore,
) -> None:
    assert (
        store.record_delivery("delivery-1", "push", "digest", NOW, action=None)
        is DeliveryStatus.NEW
    )
    assert store.delivery_status("delivery-1") is EventProcessingStatus.PROCESSING
    assert store.record_delivery("delivery-1", "push", "digest", NOW) is DeliveryStatus.DUPLICATE
    store.finish_delivery("delivery-1", EventProcessingStatus.FAILED, NOW, detail="boom")
    assert store.record_delivery("delivery-1", "push", "digest", NOW) is DeliveryStatus.RETRY
    assert store.record_delivery("delivery-1", "push", "other", NOW) is DeliveryStatus.CONFLICT
    store.finish_delivery("delivery-1", EventProcessingStatus.PROCESSED, NOW)
    assert store.record_delivery("delivery-1", "push", "digest", NOW) is DeliveryStatus.DUPLICATE
    store.record_delivery("delivery-2", "push", "digest", NOW)
    assert store.fail_stuck_deliveries(NOW + timedelta(minutes=5)) == 0
    assert store.fail_stuck_deliveries(NOW + timedelta(minutes=11)) == 1
    [row] = store.query(
        "SELECT attempts, provider FROM deliveries WHERE delivery_id = 'delivery-1'"
    )
    assert (row["attempts"], row["provider"]) == (2, "github")


def test_executions_share_a_scan_and_never_pile_up(store: SqliteStateStore) -> None:
    first, _ = store.create_job(_job(), NOW)
    assert (first.execution, first.trigger, first.scan_key) == (1, ScanTrigger.PULL_REQUEST, "k1")
    duplicate, created = store.create_execution(
        first, trigger=ScanTrigger.RERUN, now=NOW, delivery_id="rerun-a"
    )
    assert not created
    assert duplicate.job_id == first.job_id
    store.update_job(first.job_id, NOW, state=JobState.FAILED)
    rerun, created = store.create_execution(
        first, trigger=ScanTrigger.RERUN, now=NOW, delivery_id="rerun-a"
    )
    assert created
    assert (rerun.execution, rerun.trigger, rerun.scan_key, rerun.previous_job_id) == (
        2,
        ScanTrigger.RERUN,
        "k1",
        first.job_id,
    )
    assert (rerun.head_sha, rerun.context, rerun.check_name) == (
        first.head_sha,
        first.context,
        first.check_name,
    )
    store.update_job(rerun.job_id, NOW, state=JobState.PASSED)
    replay, created = store.create_execution(
        first, trigger=ScanTrigger.RERUN, now=NOW, delivery_id="rerun-a"
    )
    assert not created
    assert replay.job_id == rerun.job_id
    with pytest.raises(InfrastructureError, match="IntegrityError"), store.transaction() as db:
        db.execute(
            "INSERT INTO scan_jobs (job_id, job_key, installation_id, repository_id, owner, "
            "name, delivery_id, event, group_key, head_sha, check_name, context, state, "
            "created_at, updated_at, scan_key, trigger_kind) VALUES ('x', 'x', 42, 5001, 'o', "
            "'n', 'rerun-a', 'pull_request', 'g', ?, 'c', '{}', 'queued', 0, 0, 'k1', 'rerun')",
            (B,),
        )
    assert [e.execution for e in store.list_executions(42, REPO.id, "k1")] == [2, 1]
    assert [j.check_name for j in store.latest_jobs_for_commit(42, REPO.id, B)] == [
        "commitguard-app"
    ]


def test_merge_groups_are_never_revived_by_out_of_order_events(store: SqliteStateStore) -> None:
    common = dict(
        installation_id=42,
        repository_id=REPO.id,
        head_sha=B,
        head_ref="refs/heads/gh-readonly-queue/main/pr-1-" + C,
        base_sha=A,
        base_ref="refs/heads/main",
        pull_requests=(1,),
    )
    destroyed, changed = store.destroy_merge_group(**common, reason="dequeued", now=NOW)  # type: ignore[arg-type]
    assert changed
    assert destroyed.state is MergeGroupState.DESTROYED
    _again, changed = store.destroy_merge_group(**common, reason="dequeued", now=NOW)  # type: ignore[arg-type]
    assert not changed
    record, requested = store.record_merge_group(**common, delivery_id="late", now=NOW)  # type: ignore[arg-type]
    assert not requested
    assert record.state is MergeGroupState.DESTROYED


def test_policy_versions_are_immutable_in_the_database(store: SqliteStateStore) -> None:
    with store.transaction() as db:
        db.execute(
            "INSERT INTO organization_policy_versions (account_id, version, document, fingerprint, "
            "created_at) "
            "VALUES (1, 1, '{}', 'x', 0)"
        )
    for statement in (
        'UPDATE organization_policy_versions SET document = \'{"ai_coauthor":"allow"}\'',
        "DELETE FROM organization_policy_versions",
    ):
        with pytest.raises(Exception, match="state store error"), store.transaction() as db:
            db.execute(statement)
    assert store.query("SELECT COUNT(*) AS n FROM organization_policy_versions")[0]["n"] == 1


def test_migration_from_schema_2_backfills_executions(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    for statement in _statements(_SCHEMA_V1) + _statements(_SCHEMA_V2):
        db.execute(statement)
    db.execute("INSERT INTO meta VALUES ('schema_version', '2')")
    context = json.dumps(
        {
            "provider": "github",
            "event": "pull_request",
            "event_name": "pull_request",
            "base_sha": A,
            "head_sha": B,
        }
    )
    rows = [
        ("j1", "key-1", None, 1.0),
        ("j2", "manual-xyz", "alice", 2.0),
        ("j3", "manual-abc", "alice", 3.0),
    ]
    for job_id, key, requested_by, created in rows:
        db.execute(
            "INSERT INTO scan_jobs (job_id, job_key, installation_id, repository_id, owner, name, "
            "event, group_key, head_sha, check_name, context, state, attempts, created_at, "
            "updated_at, requested_by) VALUES (?, ?, 42, 5001, 'octo-org', 'project', "
            "'pull_request', 'pull_request:7', ?, 'commitguard-app', ?, 'passed', 1, ?, ?, ?)",
            (job_id, key, B, context, created, created, requested_by),
        )
    db.execute("INSERT INTO deliveries VALUES ('old-delivery', 'push', 'x', 1.0)")
    db.execute(
        "INSERT INTO organization_policy_versions (account_id, version, document, fingerprint, "
        "created_at) "
        "VALUES (1, 1, '{}', 'f', 1.0)"
    )
    db.commit()
    db.close()

    store = SqliteStateStore(path)
    assert store.query("SELECT value FROM meta")[0]["value"] == str(SCHEMA_VERSION)
    migrated = sorted(store.list_jobs(installation_id=42), key=lambda j: j.sequence)
    assert [(j.scan_key, j.execution, j.trigger) for j in migrated] == [
        ("key-1", 1, ScanTrigger.PULL_REQUEST),
        ("key-1", 2, ScanTrigger.MANUAL),
        ("key-1", 3, ScanTrigger.MANUAL),
    ]
    assert store.delivery_status("old-delivery") is EventProcessingStatus.PROCESSED
    [version] = store.query("SELECT kind, rollback_of FROM organization_policy_versions")
    assert (version["kind"], version["rollback_of"]) == ("change", None)
