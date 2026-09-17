"""Every governance route: cross-tenant isolation (404) and least privilege (403).

Resources are created in octo-org; then every governance route is called with
their real IDs by the owner of another organization (globex), who must receive
404 with no data, and every non-GET route by an octo-org viewer, who must be
refused (403) - never a 2xx, and never data from another tenant.
"""

import re
from datetime import timedelta

import pytest

ORG_A = 1001
ORG_B = 2002
REPO_A = 5001
ALICE = (501, "alice")
VICTOR = (502, "victor")
SAM = (503, "sam")
ADA = (504, "ada")
BOB = (601, "bob")


@pytest.fixture(autouse=True)
def _no_rate_limits(monkeypatch):  # type: ignore[no-untyped-def]
    """These sweeps test authorization; rate limits are tested separately."""
    from commitguard.security.rate_limit import RequestRateLimiter

    monkeypatch.setattr(RequestRateLimiter, "allow", lambda self, key: True)


@pytest.fixture
def resources(dash, clock):  # type: ignore[no-untyped-def]
    alice, ada, sam = dash.sign_in(ALICE), dash.sign_in(ADA), dash.sign_in(SAM)
    group = ada.post(f"/api/v1/organizations/{ORG_A}/repository-groups", {"name": "Production"})
    assert group.status == 201, group.raw
    ada.post(
        f"/api/v1/repository-groups/{group.data['id']}/repositories", {"repository_ids": [REPO_A]}
    )
    ada.put(f"/api/v1/policies/{ORG_A}", {"expected_version": 0, "floors": {"ai_coauthor": "warn"}})
    draft = ada.post(
        f"/api/v1/organizations/{ORG_A}/policy-drafts",
        {"target_type": "organization", "floors": {"ai_coauthor": "block"}},
    )
    assert draft.status == 201, draft.raw
    simulation = ada.post(f"/api/v1/policy-drafts/{draft.data['id']}/simulations", {})
    assert simulation.status == 202, simulation.raw
    rollout_draft = ada.post(
        f"/api/v1/organizations/{ORG_A}/policy-drafts",
        {"target_type": "group", "target_id": group.data["id"], "floors": {"bot_identity": "warn"}},
    ).data
    ada.post(f"/api/v1/policy-drafts/{rollout_draft['id']}/publish", {})
    second = ada.post(
        f"/api/v1/organizations/{ORG_A}/policy-drafts",
        {
            "target_type": "group",
            "target_id": group.data["id"],
            "floors": {"bot_identity": "block"},
        },
    ).data
    published = ada.post(
        f"/api/v1/policy-drafts/{second['id']}/publish",
        {"rollout": {"stages": [{"repositories": [REPO_A]}, {"percent": 100}]}},
    )
    assert published.status == 200, published.raw
    [rollout] = ada.get(f"/api/v1/organizations/{ORG_A}/rollouts").data
    exception = sam.post(
        f"/api/v1/organizations/{ORG_A}/exceptions",
        {
            "rule_id": "ai_coauthor",
            "scope_type": "repository",
            "scope_id": REPO_A,
            "action": "warn",
            "reason": "legacy",
            "expires_at": (clock() + timedelta(days=5)).isoformat(),
        },
    )
    assert exception.status == 201, exception.raw
    bulk = ada.post(
        f"/api/v1/organizations/{ORG_A}/bulk-operations",
        {
            "type": "add_to_group",
            "repository_ids": [REPO_A],
            "parameters": {"group_id": group.data["id"]},
        },
    )
    assert bulk.status == 202, bulk.raw
    schedule = ada.post(
        f"/api/v1/organizations/{ORG_A}/scan-schedules",
        {
            "name": "Nightly",
            "target_type": "organization",
            "cadence": "daily",
            "hour": 2,
            "minute": 0,
        },
    )
    assert schedule.status == 201, schedule.raw
    dash.app.deliver(  # a critical security event to acknowledge
        "installation",
        {
            "action": "suspend",
            "installation": {
                "id": 42,
                "account": {"id": ORG_A, "login": "octo-org", "type": "Organization"},
                "repository_selection": "selected",
                "permissions": {"checks": "write", "contents": "read", "metadata": "read"},
            },
            "repositories": [],
        },
    )
    [event] = [
        r["event_id"]
        for r in dash.store.query(
            "SELECT event_id FROM notification_events WHERE type = 'installation_disconnected'"
        )
    ]
    del alice
    return {
        "{organization_id:int}": str(ORG_A),
        "{repository_id:int}": str(REPO_A),
        "{group_id:hex}": group.data["id"],
        "{draft_id:hex}": draft.data["id"],
        "{simulation_id:hex}": simulation.data["id"],
        "{rollout_id:hex}": rollout["id"],
        "{exception_id:hex}": exception.data["id"],
        "{operation_id:hex}": bulk.data["id"],
        "{schedule_id:hex}": schedule.data["id"],
        "{event_id:hex}": event,
        "{kind:ident}": "compliance",
        "{target_type:ident}": "group",
    }


