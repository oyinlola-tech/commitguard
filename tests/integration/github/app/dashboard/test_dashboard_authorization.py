"""Authentication, authorization, tenant isolation and IDOR protection."""

import pytest

from commitguard.controlplane.access import ROLE_PERMISSIONS, Permission, Role
from commitguard.github.identifiers import RepositoryRef

ALICE = (501, "alice")  # owner, octo-org
VICTOR = (502, "victor")  # viewer, octo-org
SAM = (503, "sam")  # security manager, octo-org
ADA = (504, "ada")  # admin, octo-org
BOB = (601, "bob")  # owner, globex
AI = "feat: add payment service\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
REPO_A, REPO_B = 5001, 7001
ORG_A, ORG_B = 1001, 2002
INSTALLATION_A, INSTALLATION_B = 42, 77


@pytest.fixture
def tenants(dash, ops, hub):  # type: ignore[no-untyped-def]
    """A blocked scan and an open violation in each tenant; returns their IDs."""
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit(AI)
    hub.dev.push("feature")
    hub.dev.git("push", "--quiet", "--no-verify", str(hub.tmp / "globex.git"), "feature")
    ops.pull_request(base, head)
    ops.pull_request(
        base,
        head,
        repository=RepositoryRef(id=REPO_B, owner="globex", name="secret"),
        installation_id=INSTALLATION_B,
    )
    ids = {}
    for user, key in ((ALICE, "a"), (BOB, "b")):
        browser = dash.sign_in(user)
        [scan] = browser.get("/api/v1/scans").data
        [violation] = browser.get("/api/v1/violations").data
        audit = browser.get("/api/v1/audit", limit=1).data[0]
        assert scan["result"] == "blocked"
        ids[key] = {"scan": scan["id"], "violation": violation["id"], "audit": audit["id"]}
    return ids


def _reads(ids: dict[str, str], repository: int, installation: int, org: int) -> list[str]:
    return [
        f"/api/v1/repositories/{repository}",
        f"/api/v1/scans/{ids['scan']}",
        f"/api/v1/scans/{ids['scan']}/comparison",
        f"/api/v1/violations/{ids['violation']}",
        f"/api/v1/audit/{ids['audit']}",
        f"/api/v1/github/installations/{installation}",
        f"/api/v1/github/installations/{installation}/repositories",
        f"/api/v1/policies/{org}",
        f"/api/v1/policies/{org}/versions",
        f"/api/v1/organizations/{org}/members",
    ]


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
def test_unauthenticated_requests_are_rejected(dash, tenants) -> None:  # type: ignore[no-untyped-def]
    anonymous = dash.anonymous()
    for route in dash.api.routes:
        if route.public:
            continue
        path = (
            route.template.replace("{repository_id:int}", str(REPO_A))
            .replace("{organization_id:int}", str(ORG_A))
            .replace("{installation_id:int}", str(INSTALLATION_A))
            .replace("{user_id:int}", "502")
            .replace("{scan_id:hex}", tenants["a"]["scan"])
            .replace("{violation_id:hex}", tenants["a"]["violation"])
            .replace("{event_id:hex}", tenants["a"]["audit"])
            .replace("{rule_id:ident}", "ai_coauthor")
            .replace("{session_id:session}", "0123456789abcdef")
            .replace("{version:version}", "1")
        )
        result = anonymous.request(route.method, path, body={} if route.method != "GET" else None)
        assert result.status == 401, (route.template, result.raw)
        assert result.error["code"] == "UNAUTHENTICATED"
        assert "data" not in result.json


