"""Phase 7 through the GitHub App: merge queue, check re-runs, event records, recovery.

Everything runs the real stack (webhook -> event record -> queue -> worker -> real Git
mirror -> detection and policy engines -> Check Run) against the offline GitHub model.
"""

import contextlib
import json
from datetime import timedelta

from commitguard.audit.models import AuditEventType
from commitguard.github.checks import APP_CHECK_NAME
from commitguard.github.client import HttpResponse, TransportError
from commitguard.github.storage import EventProcessingStatus, JobState, ScanTrigger

CLEAN = "feat: clean change\n"


def _events(app, event_type=None):  # type: ignore[no-untyped-def]
    events = app.service.store.list_audit_events(installation_id=42, limit=1000)
    if event_type is None:
        return events
    return [e for e in events if e.type is event_type]


def _feature(hub, message: str = CLEAN) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    base = hub.dev.git("rev-parse", "main")
    hub.dev.git("checkout", "-q", "-B", "feature", base)
    head = hub.dev.commit(message, files={"feature.txt": message})
    hub.dev.push("--force", "feature")
    return base, head


def _jobs(app, **filters):  # type: ignore[no-untyped-def]
    jobs = sorted(app.jobs(), key=lambda j: j.sequence)
    return [j for j in jobs if all(getattr(j, k) == v for k, v in filters.items())]


# --------------------------------------------------------------------------- #
# Merge queue
# --------------------------------------------------------------------------- #
def test_merge_group_is_validated_on_the_merge_group_commit(app, hub, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub)
    app.deliver("pull_request", app.open_pull_request(base, head))
    app.run()
    assert app.latest_run(head)["conclusion"] == "success"  # the pull request passed

    queue_base, group = payloads.push_merge_group(hub, "main", "feature")
    result = app.deliver("merge_group", payloads.merge_group(queue_base, group))
    assert result.body == {"status": "queued"}
    assert app.run() == 1

    run = app.latest_run(group)
    assert run["conclusion"] == "success"
    assert run["head_sha"] == group  # never the pull request head
    assert len(app.github.runs_for(head)) == 1  # the pull request's check was not touched
    [job] = _jobs(app, event="merge_group")
    assert (job.trigger, job.base_sha, job.head_sha) == (ScanTrigger.MERGE_GROUP, queue_base, group)
    record = app.service.store.get_merge_group(42, payloads.REPO.id, group)
    assert record.pull_requests == (7,)  # parsed from GitHub's queue ref, display only
    assert [e.data.get("pull_requests") for e in _events(app, AuditEventType.MERGE_GROUP_CREATED)]
    assert _events(app, AuditEventType.MERGE_GROUP_PASSED)
    assert app.service.metrics.value("merge_groups_scanned") == 1


def test_merge_group_blocks_violations_the_pull_request_check_did_not_see(
    app, hub, gh, payloads
) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub)
    app.deliver("pull_request", app.open_pull_request(base, head))
    app.run()
    assert app.latest_run(head)["conclusion"] == "success"

    # Changes queued ahead of it bring an AI-attributed commit into the base.
    hub.dev.git("checkout", "-q", "main")
    queued_ahead = hub.dev.commit(gh.AI, files={"ahead.txt": "x\n"})
    hub.dev.git("checkout", "-q", "feature")
    queue_base = hub.dev.git("rev-parse", f"{queued_ahead}~1")
    hub.dev.git("checkout", "-q", "--detach", queued_ahead)
    hub.dev.git("merge", "-q", "--no-ff", "--no-verify", "-m", "Merge #7", head)
    group = hub.dev.git("rev-parse", "HEAD")
    hub.dev.push(f"HEAD:refs/heads/gh-readonly-queue/main/pr-7-{head}")
    hub.dev.git("checkout", "-q", "feature")

    app.deliver("merge_group", payloads.merge_group(queue_base, group))
    app.run()
    run = app.latest_run(group)
    assert run["conclusion"] == "failure"
    assert "BLOCKED" in run["output"]["summary"]
    assert app.latest_run(head)["conclusion"] == "success"  # historical PR result unchanged
    assert _events(app, AuditEventType.MERGE_GROUP_BLOCKED)
    [event] = app.service.store.query(
        "SELECT type, resource_type FROM notification_events WHERE type = 'merge_queue_failure'"
    )
    assert event["resource_type"] == "scan"