def _governance_routes(dash):  # type: ignore[no-untyped-def]
    governance = {(row[0], row[1]) for row in _table(dash)}
    return [
        route
        for route in dash.api.routes
        if (route.method, route.template.removeprefix("/api/v1")) in governance
    ]


def _table(dash):  # type: ignore[no-untyped-def]
    from commitguard.api.governance import GovernanceRoutes

    return GovernanceRoutes(dash.app.service.governance).routes()


def _path(template: str, values: dict[str, str]) -> str:
    for placeholder, value in values.items():
        template = template.replace(placeholder, value)
    assert not re.search(r"\{[a-z_]+:[a-z]+\}", template), template
    return template


def _query(template: str, values: dict[str, str]) -> dict[str, str]:
    query = {"q": "project", "format": "json"}
    if "{target_type:ident}" in template:
        query["target_id"] = values["{group_id:hex}"]
    return query


def test_other_tenants_get_not_found_for_every_governance_route(dash, resources) -> None:  # type: ignore[no-untyped-def]
    bob = dash.sign_in(BOB)
    routes = _governance_routes(dash)
    assert len(routes) == len(_table(dash))
    for route in routes:
        path = _path(route.template, resources)
        result = bob.request(
            route.method,
            path,
            query=_query(route.template, resources) if route.method == "GET" else None,
            body={} if route.method != "GET" else None,
        )
        assert result.status == 404, (route.method, route.template, result.status, result.raw)
        assert "data" not in (result.json or {}), route.template


def test_viewers_cannot_change_anything(dash, resources) -> None:  # type: ignore[no-untyped-def]
    victor = dash.sign_in(VICTOR)
    allowed_reads = 0
    for route in _governance_routes(dash):
        path = _path(route.template, resources)
        if route.method == "GET":
            result = victor.get(path, **_query(route.template, resources))
            if route.template.endswith("/reports/{kind:ident}"):
                assert result.status == 200
                continue
            assert result.status in (200, 403, 404), (route.template, result.raw)
            allowed_reads += result.status == 200
            continue
        result = victor.request(route.method, path, body={})
        assert result.status == 403, (route.method, route.template, result.status, result.raw)
    assert allowed_reads >= 20  # viewers can see governance, just not change it


def test_security_manager_can_request_but_not_approve_or_publish(dash, resources) -> None:  # type: ignore[no-untyped-def]
    sam = dash.sign_in(SAM)
    draft = resources["{draft_id:hex}"]
    exception = resources["{exception_id:hex}"]
    assert sam.post(f"/api/v1/policy-drafts/{draft}/publish", {}).status == 403
    assert sam.post(f"/api/v1/exceptions/{exception}/approve", {}).status == 403
    assert (
        sam.put(
            f"/api/v1/organizations/{ORG_A}/rules",
            {"expected_version": 0, "rules": {}, "reason": "x"},
        ).status
        == 403
    )
    assert sam.post(f"/api/v1/rollouts/{resources['{rollout_id:hex}']}/rollback", {}).status == 403
    assert sam.get(f"/api/v1/organizations/{ORG_A}/security/overview").status == 200
