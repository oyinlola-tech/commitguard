"""API contract: envelopes, pagination, server-side filtering, sorting, routes and docs."""

import re
from pathlib import Path

import pytest

ALICE = (501, "alice")
PROJECT_ROOT = Path(__file__).resolve().parents[5]


@pytest.fixture
def seeded(dash, seed):  # type: ignore[no-untyped-def]
    dataset = seed(dash, repositories=12, scans=130, findings=300, audit_events=260)
    access = {42: {r.id for r in dataset.repositories}}
    browser = dash.sign_in(ALICE, access)
    return browser, dataset


def _walk(browser, path, **query):  # type: ignore[no-untyped-def]
    items, pages, cursor = [], 0, None
    while True:
        result = browser.get(path, cursor=cursor, **query)
        assert result.status == 200, result.raw
        assert set(result.json) == {"data", "meta"}
        assert len(result.data) <= result.meta["limit"]
        items.extend(result.data)
        pages += 1
        cursor = result.meta["next_cursor"]
        if cursor is None:
            return items, pages


def test_envelopes(dash) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    success = alice.get("/api/v1/rules")
    assert set(success.json) == {"data", "meta"}
    failure = alice.get("/api/v1/scans/" + "0" * 32)
    assert failure.json == {
        "error": {
            "code": "NOT_FOUND",
            "message": "The requested resource was not found.",
            "request_id": failure.header("X-Request-ID"),
        }
    }


def test_default_page_sizes_are_bounded(seeded) -> None:  # type: ignore[no-untyped-def]
    browser, _ = seeded
    for path in ("/api/v1/scans", "/api/v1/violations", "/api/v1/audit"):
        result = browser.get(path)
        assert result.meta["limit"] == 25
        assert len(result.data) == 25
        assert result.meta["next_cursor"]
    repositories = browser.get("/api/v1/repositories")
    assert (len(repositories.data), repositories.meta["next_cursor"]) == (12, None)


@pytest.mark.parametrize(
    ("path", "query", "expected"),
    [
        ("/api/v1/scans", {"limit": 40}, 130),
        ("/api/v1/scans", {"limit": 40, "sort": "oldest"}, 130),
        ("/api/v1/violations", {"limit": 17, "sort": "severity"}, 60),
        ("/api/v1/violations", {"limit": 17, "sort": "repository"}, 60),
        ("/api/v1/violations", {"limit": 17, "sort": "oldest"}, 60),
        ("/api/v1/repositories", {"limit": 5}, 12),
    ],
)
def test_cursor_pagination_is_complete_and_stable(seeded, path, query, expected) -> None:  # type: ignore[no-untyped-def]
    browser, _ = seeded
    items, pages = _walk(browser, path, **query)
    ids = [item["id"] for item in items]
    assert len(ids) == expected
    assert len(set(ids)) == expected  # no duplicates across pages
    assert pages == -(-expected // query["limit"])


def test_audit_pagination_over_many_events(seeded) -> None:  # type: ignore[no-untyped-def]
    browser, _ = seeded
    items, _ = _walk(browser, "/api/v1/audit", limit=100)
    assert len({e["id"] for e in items}) == len(items) >= 260
    times = [e["occurred_at"] for e in items]
    assert times == sorted(times, reverse=True)


def test_sorting(seeded) -> None:  # type: ignore[no-untyped-def]
    browser, _ = seeded
    newest = browser.get("/api/v1/scans", limit=100).data
    created = [s["created_at"] for s in newest]
    assert created == sorted(created, reverse=True)
    oldest = browser.get("/api/v1/scans", limit=100, sort="oldest").data
    assert [s["created_at"] for s in oldest] == sorted(s["created_at"] for s in oldest)
    ranks = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    severity = [ranks[v["severity"]] for v in browser.get("/api/v1/violations", limit=100, sort="severity").data]
    assert severity == sorted(severity, reverse=True)
    names = [v["repository"]["full_name"] for v in browser.get("/api/v1/violations", limit=100, sort="repository").data]
    assert names == sorted(names)
    repositories = [r["full_name"] for r in browser.get("/api/v1/repositories", limit=100).data]
    assert repositories == sorted(repositories, key=str.lower)


def test_filters_are_applied_on_the_server(seeded) -> None:  # type: ignore[no-untyped-def]
    browser, dataset = seeded
    blocked = browser.get("/api/v1/scans", result="blocked", limit=100).data
    assert blocked and {s["result"] for s in blocked} == {"blocked"}
    errors = browser.get("/api/v1/scans", result="error", limit=100).data
    assert errors and {s["result"] for s in errors} == {"error"}
    repository = dataset.repositories[3]
    in_repository = browser.get("/api/v1/scans", repository=repository.id, limit=100).data
    assert in_repository and {s["repository"]["id"] for s in in_repository} == {repository.id}
    critical = browser.get("/api/v1/violations", severity="critical", status="open", limit=100).data
    assert critical and {(v["severity"], v["status"]) for v in critical} == {("critical", "open")}
    resolved = browser.get("/api/v1/violations", status="resolved", limit=100).data
    assert resolved and {v["status"] for v in resolved} == {"resolved"}
    by_name = browser.get("/api/v1/repositories", q="service-00", limit=100).data
    assert {r["name"] for r in by_name} == {f"service-00{i}" for i in range(10)}
    by_sha = browser.get("/api/v1/violations", q="0" * 39 + "5").data
    assert [v["commit_sha"] for v in by_sha] == ["0" * 39 + "5"]
    window = browser.get(
        "/api/v1/scans", **{"from": "2026-09-01T10:00:00+00:00", "to": "2026-09-01T11:00:00Z"}, limit=100
    ).data
    assert len(window) == 60
    events = browser.get("/api/v1/audit", type="scan_queued", limit=100).data
    assert {e["type"] for e in events} == {"scan_queued"}


def test_documented_routes_match_implemented_routes(dash) -> None:  # type: ignore[no-untyped-def]
    text = (PROJECT_ROOT / "docs" / "dashboard.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `(GET|POST|PUT|DELETE) (/api/v1/[^`]+)` \|", text, re.M))
    implemented = {
        (route.method, re.sub(r"\{([a-z_]+):[a-z]+\}", r"{\1}", route.template))
        for route in dash.api.routes
    }
    assert documented == implemented