def test_merge_group_that_cannot_be_validated_fails_closed(app, hub, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    _feature(hub)
    queue_base, group = payloads.push_merge_group(hub, "main", "feature")
    app.github.fail("GET", r"/repositories/5001$", TransportError("reset"))
    app.deliver("merge_group", payloads.merge_group(queue_base, group))
    app.run()
    [job] = _jobs(app, event="merge_group")
    assert (job.state, job.failure_kind) == (JobState.ERROR, "infrastructure")
    assert app.github.runs_for(group) == []  # never a success; the queue keeps waiting / times out
    assert _events(app, AuditEventType.MERGE_GROUP_SCAN_FAILED)
    assert app.service.metrics.value("merge_groups_failed") == 1


def test_recreated_merge_groups_get_their_own_validation(app, hub, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    _feature(hub)
    base_a, group_a = payloads.push_merge_group(hub, "main", "feature")
    app.deliver("merge_group", payloads.merge_group(base_a, group_a))
    app.run()
    app.deliver(
        "merge_group",
        payloads.merge_group(base_a, group_a, action="destroyed", reason="invalidated"),
    )
    # The base branch moved: GitHub builds merge group B on the new base.
    hub.dev.git("checkout", "-q", "main")
    hub.dev.commit("chore: unrelated\n", files={"other.txt": "1\n"})
    hub.dev.push("main")
    hub.dev.git("checkout", "-q", "feature")
    base_b, group_b = payloads.push_merge_group(hub, "main", "feature")
    app.deliver("merge_group", payloads.merge_group(base_b, group_b))
    app.run()
    assert group_a != group_b
    assert app.latest_run(group_a)["conclusion"] == "success"
    assert app.latest_run(group_b)["conclusion"] == "success"
    jobs = _jobs(app, event="merge_group")
    assert [j.head_sha for j in jobs] == [group_a, group_b]
    assert jobs[0].scan_key != jobs[1].scan_key  # no result is reused across groups


def test_duplicate_stale_and_forged_merge_group_events(app, hub, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    _feature(hub)
    queue_base, group = payloads.push_merge_group(hub, "main", "feature")
    payload = payloads.merge_group(queue_base, group)

    # Replayed delivery (same ID) and a second delivery for the same group: one scan.
    assert app.deliver("merge_group", payload, delivery="merge-group-delivery-1").body == {
        "status": "queued"
    }
    assert app.deliver("merge_group", payload, delivery="merge-group-delivery-1").body == {
        "status": "duplicate"
    }
    assert app.deliver("merge_group", payload, delivery="merge-group-delivery-2").body == {
        "status": "duplicate"
    }
    assert len(_jobs(app, event="merge_group")) == 1

    # Destroyed before the worker ran: the scan is cancelled as stale, nothing is published.
    app.deliver(
        "merge_group",
        payloads.merge_group(queue_base, group, action="destroyed", reason="dequeued"),
    )
    app.run()
    [job] = _jobs(app, event="merge_group")
    assert job.state is JobState.CANCELLED
    assert app.github.runs_for(group) == []

    # A late checks_requested cannot revive a destroyed group.
    assert app.deliver("merge_group", payload, delivery="merge-group-delivery-3").body == {
        "status": "duplicate"
    }
    assert app.run() == 0

    # Forged shapes are rejected before anything is stored.
    wrong_ref = payloads.merge_group(queue_base, group, head_ref="refs/heads/feature")
    assert app.deliver("merge_group", wrong_ref).status == 400
    other_base = payloads.merge_group(queue_base, group)
    other_base["merge_group"]["head_ref"] = f"refs/heads/gh-readonly-queue/release/pr-7-{'e' * 40}"
    assert app.deliver("merge_group", other_base).status == 400
    mismatched = payloads.merge_group(queue_base, group)
    mismatched["merge_group"]["head_commit"]["id"] = "a" * 40
    assert app.deliver("merge_group", mismatched).status == 400


def test_merge_group_for_a_repository_outside_the_installation_is_not_scanned(
    app, hub, payloads
) -> None:  # type: ignore[no-untyped-def]
    app.install()
    _feature(hub)
    queue_base, group = payloads.push_merge_group(hub, "main", "feature")
    other = payloads.REPO.model_copy(update={"id": 7777, "name": "someone-elses"})
    app.remotes.paths[7777] = hub.bare
    app.deliver("merge_group", payloads.merge_group(queue_base, group, repository=other))
    app.run()
    jobs = app.service.store.list_jobs(installation_id=42, repository_id=7777)
    assert [(j.state, j.failure_kind) for j in jobs] == [(JobState.ERROR, "authorization")]
    assert app.github.check_runs == {}


def test_merged_group_moves_violations_to_the_base_branch(app, hub, gh, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    _feature(hub, gh.AI)
    queue_base, group = payloads.push_merge_group(hub, "main", "feature")
    app.deliver("merge_group", payloads.merge_group(queue_base, group))
    app.run()
    store = app.service.store
    [exposure] = store.query("SELECT kind, active FROM violation_exposures")
    assert (exposure["kind"], exposure["active"]) == ("merge_group", 1)
    app.deliver(
        "merge_group", payloads.merge_group(queue_base, group, action="destroyed", reason="merged")
    )
    rows = store.query("SELECT kind, active FROM violation_exposures ORDER BY kind")
    assert [(r["kind"], r["active"]) for r in rows] == [("branch", 1), ("merge_group", 0)]


# --------------------------------------------------------------------------- #
# Check re-runs
# --------------------------------------------------------------------------- #
def test_rerun_creates_a_new_execution_and_preserves_history(app, hub, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub)
    app.deliver("pull_request", app.open_pull_request(base, head))
    app.github.fail("GET", r"/pulls/7$", HttpResponse(503, {}, b"{}"))  # GitHub outage
    app.run()
    [original] = _jobs(app)
    assert original.state is JobState.ERROR

    app.github.failures.clear()
    run = app.github.runs_for(head)
    assert run == []  # the outage happened before a check run existed
    # GitHub shows the check as missing; the developer re-runs from a completed check instead.
    app.deliver("pull_request", app.open_pull_request(base, head, action="reopened"))
    app.run()
    first_run = app.latest_run(head)
    assert first_run["conclusion"] == "success"

    rerun = app.deliver("check_run", payloads.check_run(first_run), delivery="rerun-delivery-1")
    assert rerun.body == {"status": "queued"}
    assert app.deliver(
        "check_run", payloads.check_run(first_run), delivery="rerun-delivery-1"
    ).body == {"status": "duplicate"}
    assert app.run() == 1
    jobs = _jobs(app)
    assert [(j.execution, j.trigger, j.state) for j in jobs] == [
        (1, ScanTrigger.PULL_REQUEST, JobState.ERROR),
        (2, ScanTrigger.PULL_REQUEST, JobState.PASSED),
        (3, ScanTrigger.RERUN, JobState.PASSED),
    ]
    assert len({j.scan_key for j in jobs}) == 1
    assert len(app.github.runs_for(head)) == 1  # the same check run was reset and completed
    assert app.latest_run(head)["history"][-3:] == ["queued", "in_progress", "success"]
    assert _events(app, AuditEventType.CHECK_RERUN_REQUESTED)
    assert _events(app, AuditEventType.SCAN_STARTED)
    assert app.service.metrics.value("check_reruns") == 1


def test_rerun_while_an_execution_is_queued_does_not_pile_up(app, hub, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub)
    app.deliver("pull_request", app.open_pull_request(base, head))
    app.run()
    run = app.latest_run(head)
    assert app.deliver("check_run", payloads.check_run(run)).body == {"status": "queued"}
    assert app.deliver("check_run", payloads.check_run(run)).body == {"status": "duplicate"}
    assert app.deliver("check_suite", payloads.check_suite(head)).body == {"status": "duplicate"}
    assert app.run() == 1


def test_stale_and_mismatched_reruns_are_rejected(app, hub, gh, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, old_head = _feature(hub)
    app.deliver("pull_request", app.open_pull_request(base, old_head))
    app.run()
    old_run = app.latest_run(old_head)
    new_head = hub.dev.commit(gh.AI)
    hub.dev.push("feature")
    app.deliver("pull_request", app.open_pull_request(base, new_head, action="synchronize"))
    app.run()

    # Re-running the outdated commit's check must not replace the pull request's state.
    assert app.deliver("check_run", payloads.check_run(old_run)).body == {"status": "ignored"}
    reasons = [e.data["reason"] for e in _events(app, AuditEventType.CHECK_RERUN_REJECTED)]
    assert reasons == ["a newer commit has been scanned for this pull request or branch"]

    new_run = app.latest_run(new_head)
    wrong_sha = payloads.check_run({**new_run, "head_sha": old_head})
    other_repo = payloads.check_run(
        new_run, repository=payloads.REPO.model_copy(update={"id": 7777, "name": "x"})
    )
    unknown = payloads.check_run({**new_run, "external_id": "f" * 32})
    renamed = payloads.check_run({**new_run, "name": "commitguard-app/push"})
    for payload in (wrong_sha, other_repo, unknown, renamed):
        assert app.deliver("check_run", payload).body == {"status": "ignored"}
    other_app = payloads.check_run(new_run, app_id=999)
    assert app.deliver("check_run", other_app).body == {"status": "ignored"}
    assert app.deliver("check_run", payloads.check_run(new_run, action="completed")).status == 202
    assert app.run() == 0
    assert len(_events(app, AuditEventType.CHECK_RERUN_REJECTED)) == 5


def test_rerun_after_installation_loses_access_fails_closed(app, hub, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub)
    app.deliver("pull_request", app.open_pull_request(base, head))
    app.run()
    run = app.latest_run(head)
    app.github.installations[42].permissions["checks"] = "read"  # permissions changed on GitHub
    app.deliver(
        "installation", payloads.installation("new_permissions_accepted", 42, (payloads.REPO,))
    )
    app.deliver("check_run", payloads.check_run(run))
    app.run()
    rerun = _jobs(app)[-1]
    assert (rerun.trigger, rerun.state, rerun.failure_kind) == (
        ScanTrigger.RERUN,
        JobState.ERROR,
        "authorization",
    )
    assert app.latest_run(head)["conclusion"] == "success"  # the earlier result is not rewritten
    [event] = app.service.store.query(
        "SELECT type FROM notification_events WHERE type = 'check_rerun_failed'"
    )
    assert event["type"] == "check_rerun_failed"


def test_check_suite_rerun_reruns_every_commitguard_check_on_the_commit(app, hub, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub)
    app.deliver("pull_request", app.open_pull_request(base, head))
    app.deliver("push", payloads.push(base, head, ref="refs/heads/feature"))
    app.run()
    result = app.deliver("check_suite", payloads.check_suite(head))
    assert result.body == {"status": "queued"}
    assert app.run() == 2
    reruns = _jobs(app, trigger=ScanTrigger.RERUN)
    assert sorted(j.check_name for j in reruns) == [APP_CHECK_NAME, "commitguard-app/push"]
    unknown = app.deliver("check_suite", payloads.check_suite("c" * 40))
    assert unknown.body == {"status": "ignored"}


# --------------------------------------------------------------------------- #
# Event records, replay and recovery
# --------------------------------------------------------------------------- #
def test_replayed_webhook_is_one_logical_event(app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub, gh.AI)
    payload = app.open_pull_request(base, head)
    assert app.deliver("pull_request", payload, delivery="same-delivery-id").body == {
        "status": "queued"
    }
    assert app.deliver("pull_request", payload, delivery="same-delivery-id").body == {
        "status": "duplicate"
    }
    app.run()
    assert len(app.jobs()) == 1
    assert len(_events(app, AuditEventType.SCAN_QUEUED)) == 1
    assert len(app.github.runs_for(head)) == 1
    notifications = app.service.store.query("SELECT occurrences FROM notification_events")
    assert [n["occurrences"] for n in notifications] == [1]
    assert app.service.store.delivery_status("same-delivery-id") is EventProcessingStatus.PROCESSED
    assert app.service.metrics.value("github_events_replayed") == 1


def test_failed_event_processing_is_retried_on_redelivery(app, hub, payloads, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    service = app.service
    original = service.installations.handle_installation
    calls = {"n": 0}

    def flaky(event):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database unavailable")
        return original(event)

    monkeypatch.setattr(service.installations, "handle_installation", flaky)
    app.github.add_installation()
    payload = payloads.installation("created", 42, (payloads.REPO,))
    with contextlib.suppress(RuntimeError):
        app.deliver("installation", payload, delivery="install-1")
    assert service.store.delivery_status("install-1") is EventProcessingStatus.FAILED
    assert service.store.get_installation(42) is None
    # GitHub redelivers with the same delivery ID: processed, not dropped as a duplicate.
    assert app.deliver("installation", payload, delivery="install-1").status == 200
    assert service.store.get_installation(42) is not None
    assert service.store.delivery_status("install-1") is EventProcessingStatus.PROCESSED
    assert app.deliver("installation", payload, delivery="install-1").body == {
        "status": "duplicate"
    }
    assert service.metrics.value("github_events_failed") == 1


def test_abandoned_processing_is_recovered(app, payloads, make_app) -> None:  # type: ignore[no-untyped-def]
    store = app.service.store
    from datetime import UTC, datetime

    long_ago = datetime.now(UTC) - timedelta(hours=1)
    store.record_delivery("crashed", "push", "digest", long_ago)  # never finished
    assert app.service.recovery.run_once()["stuck_deliveries"] == 1
    assert store.delivery_status("crashed") is EventProcessingStatus.FAILED


def test_infrastructure_failures_are_retried_automatically_and_bounded(
    make_app, hub, payloads
) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    clock = {"now": datetime(2026, 9, 1, 12, 0, tzinfo=UTC)}
    app = make_app(now=lambda: clock["now"])
    app.install()
    base, head = _feature(hub)
    app.deliver("pull_request", app.open_pull_request(base, head))
    app.github.fail("GET", r"/repositories/5001$", TransportError("reset"), times=12)
    app.run()
    assert _jobs(app)[0].state is JobState.ERROR
    recovery = app.service.recovery
    assert recovery.retry_failed_scans() == 0  # backoff: not yet
    clock["now"] += timedelta(minutes=6)
    assert recovery.retry_failed_scans() == 1
    app.run()
    assert _jobs(app)[-1].state is JobState.ERROR  # still down
    clock["now"] += timedelta(minutes=21)
    app.github.failures.clear()
    assert recovery.retry_failed_scans() == 1
    app.run()
    jobs = _jobs(app)
    assert [(j.trigger, j.state) for j in jobs] == [
        (ScanTrigger.PULL_REQUEST, JobState.ERROR),
        (ScanTrigger.RETRY, JobState.ERROR),
        (ScanTrigger.RETRY, JobState.PASSED),
    ]
    assert app.latest_run(head)["conclusion"] == "success"
    clock["now"] += timedelta(hours=1)
    assert recovery.retry_failed_scans() == 0  # nothing left to retry; never unbounded
    assert len(_events(app, AuditEventType.SCAN_RETRY_SCHEDULED)) == 2


def test_job_crashing_on_every_attempt_is_audited_and_fails_its_check(
    app, hub, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    app.install()
    base, head = _feature(hub)
    app.deliver("pull_request", app.open_pull_request(base, head))
    store = app.service.store
    [job] = app.jobs()
    # Three workers died mid-scan after creating the check run (lease expired each time).
    store.update_job(job.job_id, datetime.now(UTC), check_run_id=None)
    with store.transaction() as db:
        db.execute(
            "UPDATE scan_jobs SET attempts = 3, state = 'running', lease_expires_at = 0 "
            "WHERE job_id = ?",
            (job.job_id,),
        )
    assert app.service.worker.process(job.job_id) is None
    final = store.get_job(job.job_id)
    assert (final.state, final.message) == (
        JobState.ERROR,
        "scan abandoned after repeated attempts",
    )
    assert [e.data["reason"] for e in _events(app, AuditEventType.SCAN_ERROR)] == [
        "scan abandoned after repeated attempts"
    ]


def test_event_records_never_store_payload_bodies(app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub, gh.AI)
    app.deliver("pull_request", app.open_pull_request(base, head), delivery="delivery-0009")
    [row] = app.service.store.query("SELECT * FROM deliveries WHERE delivery_id = 'delivery-0009'")
    stored = json.dumps(dict(row))
    assert "Claude" not in stored
    assert "noreply@anthropic.com" not in stored
    assert (row["provider"], row["status"], row["action"]) == ("github", "processed", "opened")
