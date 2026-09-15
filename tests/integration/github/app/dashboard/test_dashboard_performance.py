"""Representative data volume: 100 repositories, 10,000 scans, 50,000 findings,
100,000 audit events (test targets from the Phase 6 specification, not a claim
of production scale).

Every list endpoint must return one bounded page quickly, queries must use
indexes for the default orderings, and a detail page must not load the whole
collection. Timings are generous so the test is stable on slow CI machines;
the numbers measured on the development machine are recorded in
docs/dashboard.md.
"""

import time

import pytest

ALICE = (501, "alice")
BUDGET_SECONDS = 1.5


@pytest.fixture
def large(dash, seed):  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    dataset = seed(dash, repositories=100, scans=10_000, findings=50_000, audit_events=100_000)
    seeded = time.perf_counter() - started
    browser = dash.sign_in(ALICE, {42: {r.id for r in dataset.repositories}})
    return browser, dataset, seeded


def _timed(browser, path, **query):  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    result = browser.get(path, **query)
    elapsed = time.perf_counter() - started
    assert result.status == 200, result.raw
    return result, elapsed


def test_large_dataset_pages_stay_fast_and_bounded(large, capsys) -> None:  # type: ignore[no-untyped-def]
    browser, dataset, seeded = large
    timings = {}
    checks = [
        ("overview", "/api/v1/dashboard/overview", {}),
        ("repositories", "/api/v1/repositories", {}),
        ("repositories:risk", "/api/v1/repositories", {"sort": "risk", "limit": 100}),
        ("scans", "/api/v1/scans", {}),
        ("scans:blocked", "/api/v1/scans", {"result": "blocked"}),
        ("scans:rule", "/api/v1/scans", {"rule": "ai_coauthor"}),
        ("violations", "/api/v1/violations", {}),
        ("violations:severity", "/api/v1/violations", {"sort": "severity", "status": "open"}),
        ("violations:search", "/api/v1/violations", {"q": "service-042"}),
        ("audit", "/api/v1/audit", {}),
        ("audit:type", "/api/v1/audit", {"type": "scan_queued"}),
        ("scan detail", f"/api/v1/scans/{dataset.scan_ids[-1]}", {}),
        ("violation detail", f"/api/v1/violations/{dataset.violation_ids[0]}", {}),
        ("repository detail", f"/api/v1/repositories/{dataset.repositories[5].id}", {}),
    ]
    for name, path, query in checks:
        result, elapsed = _timed(browser, path, **query)
        timings[name] = elapsed
        if isinstance(result.data, list):
            assert len(result.data) <= result.meta["limit"] <= 100
        assert len(result.raw) < 600_000, name  # no endpoint ships the collection
        # Second page through the cursor is as fast as the first.
        if isinstance(result.data, list) and result.meta.get("next_cursor"):
            _, second = _timed(browser, path, cursor=result.meta["next_cursor"], **query)
            timings[f"{name} (page 2)"] = second
    overview = browser.get("/api/v1/dashboard/overview").data
    assert overview["summary"]["repositories_monitored"] == 100
    with capsys.disabled():
        print(f"\nseeded dataset in {seeded:.1f}s")
        for name, elapsed in timings.items():
            print(f"  {name:<32} {elapsed * 1000:7.1f} ms")
    slow = {k: round(v, 2) for k, v in timings.items() if v > BUDGET_SECONDS}
    assert not slow


def test_default_orderings_use_indexes(large, dash) -> None:  # type: ignore[no-untyped-def]
    store = dash.store

    def plan(sql: str, params: tuple) -> str:  # type: ignore[type-arg]
        rows = store.query("EXPLAIN QUERY PLAN " + sql, params)
        return " | ".join(r["detail"] for r in rows)

    audit = plan(
        "SELECT rowid FROM audit_events a WHERE a.account_id = ? "
        "ORDER BY a.occurred_at DESC LIMIT 26",
        (1001,),
    )
    assert "audit_account" in audit
    assert "TEMP B-TREE" not in audit
    violations = plan(
        "SELECT violation_id FROM violations v WHERE v.installation_id = ? "
        "ORDER BY v.last_detected_at DESC LIMIT 26",
        (42,),
    )
    assert "violations_detected" in violations
    assert "TEMP B-TREE" not in violations
    findings = plan("SELECT * FROM findings WHERE job_id = ?", ("0" * 32,))
    assert "findings_job" in findings


