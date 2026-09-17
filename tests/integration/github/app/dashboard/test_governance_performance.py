"""Organization governance at volume: 1,000 repositories, 20,000 scans, 100,000 findings.

Budgets are generous so the test is stable on slow CI machines; the timings
measured on the development machine (and a larger 10,000-repository run with
``scripts/benchmark_governance.py``) are recorded in docs/security-posture.md.
"""

import time

import pytest

ORG = 1001
INSTALLATION = 42
ALICE = (501, "alice")
ADA = (504, "ada")


@pytest.fixture(autouse=True)
def _no_rate_limits(monkeypatch):  # type: ignore[no-untyped-def]
    from commitguard.security.rate_limit import RequestRateLimiter

    monkeypatch.setattr(RequestRateLimiter, "allow", lambda self, key: True)


@pytest.fixture
def organization(dash, seed):  # type: ignore[no-untyped-def]
    dataset = seed(dash, repositories=1000, scans=20_000, findings=100_000, audit_events=10_000)
    ids = {r.id for r in dataset.repositories}
    dash.app.service.governance.inventory.discovered(ORG, INSTALLATION, dataset.repositories)
    alice = dash.sign_in(ALICE, {INSTALLATION: ids})
    ada = dash.sign_in(ADA, {INSTALLATION: ids})
    return alice, ada, sorted(ids)


def _timed(label, timings, call):  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    result = call()
    timings[label] = time.perf_counter() - started
    return result


def test_governance_at_1000_repositories(dash, organization, capsys) -> None:  # type: ignore[no-untyped-def]
    alice, ada, ids = organization
    governance = dash.app.service.governance
    timings: dict[str, float] = {}

    overview = _timed(
        "security overview",
        timings,
        lambda: alice.get(f"/api/v1/organizations/{ORG}/security/overview"),
    )
    assert overview.status == 200, overview.raw
    assert overview.data["repositories"] == 1000

    matrix = _timed(
        "repository matrix (sorted, filtered)",
        timings,
        lambda: alice.get(
            f"/api/v1/organizations/{ORG}/security/repositories",
            posture="unknown",
            sort="violations",
            limit=100,
        ),
    )
    assert matrix.status == 200, matrix.raw
    assert len(matrix.data) == 100
    assert matrix.meta["total"] <= 1000
    search = _timed(
        "repository matrix search",
        timings,
        lambda: alice.get(f"/api/v1/organizations/{ORG}/security/repositories", q="service-42"),
    )
    assert search.status == 200

    publish = _timed(
        "publish organization policy (invalidates 1,000)",
        timings,
        lambda: alice.put(
            f"/api/v1/policies/{ORG}", {"expected_version": 0, "floors": {"ai_coauthor": "block"}}
        ),
    )
    assert publish.status == 200, publish.raw

    resolved = _timed(
        "propagate 1,000 effective policies",
        timings,
        lambda: governance.resolver.propagate(limit=1000),
    )
    assert resolved == {"resolved": 1000, "failed": 0}
    started = time.perf_counter()
    for repository_id in ids[:200]:
        governance.resolver.for_repository(ORG, repository_id)
    timings["cached resolution x200"] = time.perf_counter() - started

    group = ada.post(f"/api/v1/organizations/{ORG}/repository-groups", {"name": "All"}).data
    queued = _timed(
        "queue bulk operation (1,000 items)",
        timings,
        lambda: ada.post(
            f"/api/v1/organizations/{ORG}/bulk-operations",
            {
                "type": "add_to_group",
                "repository_ids": ids,
                "parameters": {"group_id": group["id"]},
            },
        ),
    )
    assert queued.status == 202, queued.raw
    processed = _timed(
        "process 1,000 bulk items", timings, lambda: governance.bulk.run_pending(budget=1000)
    )
    assert processed == 1000

    draft = ada.post(
        f"/api/v1/organizations/{ORG}/policy-drafts",
        {"target_type": "organization", "floors": {"ai_coauthor": "warn"}, "reason": "trial"},
    ).data
    simulation = ada.post(
        f"/api/v1/policy-drafts/{draft['id']}/simulations", {"period_days": 90}
    ).data
    _timed("simulation (1,000 repositories)", timings, lambda: governance.simulations.run_pending())
    result = ada.get(f"/api/v1/simulations/{simulation['id']}").data
    assert result["state"] == "completed", result
    assert result["result"]["repositories_analyzed"] > 0

    report = _timed(
        "compliance report (CSV)",
        timings,
        lambda: alice.get(f"/api/v1/organizations/{ORG}/reports/compliance", format="csv"),
    )
    assert report.status == 200
    audit = _timed(
        "audit by type",
        timings,
        lambda: alice.get("/api/v1/audit", type="repository_group_members_added"),
    )
    assert audit.status == 200
    trends = _timed(
        "trends (90 days)",
        timings,
        lambda: alice.get(f"/api/v1/organizations/{ORG}/security/trends", days=90),
    )
    assert trends.status == 200

    budgets = {
        "security overview": 6.0,
        "repository matrix (sorted, filtered)": 6.0,
        "repository matrix search": 6.0,
        "publish organization policy (invalidates 1,000)": 2.0,
        "propagate 1,000 effective policies": 30.0,
        "cached resolution x200": 2.0,
        "queue bulk operation (1,000 items)": 3.0,
        "process 1,000 bulk items": 30.0,
        "simulation (1,000 repositories)": 60.0,
        "compliance report (CSV)": 10.0,
        "audit by type": 2.0,
        "trends (90 days)": 3.0,
    }
    with capsys.disabled():
        print("\ngovernance at 1,000 repositories:")
        for label, seconds in timings.items():
            print(f"  {label:<48} {seconds * 1000:9.1f} ms")
    for label, budget in budgets.items():
        assert timings[label] < budget, (label, timings[label])