def test_session_lifecycle_expiry_logout_and_revocation(dash, clock) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    second = dash.sign_in(ALICE)
    sessions = alice.get("/api/v1/auth/sessions").data
    assert len(sessions) == 2
    assert sum(s["current"] for s in sessions) == 1

    # Revoking the other session signs that browser out.
    other = next(s for s in sessions if not s["current"])
    assert alice.delete(f"/api/v1/auth/sessions/{other['id']}").status == 200
    assert second.get("/api/v1/repositories").status == 401

    # Another user's session ID is "not found", not revocable.
    bob = dash.sign_in(BOB)
    [bob_session] = bob.get("/api/v1/auth/sessions").data
    assert alice.delete(f"/api/v1/auth/sessions/{bob_session['id']}").status == 404
    assert bob.get("/api/v1/repositories").status == 200

    # Idle timeout.
    clock.advance(hours=2, minutes=1)
    expired = bob.get("/api/v1/repositories")
    assert (expired.status, expired.error["code"]) == (401, "SESSION_EXPIRED")
    assert any("Max-Age=0" in c for c in expired.all("Set-Cookie"))

    # Sign-out invalidates the session on the server, not only the cookie.
    alice = dash.sign_in(ALICE)
    token = dict(alice.cookies)
    assert alice.post("/api/v1/auth/logout").status == 200
    replay = dash.anonymous()
    replay.cookies.update(token)
    assert replay.get("/api/v1/repositories").status == 401


def test_absolute_session_lifetime(dash, clock) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    for _ in range(7):  # active every hour, never idle
        clock.advance(hours=1)
        assert alice.get("/api/v1/auth/session").status == 200
    clock.advance(hours=1)
    result = alice.get("/api/v1/auth/session")
    assert (result.status, result.error["code"]) == (401, "SESSION_EXPIRED")
    assert alice.get("/api/v1/auth/session").error["code"] == "UNAUTHENTICATED"


