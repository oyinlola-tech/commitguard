"""Stale scans, duplicate deliveries, replay and concurrency."""

import threading

from commitguard.github.checks import APP_CHECK_NAME, APP_PUSH_CHECK_NAME
from commitguard.github.storage import JobState

ZERO = "0" * 40


def _feature(hub, message: str) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    base = hub.dev.git("rev-parse", "main")
    hub.dev.git("checkout", "-q", "-B", "feature", "main")
    head = hub.dev.commit(message)
    hub.dev.push("--force", "feature")
    return base, head


def test_older_pull_request_scan_finishing_later_cannot_affect_newer_head(app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    """Scan A queued, scan B queued, B finishes first, A runs afterwards."""
    app.install()
    base, head_a = _feature(hub, "feat: first\n")
    hub.dev.git("checkout", "-q", "feature")
    head_b = hub.dev.commit(gh.AI)
    hub.dev.push("feature")
    app.deliver("pull_request", app.open_pull_request(base, head_a, action="opened"))
    app.deliver("pull_request", app.open_pull_request(base, head_b, action="synchronize"))
    jobs = sorted(app.jobs(), key=lambda j: j.sequence)
    job_a, job_b = jobs

    assert app.service.worker.process(job_b.job_id) is JobState.FAILED
    assert app.service.worker.process(job_a.job_id) is JobState.CANCELLED
    assert app.latest_run(head_b)["conclusion"] == "failure"
    assert app.github.runs_for(head_a) == []  # A never published anything


def test_older_scan_of_same_commit_cannot_overwrite_newer_result(app, hub, gh, payloads) -> None:  # type: ignore[no-untyped-def]
    """Two scans own the same (commit, check) slot; the newer one wins even mid-flight."""
    app.install()
    first = hub.dev.git("rev-parse", "main")
    second = hub.dev.commit("docs: second\n")
    third = hub.dev.commit("docs: third\n")
    hub.dev.push("main")
    # Two different pushes that both end at `third` (e.g. a force push racing a push).
    app.deliver("push", payloads.push(first, third))
    app.deliver("push", payloads.push(second, third))
    older, newer = sorted(app.jobs(), key=lambda j: j.sequence)

    original_prepare = app.service.mirrors.prepare
    calls = {"n": 0}

    def prepare_with_race(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            # While the older scan fetches, the newer scan runs to completion.
            assert app.service.worker.process(newer.job_id) is JobState.PASSED
        return original_prepare(*args, **kwargs)

    app.service.mirrors.prepare = prepare_with_race  # type: ignore[method-assign]
    assert app.service.worker.process(older.job_id) is JobState.CANCELLED

    runs = app.github.runs_for(third, APP_PUSH_CHECK_NAME)
    assert len(runs) == 1  # the newer scan reused the run instead of creating a duplicate
    assert runs[0]["conclusion"] == "success"
    assert runs[0]["history"][-1] == "success"
    assert "| Commits scanned | 1 |" in runs[0]["output"]["summary"]  # newer range: second..third


def test_same_delivery_many_times_is_processed_once(app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub, gh.AI)
    payload = app.open_pull_request(base, head)
    delivery = "a4b1c2d3-0000-4000-8000-000000000001"
    statuses = [
        app.deliver("pull_request", payload, delivery=delivery).body["status"] for _ in range(5)
    ]
    assert statuses == ["queued", "duplicate", "duplicate", "duplicate", "duplicate"]
    assert app.run() == 1
    assert len(app.github.runs_for(head)) == 1
    assert app.service.metrics.value("webhooks_duplicate") == 4


def test_same_delivery_id_with_different_payload_is_rejected(app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub, "feat: ok\n")
    app.deliver("pull_request", app.open_pull_request(base, head), delivery="reused-id")
    forged = app.open_pull_request(base, base, number=9)
    result = app.deliver("pull_request", forged, delivery="reused-id")
    assert result.status == 409
    assert app.run() == 1
    assert [j.pull_request_number for j in app.jobs()] == [7]


def test_same_payload_with_new_delivery_ids_runs_one_scan(app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub, "feat: ok\n")
    payload = app.open_pull_request(base, head)
    results = [app.deliver("pull_request", payload).body["status"] for _ in range(3)]
    assert results == ["queued", "duplicate", "duplicate"]
    app.run()
    assert len(app.github.runs_for(head)) == 1


def test_concurrent_webhooks_and_workers(make_app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    app = make_app(workers=3)
    app.install()
    base, head = _feature(hub, gh.AI)
    payload = app.open_pull_request(base, head)
    app.service.start()
    try:
        threads = [
            threading.Thread(
                target=app.deliver,
                args=("pull_request", payload),
                kwargs={"delivery": f"delivery-{i % 3:04d}"},
            )
            for i in range(12)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        deadline = threading.Event()
        for _ in range(200):
            if app.jobs() and all(j.state.terminal for j in app.jobs()):
                break
            deadline.wait(0.05)
    finally:
        app.service.stop()
    assert len(app.jobs()) == 1
    assert app.jobs()[0].state is JobState.FAILED
    runs = app.github.runs_for(head, APP_CHECK_NAME)
    assert len(runs) == 1
    assert runs[0]["conclusion"] == "failure"
