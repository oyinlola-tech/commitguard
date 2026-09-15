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
        "SELECT rowid FROM audit_events a WHERE a.account_id = ? ORDER BY a.occurred_at DESC LIMIT 26",
        (1001,),
    )
    assert "audit_account" in audit and "TEMP B-TREE" not in audit
    violations = plan(
        "SELECT violation_id FROM violations v WHERE v.installation_id = ? "
        "ORDER BY v.last_detected_at DESC LIMIT 26",
        (42,),
    )
    assert "violations_detected" in violations and "TEMP B-TREE" not in violations
    findings = plan("SELECT * FROM findings WHERE job_id = ?", ("0" * 32,))
    assert "findings_job" in findings