# --------------------------------------------------------------------------- #
# Phase 7 volumes: 100,000 notifications, 100,000 event records, 10,000 policy versions
# --------------------------------------------------------------------------- #
def _seed_phase7(dash, *, notifications: int, events: int, versions: int) -> None:  # type: ignore[no-untyped-def]
    import json
    import uuid

    from commitguard.security.hashing import sha256_hex

    store = dash.store
    now = dash.clock().timestamp()
    types = ("high_violation", "policy_changed", "merge_queue_failure", "installation_reconnected")
    with store.transaction() as db:
        event_rows, inbox_rows = [], []
        for i in range(notifications):
            event_id = uuid.uuid4().hex
            notification_type = types[i % len(types)]
            repository = (
                5001 if notification_type in ("high_violation", "merge_queue_failure") else None
            )
            event_rows.append(
                (
                    event_id,
                    1001,
                    notification_type,
                    "high",
                    42,
                    repository,
                    "scan",
                    uuid.uuid4().hex,
                    f"seed:{i}",
                    f"Seeded notification {i}",
                    "body",
                    "{}",
                    1,
                    now - i,
                    now - i,
                    now - i,
                )
            )
            inbox_rows.append(
                (
                    uuid.uuid4().hex,
                    event_id,
                    1001,
                    501,
                    "unread" if i % 3 else "read",
                    "high",
                    now - i,
                    now - i,
                    now - i,
                )
            )
        db.executemany(
            "INSERT INTO notification_events (event_id, account_id, type, severity, "
            "installation_id, "
            "repository_id, resource_type, resource_id, dedup_key, title, body, metadata, "
            "occurrences, created_at, last_occurred_at, dispatched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            event_rows,
        )
        db.executemany(
            "INSERT INTO notifications (notification_id, event_id, account_id, user_id, state, "
            "severity, created_at, updated_at, sort_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            inbox_rows,
        )
        db.executemany(
            "INSERT INTO deliveries (delivery_id, event, body_sha256, received_at, provider, "
            "action, status, attempts, installation_id, repository_id, updated_at) "
            "VALUES (?, 'pull_request', 'x', ?, 'github', 'synchronize', 'processed', 1, 42, "
            "5001, ?)",
            [(f"seeded-delivery-{i:08d}", now - i, now - i) for i in range(events)],
        )
        documents = []
        for version in range(1, versions + 1):
            document = json.dumps(
                {"ai_coauthor": "block" if version % 2 else "warn"}, separators=(",", ":")
            )
            documents.append(
                (
                    1001,
                    version,
                    document,
                    sha256_hex(document.encode()),
                    now - (versions - version),
                    501,
                    "alice",
                    "seed",
                )
            )
        db.executemany(
            "INSERT INTO organization_policy_versions (account_id, version, document, fingerprint, "
            "created_at, created_by_id, created_by_login, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            documents,
        )


def test_phase7_volumes_stay_fast_and_bounded(dash, capsys) -> None:  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    _seed_phase7(dash, notifications=100_000, events=100_000, versions=10_000)
    seeded = time.perf_counter() - started
    browser = dash.sign_in(ALICE)
    timings = {}
    checks = [
        ("notifications", "/api/v1/notifications", {}),
        ("notifications:unread", "/api/v1/notifications", {"state": "unread"}),
        ("notifications:scans", "/api/v1/notifications", {"category": "scans"}),
        ("notification counts", "/api/v1/notifications/counts", {}),
        ("policy", "/api/v1/policies/1001", {}),
        ("policy versions", "/api/v1/policies/1001/versions", {}),
        ("policy diff", "/api/v1/policies/1001/diff", {"from": "1", "to": "10000"}),
        ("notification settings", "/api/v1/organizations/1001/notification-settings", {}),
    ]
    for name, path, query in checks:
        result, elapsed = _timed(browser, path, **query)
        timings[name] = elapsed
        if isinstance(result.data, list):
            assert len(result.data) <= result.meta["limit"] <= 100
            if result.meta.get("next_cursor"):
                _, second = _timed(browser, path, cursor=result.meta["next_cursor"], **query)
                timings[f"{name} (page 2)"] = second
        assert len(result.raw) < 600_000, name
    counts = browser.get("/api/v1/notifications/counts").data
    assert (counts["unread"], counts["capped"]) == (1000, True)  # bounded count for the badge
    started = time.perf_counter()
    assert browser.post("/api/v1/notifications/read-all", {}).data["updated"] == 10_000  # bounded
    timings["read-all (10,000)"] = time.perf_counter() - started
    with capsys.disabled():
        print(f"\nseeded Phase 7 volumes in {seeded:.1f}s")
        for name, elapsed in timings.items():
            print(f"  {name:<32} {elapsed * 1000:7.1f} ms")
    slow = {k: round(v, 2) for k, v in timings.items() if v > BUDGET_SECONDS}
    assert not slow

    store = dash.store
    plan = " | ".join(
        r["detail"]
        for r in store.query(
            "EXPLAIN QUERY PLAN SELECT notification_id FROM notifications n WHERE n.user_id = ? "
            "AND n.state = 'unread' ORDER BY n.sort_at DESC LIMIT 26",
            (501,),
        )
    )
    assert "notifications_user_state" in plan
    assert "TEMP B-TREE" not in plan
    deliveries_plan = " | ".join(
        r["detail"]
        for r in store.query(
            "EXPLAIN QUERY PLAN SELECT delivery_id FROM notification_deliveries "
            "WHERE status = 'pending' "
            "AND next_retry_at <= ?",
            (0,),
        )
    )
    assert "notification_deliveries_due" in deliveries_plan
