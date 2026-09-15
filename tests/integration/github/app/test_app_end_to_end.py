"""End-to-end GitHub App scenarios: webhook -> queue -> worker -> real Git -> Check Run."""

from commitguard.audit.models import AuditEventType
from commitguard.github.checks import APP_CHECK_NAME, APP_PUSH_CHECK_NAME
from commitguard.github.storage import JobState

ZERO = "0" * 40


def test_pull_request_lifecycle(app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    # Step 1: install CommitGuard into the repository.
    assert app.install().status == 200
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    clean = hub.dev.commit("feat: clean change\n", files={"a.txt": "1\n"})
    hub.dev.push("feature")

    # Step 2: a clean pull request passes.
    result = app.deliver("pull_request", app.open_pull_request(base, clean))
    assert result.status == 202
    assert result.body == {"status": "queued"}
    assert app.github.check_runs == {}  # nothing is scanned on the request path
    assert app.run() == 1
    run = app.latest_run(clean)
    assert run["history"] == ["queued", "in_progress", "success"]
    assert run["output"]["title"] == "Passed: 1 commit(s) scanned"
    assert "All configured CommitGuard policies passed." in run["output"]["summary"]

    # Step 3: an AI-attributed commit fails; the whole PR range is evaluated.
    ai = hub.dev.commit(gh.AI)
    hub.dev.push("feature")
    synchronize = app.open_pull_request(base, ai, action="synchronize")
    assert app.deliver("pull_request", synchronize, delivery="delivery-ai").status == 202
    assert app.run() == 1
    failed = app.latest_run(ai)
    assert failed["conclusion"] == "failure"
    assert "BLOCKED" in failed["output"]["summary"]
    assert "| Commits scanned | 2 |" in failed["output"]["summary"]
    assert "**Rule:** ai_coauthor" in failed["output"]["text"]
    assert "Remediation" in failed["output"]["text"]

    # Step 4: the developer rewrites the branch; the new head passes.
    hub.dev.git("reset", "-q", "--hard", clean)
    fixed = hub.dev.commit("feat: add payment service\n")
    hub.dev.push("--force", "feature")
    app.deliver("pull_request", app.open_pull_request(base, fixed, action="synchronize"))
    assert app.run() == 1
    assert app.latest_run(fixed)["conclusion"] == "success"

    # Step 5: replaying the earlier webhook does not re-run or duplicate anything.
    requests_before = len(app.github.requests)
    replay = app.deliver("pull_request", synchronize, delivery="delivery-ai")
    assert (replay.status, replay.body) == (200, {"status": "duplicate"})
    redelivered = app.deliver("pull_request", synchronize)  # new delivery ID, same event
    assert (redelivered.status, redelivered.body) == (202, {"status": "duplicate"})
    assert app.run() == 0
    assert len(app.github.requests) == requests_before

    # Step 6: every result stays attached to exactly the commit it evaluated.
    assert app.latest_run(fixed)["conclusion"] == "success"
    assert app.latest_run(ai)["conclusion"] == "failure"
    assert len(app.github.runs_for(fixed)) == 1
    assert {j.state for j in app.jobs()} == {JobState.PASSED, JobState.FAILED}

    events = [e.type for e in app.service.store.list_audit_events(installation_id=42, limit=100)]
    for expected in (
        AuditEventType.INSTALLATION_CREATED,
        AuditEventType.SCAN_QUEUED,
        AuditEventType.REPOSITORY_SCANNED,
        AuditEventType.SCAN_PASSED,
        AuditEventType.SCAN_FAILED,
        AuditEventType.POLICY_VIOLATION,
    ):
        assert expected in events
    assert app.service.metrics.value("scans_completed") == 3


def test_push_events(app, hub, gh, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    before = hub.dev.git("rev-parse", "HEAD")
    ai = hub.dev.commit(gh.AI)
    hub.dev.push("main")
    assert app.deliver("push", payloads.push(before, ai)).status == 202
    assert app.run() == 1
    run = app.latest_run(ai, APP_PUSH_CHECK_NAME)
    assert run["conclusion"] == "failure"
    assert app.github.runs_for(ai, APP_CHECK_NAME) == []  # a push never writes the PR check

    # A new branch is limited by the default branch tip (from the API, not the payload).
    hub.dev.git("checkout", "-q", "-b", "topic")
    clean = hub.dev.commit("docs: explain setup\n")
    hub.dev.push("topic")
    app.deliver("push", payloads.push(ZERO, clean, ref="refs/heads/topic"))
    assert app.run() == 1
    topic = app.latest_run(clean, APP_PUSH_CHECK_NAME)
    assert topic["conclusion"] == "success"
    assert "| Commits scanned | 1 |" in topic["output"]["summary"]

    # Deleted branches and tags are not scanned.
    deleted = app.deliver("push", payloads.push(clean, ZERO, ref="refs/heads/topic"))
    assert (deleted.status, deleted.body) == (202, {"status": "ignored"})
    tag = app.deliver("push", payloads.push(ZERO, clean, ref="refs/tags/v1"))
    assert tag.body == {"status": "ignored"}
    assert app.run() == 0


def test_closed_pull_requests_are_not_scanned(app, hub, gh, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit(gh.AI)
    hub.dev.push("feature")
    app.deliver("pull_request", app.open_pull_request(base, head))
    closed = app.deliver("pull_request", payloads.pr(base, head, action="closed"))
    assert closed.status == 200
    assert app.run() == 0  # the queued scan was cancelled
    assert [j.state for j in app.jobs()] == [JobState.CANCELLED]
    assert app.github.check_runs == {}

    merged = app.deliver(
        "pull_request", payloads.pr(base, head, number=8, action="closed", merged=True)
    )
    assert merged.status == 200
    assert app.run() == 0
    events = [e.type for e in app.service.store.list_audit_events(installation_id=42)]
    assert AuditEventType.PULL_REQUEST_MERGED in events


def test_pull_request_closed_before_worker_runs(app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit("feat: x\n")
    hub.dev.push("feature")
    app.deliver("pull_request", app.open_pull_request(base, head))
    app.github.pulls[(5001, 7)]["state"] = "closed"  # closed webhook not received yet
    assert app.run() == 1
    assert [j.state for j in app.jobs()] == [JobState.CANCELLED]
    assert app.github.check_runs == {}


def test_warning_only_pull_request_passes_with_warnings(app, hub) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit(
        "chore: bump\n",
        author="dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>",
    )
    hub.dev.push("feature")
    app.deliver("pull_request", app.open_pull_request(base, head))
    app.run()
    run = app.latest_run(head)
    assert run["conclusion"] == "success"
    assert run["output"]["title"] == "Passed with 1 warning(s)"
    assert "### Warnings" in run["output"]["text"]


def test_worker_restart_recovers_queued_jobs(make_app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    first = make_app()
    first.install()
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit(gh.AI)
    hub.dev.push("feature")
    first.deliver("pull_request", first.open_pull_request(base, head))
    # The process dies before a worker picks the job up: the in-memory queue is lost.
    first.service.queue = type(first.service.queue)()
    assert first.run() == 0
    assert first.service.recover() == 1
    assert first.run() == 1
    assert first.latest_run(head)["conclusion"] == "failure"