def test_sign_in_flow_is_bound_to_the_browser(dash) -> None:  # type: ignore[no-untyped-def]
    dash.github.add_user(*ALICE, {INSTALLATION_A: {REPO_A}})
    victim = dash.anonymous()
    start = victim.get("/api/v1/auth/login", return_to="/scans")
    cookie = start.header("Set-Cookie") or ""
    assert cookie.startswith("__Host-commitguard_oauth_state=")
    assert all(flag in cookie for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"))
    code, state = dash.github.authorize(ALICE[0], start.header("Location") or "")

    # An attacker who got the code and state cannot complete the flow in another browser.
    attacker = dash.anonymous()
    stolen = attacker.get("/api/v1/auth/callback", code=code, state=state)
    assert stolen.header("Location") == "/login?error=sign_in_failed"
    assert "__Host-commitguard_session" not in attacker.cookies

    # The legitimate browser's state was consumed by nothing: it still cannot be reused
    # after a successful sign-in.
    victim_code, victim_state = dash.github.authorize(
        ALICE[0], victim.get("/api/v1/auth/login").header("Location") or ""
    )
    done = victim.get("/api/v1/auth/callback", code=victim_code, state=victim_state)
    assert done.status == 302 and done.header("Location") == "/dashboard"
    session_cookie = next(c for c in done.all("Set-Cookie") if "commitguard_session=" in c)
    assert all(flag in session_cookie for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"))
    replayed = dash.anonymous()
    replayed.cookies["__Host-commitguard_oauth_state"] = victim_state
    again = replayed.get("/api/v1/auth/callback", code=victim_code, state=victim_state)
    assert again.header("Location") == "/login?error=sign_in_failed"


@pytest.mark.parametrize(
    "return_to",
    ["//evil.example/x", "https://evil.example", "/\\evil.example", "/api/v1/auth/logout", "javascript:alert(1)"],
)
def test_sign_in_never_redirects_off_site(dash, return_to) -> None:  # type: ignore[no-untyped-def]
    dash.github.add_user(*ALICE, {INSTALLATION_A: {REPO_A}})
    browser = dash.anonymous()
    start = browser.get("/api/v1/auth/login", return_to=return_to)
    code, state = dash.github.authorize(ALICE[0], start.header("Location") or "")
    done = browser.get("/api/v1/auth/callback", code=code, state=state)
    assert done.header("Location") == "/dashboard"


def test_github_user_token_is_never_stored_or_returned(dash) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    tokens = list(dash.github.user_tokens)
    assert tokens
    session = alice.get("/api/v1/auth/session")
    database = (dash.app.data_dir / "commitguard-app.sqlite3").read_bytes()
    for token in tokens:
        assert token.encode() not in database
        assert token.encode() not in session.raw
    session_token = alice.cookies["__Host-commitguard_session"]
    assert session_token.encode() not in database  # only its hash is stored
    assert session_token not in session.raw.decode()


# --------------------------------------------------------------------------- #
# Roles and permissions
# --------------------------------------------------------------------------- #
def test_roles_have_practical_differences() -> None:
    ranks = list(Role)
    for lower, higher in zip(ranks, ranks[1:], strict=False):
        assert ROLE_PERMISSIONS[lower] < ROLE_PERMISSIONS[higher]
    assert Permission.AUDIT_READ not in ROLE_PERMISSIONS[Role.VIEWER]
    assert Permission.POLICIES_WRITE not in ROLE_PERMISSIONS[Role.SECURITY_MANAGER]
    assert Permission.MEMBERS_MANAGE not in ROLE_PERMISSIONS[Role.ADMIN]


def test_every_permission_is_checked_somewhere() -> None:
    from pathlib import Path

    import commitguard

    source = "\n".join(
        p.read_text()
        for p in Path(commitguard.__file__).parent.joinpath("api").glob("*.py")
    ) + "\n".join(
        p.read_text()
        for p in Path(commitguard.__file__).parent.joinpath("controlplane").glob("*.py")
        if p.name != "access.py"
    )
    for permission in Permission:
        assert f"Permission.{permission.name}" in source or f"p.{permission.name}" in source, (
            permission
        )


def test_viewer_reads_but_cannot_change_anything(dash, tenants) -> None:  # type: ignore[no-untyped-def]
    victor = dash.sign_in(VICTOR)
    for path in (
        "/api/v1/repositories",
        f"/api/v1/repositories/{REPO_A}",
        "/api/v1/scans",
        f"/api/v1/scans/{tenants['a']['scan']}",
        "/api/v1/violations",
        f"/api/v1/violations/{tenants['a']['violation']}",
        f"/api/v1/policies/{ORG_A}",
        "/api/v1/rules",
        "/api/v1/dashboard/overview",
    ):
        assert victor.get(path).status == 200, path
    assert victor.get("/api/v1/audit").status == 403
    assert victor.get(f"/api/v1/organizations/{ORG_A}/members").status == 403
    policy = victor.get(f"/api/v1/policies/{ORG_A}").data
    assert policy["can_write"] is False
    denied = [
        victor.put(f"/api/v1/policies/{ORG_A}", {"expected_version": 0, "floors": {"bot_identity": "block"}}),
        victor.put(f"/api/v1/violations/{tenants['a']['violation']}/acknowledgement", {}),
        victor.post(f"/api/v1/scans/{tenants['a']['scan']}/rescan"),
        victor.put(f"/api/v1/repositories/{REPO_A}/monitoring", {"enabled": False, "confirm": True, "reason": "x"}),
        victor.post(f"/api/v1/repositories/{REPO_A}/enforcement/refresh"),
        victor.post(f"/api/v1/github/installations/{INSTALLATION_A}/sync"),
        victor.put(f"/api/v1/organizations/{ORG_A}/members/999", {"role": "owner"}),
    ]
    assert [r.status for r in denied] == [403] * len(denied)
    assert {r.error["code"] for r in denied} == {"FORBIDDEN"}
    assert dash.store.query("SELECT COUNT(*) AS n FROM organization_policy_versions")[0]["n"] == 0
    assert victor.get("/api/v1/violations").data[0]["status"] == "open"


def test_admin_changes_policy_and_it_is_audited(dash) -> None:  # type: ignore[no-untyped-def]
    ada = dash.sign_in(ADA)
    result = ada.put(
        f"/api/v1/policies/{ORG_A}",
        {"expected_version": 0, "floors": {"bot_identity": "block"}, "reason": "no bots"},
    )
    assert result.status == 200, result.raw
    assert result.data["version"] == 1
    assert result.meta["changes"] == [
        {"policy_id": "bot_identity", "old": None, "new": "block", "weakening": False}
    ]
    alice = dash.sign_in(ALICE)
    [event] = alice.get("/api/v1/audit", type="organization_policy_changed").data
    assert event["actor"] == {"type": "user", "id": ADA[0], "login": "ada"}
    assert event["organization_id"] == ORG_A
    assert event["data"]["changes"] == "bot_identity: repository -> block"
    assert event["data"]["reason"] == "no bots"


def test_security_manager_triage_but_not_policy(dash, tenants) -> None:  # type: ignore[no-untyped-def]
    sam = dash.sign_in(SAM)
    assert sam.put(f"/api/v1/violations/{tenants['a']['violation']}/acknowledgement", {}).status == 200
    assert sam.get("/api/v1/audit").status == 200
    assert sam.put(
        f"/api/v1/policies/{ORG_A}", {"expected_version": 0, "floors": {"bot_identity": "block"}}
    ).status == 403


def test_role_changes_apply_to_the_next_request(dash) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    victor = dash.sign_in(VICTOR)
    assert victor.get("/api/v1/repositories").status == 200
    assert alice.delete(f"/api/v1/organizations/{ORG_A}/members/{VICTOR[0]}").status == 200
    assert victor.get("/api/v1/repositories").status == 403
    assert victor.get("/api/v1/auth/session").data["organizations"] == []
    types = [e["type"] for e in alice.get("/api/v1/audit", limit=50).data]
    assert "member_removed" in types


def test_owner_rules_for_members(dash) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    assert alice.put(f"/api/v1/organizations/{ORG_A}/members/{ALICE[0]}", {"role": "viewer"}).status == 403
    promoted = alice.put(f"/api/v1/organizations/{ORG_A}/members/{ADA[0]}", {"role": "owner"})
    assert promoted.status == 200, promoted.raw
    ada = dash.sign_in(ADA)
    assert ada.put(f"/api/v1/organizations/{ORG_A}/members/{ALICE[0]}", {"role": "viewer"}).status == 200
    # The last owner cannot be removed.
    last = alice.delete(f"/api/v1/organizations/{ORG_A}/members/{ADA[0]}")
    assert last.status in (403, 404)  # alice is no longer an owner
    assert ada.delete(f"/api/v1/organizations/{ORG_A}/members/{ADA[0]}").status == 403
    invalid = ada.put(f"/api/v1/organizations/{ORG_A}/members/777", {"role": "superuser"})
    assert invalid.status == 400


# --------------------------------------------------------------------------- #
# Tenant isolation and IDOR
# --------------------------------------------------------------------------- #
def test_tenants_cannot_read_each_other(dash, tenants) -> None:  # type: ignore[no-untyped-def]
    alice, bob = dash.sign_in(ALICE), dash.sign_in(BOB)
    for browser, own, foreign in (
        (alice, (tenants["a"], REPO_A, INSTALLATION_A, ORG_A), (tenants["b"], REPO_B, INSTALLATION_B, ORG_B)),
        (bob, (tenants["b"], REPO_B, INSTALLATION_B, ORG_B), (tenants["a"], REPO_A, INSTALLATION_A, ORG_A)),
    ):
        for path in _reads(*own):
            assert browser.get(path).status == 200, path
        for path in _reads(*foreign):
            result = browser.get(path)
            assert result.status == 404, (path, result.raw)
            assert result.error["code"] == "NOT_FOUND"
            assert set(result.error) == {"code", "message", "request_id"}
            for secret in ("globex", "octo-org", foreign[0]["violation"]):
                assert secret not in result.raw.decode()

    def names(browser, path):  # type: ignore[no-untyped-def]
        return {r["repository"]["full_name"] for r in browser.get(path).data}

    assert names(alice, "/api/v1/scans") == {"octo-org/project"}
    assert names(bob, "/api/v1/scans") == {"globex/secret"}
    assert names(alice, "/api/v1/violations") == {"octo-org/project"}
    assert {r["full_name"] for r in alice.get("/api/v1/repositories").data} == {"octo-org/project"}
    assert {i["id"] for i in alice.get("/api/v1/github/installations").data} == {INSTALLATION_A}
    assert {p["organization"]["id"] for p in bob.get("/api/v1/policies").data} == {ORG_B}
    assert all(e["organization_id"] == ORG_A for e in alice.get("/api/v1/audit", limit=100).data)
    assert all(e["organization_id"] == ORG_B for e in bob.get("/api/v1/audit", limit=100).data)
    # Filtering by the other tenant's IDs reveals nothing.
    assert alice.get("/api/v1/scans", organization=ORG_B).status == 404
    assert alice.get("/api/v1/scans", repository=REPO_B).data == []
    assert alice.get("/api/v1/dashboard/overview", organization=ORG_B).status == 404
    overview = bob.get("/api/v1/dashboard/overview").data
    assert overview["summary"]["open_violations"] == 1
    assert [s["repository"]["full_name"] for s in overview["recent_scans"]] == ["globex/secret"]


def test_idor_on_every_write_is_rejected(dash, tenants) -> None:  # type: ignore[no-untyped-def]
    bob = dash.sign_in(BOB)
    a = tenants["a"]
    github_calls = len(dash.github.requests)
    attempts = {
        "acknowledge": bob.put(f"/api/v1/violations/{a['violation']}/acknowledgement", {}),
        "unacknowledge": bob.delete(f"/api/v1/violations/{a['violation']}/acknowledgement"),
        "rescan": bob.post(f"/api/v1/scans/{a['scan']}/rescan"),
        "monitoring": bob.put(
            f"/api/v1/repositories/{REPO_A}/monitoring", {"enabled": False, "confirm": True, "reason": "x"}
        ),
        "refresh": bob.post(f"/api/v1/repositories/{REPO_A}/enforcement/refresh"),
        "sync": bob.post(f"/api/v1/github/installations/{INSTALLATION_A}/sync"),
        "policy": bob.put(
            f"/api/v1/policies/{ORG_A}", {"expected_version": 0, "floors": {"ai_coauthor": "block"}}
        ),
        "preview": bob.post(f"/api/v1/policies/{ORG_A}/preview", {"floors": {}}),
        "member": bob.put(f"/api/v1/organizations/{ORG_A}/members/{BOB[0]}", {"role": "owner"}),
    }
    for name, result in attempts.items():
        assert result.status == 404, (name, result.raw)
    alice = dash.sign_in(ALICE)
    assert alice.get(f"/api/v1/violations/{a['violation']}").data["violation"]["status"] == "open"
    assert len(dash.app.jobs()) == 1  # no re-scan was queued
    memberships = dash.store.query(
        "SELECT account_id FROM memberships WHERE user_id = ?", (BOB[0],)
    )
    assert [m["account_id"] for m in memberships] == [ORG_B]
    # Sign-in calls aside, nothing reached GitHub on Bob's behalf.
    sign_in_paths = {"/login/oauth/access_token", "/user", "/user/installations"}
    extra = [
        path
        for _, path, _ in dash.github.requests[github_calls:]
        if path not in sign_in_paths and not path.startswith("/user/installations/")
    ]
    assert extra == []


def test_repositories_hidden_when_github_denies_the_user(dash, tenants) -> None:  # type: ignore[no-untyped-def]
    # Ada is an admin in CommitGuard, but GitHub no longer lets her see the repository.
    ada = dash.sign_in(ADA, {INSTALLATION_A: set()})
    assert ada.get("/api/v1/repositories").data == []
    assert ada.get("/api/v1/scans").data == []
    assert ada.get(f"/api/v1/scans/{tenants['a']['scan']}").status == 404
    assert ada.get(f"/api/v1/violations/{tenants['a']['violation']}").status == 404
    assert ada.post(f"/api/v1/repositories/{REPO_A}/enforcement/refresh").status == 404
    # Organization-level data is still available to her role.
    assert ada.get(f"/api/v1/policies/{ORG_A}").status == 200


def test_membership_requires_github_installation_access(dash, tenants) -> None:  # type: ignore[no-untyped-def]
    # Victor has a CommitGuard role, but GitHub does not list the installation for him.
    victor = dash.sign_in(VICTOR, {})
    assert victor.get("/api/v1/auth/session").data["organizations"] == []
    assert victor.get(f"/api/v1/policies/{ORG_A}").status == 404
    assert victor.get("/api/v1/repositories").status == 403
