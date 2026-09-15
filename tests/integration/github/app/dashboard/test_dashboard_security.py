"""Dashboard API security: CSRF, CORS, injection, XSS, traversal, secrets, limits, headers."""

import base64
import io
import json
import logging
from pathlib import Path

import pytest

from commitguard.api.hosting import StaticSite, build_dashboard, create_server_app
from commitguard.api.settings import DashboardSettings, Environment
from commitguard.observability.logging import configure_json_logging

ALICE = (501, "alice")
ADA = (504, "ada")
ORG = 1001
XSS = "<script>alert(1)</script>"
AI = "feat: add payment service\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"


@pytest.fixture
def blocked(dash, ops, hub):  # type: ignore[no-untyped-def]
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    # Git refuses angle brackets in author names; commit messages carry anything.
    head = hub.dev.commit(
        f"feat: x\n\nCo-authored-by: Claude {XSS} <noreply@anthropic.com>\n",
        author='Mallory "onmouseover=alert(1) <attacker@example.com>',
    )
    hub.dev.push("feature")
    ops.pull_request(base, head)
    return head


# --------------------------------------------------------------------------- #
# CSRF and CORS
# --------------------------------------------------------------------------- #
def test_writes_require_origin_and_csrf_token(dash) -> None:  # type: ignore[no-untyped-def]
    ada = dash.sign_in(ADA)
    body = {"expected_version": 0, "floors": {"bot_identity": "block"}}
    path = f"/api/v1/policies/{ORG}"
    cases = {
        "no token": ada.request("PUT", path, body=body, send_csrf=False),
        "wrong token": ada.request(
            "PUT", path, body=body, headers={"X-CSRF-Token": "0" * 64}, send_csrf=False
        ),
        "foreign origin": ada.request("PUT", path, body=body, origin="https://evil.example"),
        "no origin": ada.request("PUT", path, body=body, origin=None),
        "null origin": ada.request("PUT", path, body=body, origin="null"),
    }
    for name, result in cases.items():
        assert (result.status, result.error["code"]) == (403, "CSRF_FAILED"), name
    # A cross-site form post cannot send JSON.
    form = ada.request(
        "PUT", path, raw_body=b"expected_version=0", headers={"Content-Type": "text/plain"}
    )
    assert form.status == 415
    assert dash.store.query("SELECT COUNT(*) AS n FROM organization_policy_versions")[0]["n"] == 0
    assert ada.request("PUT", path, body=body).status == 200


def test_csrf_token_is_bound_to_the_session(dash) -> None:  # type: ignore[no-untyped-def]
    ada, alice = dash.sign_in(ADA), dash.sign_in(ALICE)
    result = ada.request(
        "PUT",
        f"/api/v1/policies/{ORG}",
        body={"expected_version": 0, "floors": {"bot_identity": "block"}},
        headers={"X-CSRF-Token": alice.csrf or ""},
        send_csrf=False,
    )
    assert (result.status, result.error["code"]) == (403, "CSRF_FAILED")


def test_cors_uses_an_explicit_allow_list(make_dashboard) -> None:  # type: ignore[no-untyped-def]
    dash = make_dashboard(allowed_origins=("https://admin.commitguard.test",))
    alice = dash.sign_in(ALICE)
    allowed = alice.request(
        "GET", "/api/v1/rules", headers={"Origin": "https://admin.commitguard.test"}
    )
    assert allowed.header("Access-Control-Allow-Origin") == "https://admin.commitguard.test"
    assert allowed.header("Access-Control-Allow-Credentials") == "true"
    other = alice.request("GET", "/api/v1/rules", headers={"Origin": "https://evil.example"})
    assert other.header("Access-Control-Allow-Origin") is None
    for result in (allowed, other):
        assert "*" not in (result.header("Access-Control-Allow-Origin") or "")
        assert "Origin" in (result.header("Vary") or "")
    preflight = dash.anonymous().request(
        "OPTIONS", "/api/v1/policies/1001", headers={"Origin": "https://evil.example"}
    )
    assert preflight.status == 403
    ok = dash.anonymous().request(
        "OPTIONS", "/api/v1/policies/1001", headers={"Origin": "https://admin.commitguard.test"}
    )
    assert ok.status == 204
    assert "X-CSRF-Token" in (ok.header("Access-Control-Allow-Headers") or "")
    # Allowed origins may also write (with the CSRF token).
    write = alice.request(
        "PUT",
        f"/api/v1/policies/{ORG}",
        body={"expected_version": 0, "floors": {"ai_coauthor": "block"}},
        origin="https://admin.commitguard.test",
    )
    assert write.status == 200


def test_wildcard_origins_are_refused_in_configuration() -> None:
    from commitguard.api.settings import load_dashboard_settings
    from commitguard.github.errors import AppConfigurationError

    env = {
        "COMMITGUARD_DASHBOARD_URL": "https://commitguard.example",
        "COMMITGUARD_GITHUB_CLIENT_ID": "Iv23liabc",
        "COMMITGUARD_GITHUB_CLIENT_SECRET": "x" * 40,
        "COMMITGUARD_DASHBOARD_ALLOWED_ORIGINS": "*",
    }
    with pytest.raises(AppConfigurationError):
        load_dashboard_settings(env)
    with pytest.raises(AppConfigurationError):
        load_dashboard_settings(
            {
                **env,
                "COMMITGUARD_DASHBOARD_ALLOWED_ORIGINS": "",
                "COMMITGUARD_DASHBOARD_URL": "http://commitguard.example",
            }
        )


# --------------------------------------------------------------------------- #
# Injection and XSS
# --------------------------------------------------------------------------- #
def test_untrusted_git_metadata_is_returned_as_inert_text(dash, blocked) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    violation = next(
        v for v in alice.get("/api/v1/violations").data if v["rule_id"] == "ai_coauthor"
    )
    assert violation["author"] == 'Mallory "onmouseover=alert(1) <attacker@example.com>'
    detail = alice.get(f"/api/v1/violations/{violation['id']}")
    # Text exactly as recorded: no HTML encoding in storage, no stripping, no execution.
    assert detail.data["evidence"][0]["value"] == f"Claude {XSS} <noreply@anthropic.com>"
    assert detail.header("Content-Type") == "application/json; charset=utf-8"
    assert detail.header("X-Content-Type-Options") == "nosniff"
    assert "default-src 'none'" in (detail.header("Content-Security-Policy") or "")
    searched = alice.get("/api/v1/violations", q="onmouseover").data
    assert violation["id"] in [v["id"] for v in searched]


INJECTIONS = [
    ("/api/v1/scans", {"q": "' OR 1=1 --"}, 200),
    ("/api/v1/scans", {"q": "'; DROP TABLE scan_jobs; --"}, 200),
    ("/api/v1/violations", {"q": "%' UNION SELECT session_hash FROM sessions --"}, 200),
    ("/api/v1/violations", {"q": "$(touch /tmp/commitguard-pwned)"}, 200),
    ("/api/v1/repositories", {"q": "`id`; rm -rf /"}, 200),
    ("/api/v1/scans", {"sort": "sequence; DROP TABLE users"}, 400),
    ("/api/v1/violations", {"sort": "severity_rank DESC, (SELECT 1)"}, 400),
    ("/api/v1/scans", {"organization": "1001 OR 1=1"}, 400),
    ("/api/v1/scans", {"repository": "5001) OR (1=1"}, 400),
    ("/api/v1/scans", {"rule": "ai_coauthor' --"}, 400),
    ("/api/v1/violations", {"severity": "high' OR '1'='1"}, 400),
    ("/api/v1/scans", {"result": "blocked OR 1"}, 400),
    ("/api/v1/audit", {"type": "x' OR 1=1"}, 400),
    ("/api/v1/audit", {"from": "2026-01-01' OR '1'='1"}, 400),
    (
        "/api/v1/scans",
        {"cursor": base64.urlsafe_b64encode(b'["1 OR 1=1"]').decode().rstrip("=")},
        400,
    ),
    ("/api/v1/violations", {"cursor": "../../etc/passwd"}, 400),
    ("/api/v1/scans", {"limit": "1000"}, 400),
    ("/api/v1/scans", {"limit": "-1"}, 400),
]


@pytest.mark.parametrize(("path", "query", "status"), INJECTIONS)
def test_injection_attempts_are_validated_or_inert(dash, blocked, path, query, status) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    before = {
        table: dash.store.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]  # noqa: S608 - test constant
        for table in ("scan_jobs", "violations", "users", "sessions", "findings")
    }
    result = alice.get(path, **query)
    assert result.status == status, result.raw
    if status == 200:
        assert result.data == []
    else:
        assert result.error["code"] == "VALIDATION_ERROR"
    after = {
        table: dash.store.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]  # noqa: S608 - test constant
        for table in before
    }
    assert after == before
    assert not Path("/tmp/commitguard-pwned").exists()  # noqa: S108 - canary


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/rules/$(id)",
        "/api/v1/rules/ai_coauthor;id",
        "/api/v1/scans/../../etc/passwd",
        "/api/v1/repositories/1%20OR%201=1",
        "/api/v1/scans/" + "A" * 32,
        "/api/v1/violations/%00",
        "/api/v1/../v1/rules",
    ],
)
def test_malicious_path_parameters_do_not_match_routes(dash, path) -> None:  # type: ignore[no-untyped-def]
    result = dash.sign_in(ALICE).get(path)
    assert result.status == 404
    assert result.error["code"] == "NOT_FOUND"


def test_request_parsing_limits(dash) -> None:  # type: ignore[no-untyped-def]
    ada = dash.sign_in(ADA)
    path = f"/api/v1/policies/{ORG}"
    assert ada.request("PUT", path, raw_body=b"{not json").status == 400
    duplicate = ada.request(
        "PUT", path, raw_body=b'{"expected_version":0,"expected_version":1,"floors":{}}'
    )
    assert duplicate.status == 400
    deep = b'{"floors":' + b'{"a":' * 40 + b"1" + b"}" * 40 + b"}"
    assert ada.request("PUT", path, raw_body=deep).status == 400
    huge = ada.request("PUT", path, raw_body=b'{"reason":"' + b"x" * 70_000 + b'"}')
    assert (huge.status, huge.error["code"]) == (413, "PAYLOAD_TOO_LARGE")
    repeated = ada.request("GET", "/api/v1/scans", query=None, headers={})
    assert repeated.status == 200
    env_query = ada.request("GET", "/api/v1/scans?", query={"limit": "5"})
    assert env_query.status in (200, 404)
    wrong_method = ada.request("PATCH", path, body={})
    assert (wrong_method.status, wrong_method.header("Allow")) == (405, "GET, PUT")
    unknown = ada.get("/api/v1/nothing-here")
    assert (unknown.status, unknown.error["code"]) == (404, "NOT_FOUND")


def test_repeated_query_parameters_are_rejected(dash) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    captured: dict[str, object] = {}

    def start_response(status, headers):  # type: ignore[no-untyped-def]
        captured["status"] = int(status.split()[0])

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/api/v1/scans",
        "QUERY_STRING": "organization=1001&organization=2002",
        "HTTP_COOKIE": "; ".join(f"{k}={v}" for k, v in alice.cookies.items()),
        "wsgi.input": io.BytesIO(b""),
        "REMOTE_ADDR": "198.51.100.7",
    }
    body = b"".join(dash.wsgi(environ, start_response))
    assert captured["status"] == 400
    assert json.loads(body)["error"]["field"] == "organization"


# --------------------------------------------------------------------------- #
# Secrets, rate limits and headers
# --------------------------------------------------------------------------- #
def test_secrets_never_reach_responses_or_logs(dash, blocked, payloads) -> None:  # type: ignore[no-untyped-def]
    stream = io.StringIO()
    configure_json_logging(stream, logging.DEBUG)
    try:
        alice = dash.sign_in(ALICE)
        [scan] = alice.get("/api/v1/scans").data
        violation = alice.get("/api/v1/violations").data[0]
        bodies = [
            alice.get(path).raw
            for path in (
                "/api/v1/auth/session",
                "/api/v1/auth/sessions",
                "/api/v1/dashboard/overview",
                "/api/v1/repositories",
                "/api/v1/repositories/5001",
                "/api/v1/scans",
                f"/api/v1/scans/{scan['id']}",
                f"/api/v1/violations/{violation['id']}",
                "/api/v1/policies",
                "/api/v1/rules",
                "/api/v1/audit?limit=100",
                "/api/v1/github/installations",
                "/api/v1/github/installations/42",
                "/api/v1/organizations",
            )
        ]
        alice.post("/api/v1/github/installations/42/sync")
        alice.post("/api/v1/repositories/5001/enforcement/refresh")
    finally:
        logging.getLogger("commitguard").handlers.clear()
    logs = stream.getvalue()
    responses = b"".join(bodies).decode()
    forbidden = [
        payloads.WEBHOOK_SECRET.reveal(),
        payloads.CLIENT_SECRET.reveal(),
        *dash.app.private_key_pem.reveal().splitlines()[1:4],
        *dash.github.tokens,
        *dash.github.user_tokens,
        alice.cookies["__Host-commitguard_session"],
    ]
    for secret in forbidden:
        assert secret not in responses
        assert secret not in logs
    assert (alice.csrf or "") not in logs
    assert '"route": "GET /api/v1/scans/{scan_id:hex}"' in logs
    assert "cookie" not in logs.lower().replace("set-cookie", "")


def test_rate_limits(dash) -> None:  # type: ignore[no-untyped-def]
    ada = dash.sign_in(ADA)
    anonymous = dash.anonymous()
    statuses = [anonymous.get("/api/v1/auth/login").status for _ in range(19)]
    # 20 sign-in requests per minute per address; Ada's login and callback used two.
    assert statuses[:18] == [302] * 18
    assert statuses[18] == 429
    writes = [ada.post(f"/api/v1/policies/{ORG}/preview", {"floors": {}}).status for _ in range(3)]
    assert writes == [200, 200, 200]
    github_calls = [ada.post("/api/v1/github/installations/42/sync").status for _ in range(11)]
    assert github_calls[:10] == [200] * 10
    limited = ada.post("/api/v1/github/installations/42/sync")
    assert (limited.status, limited.header("Retry-After")) == (429, "60")


def test_security_headers_by_environment(dash, make_app, clock, payloads) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    result = alice.get("/api/v1/rules")
    headers = {k: v for k, v in result.headers}
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert "Strict-Transport-Security" not in headers  # test environment
    assert len(headers["X-Request-ID"]) == 32

    app = make_app(now=clock)
    production = build_dashboard(
        app.service,
        DashboardSettings(
            origin="https://commitguard.example",
            client_id=payloads.CLIENT_ID,
            client_secret=payloads.CLIENT_SECRET,
            environment=Environment.PRODUCTION,
        ),
        now=clock,
    )
    captured: dict[str, object] = {}

    def start_response(status, response_headers):  # type: ignore[no-untyped-def]
        captured["headers"] = dict(response_headers)

    list(
        create_server_app(app.service, production)(
            {"REQUEST_METHOD": "GET", "PATH_INFO": "/api/v1/rules", "wsgi.input": io.BytesIO()},
            start_response,
        )
    )
    assert "max-age=" in captured["headers"]["Strict-Transport-Security"]  # type: ignore[index]


# --------------------------------------------------------------------------- #
# Dashboard hosting
# --------------------------------------------------------------------------- #
@pytest.fixture
def site(tmp_path, payloads):  # type: ignore[no-untyped-def]
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>CommitGuard</title>")
    (root / "assets" / "app-1234.js").write_text("console.log('ok')")
    (tmp_path / "secret.txt").write_text("do not serve")
    (root / ".env").write_text("SECRET=1")
    settings = DashboardSettings(
        origin="https://commitguard.test",
        client_id=payloads.CLIENT_ID,
        client_secret=payloads.CLIENT_SECRET,
        environment=Environment.TEST,
        static_dir=root,
    )
    return StaticSite(root, settings)


def _serve(site, path, method="GET"):  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}

    def start_response(status, headers):  # type: ignore[no-untyped-def]
        captured["status"] = int(status.split()[0])
        captured["headers"] = dict(headers)

    body = b"".join(site({"REQUEST_METHOD": method, "PATH_INFO": path}, start_response))
    return captured["status"], captured["headers"], body


@pytest.mark.parametrize(
    "path",
    [
        "/../secret.txt",
        "/assets/../../secret.txt",
        "/%2e%2e/secret.txt",
        "/assets/..\\..\\secret.txt",
        "/.env",
        "/assets/missing.js",
        "/index.html\x00.js",
        "//etc/passwd",
    ],
)
def test_static_site_path_traversal(site, path) -> None:  # type: ignore[no-untyped-def]
    status, _, body = _serve(site, path)
    assert status == 404 or b"CommitGuard" in body
    assert b"do not serve" not in body
    assert b"SECRET" not in body
    assert b"root:" not in body


def test_static_site_routes_and_headers(site) -> None:  # type: ignore[no-untyped-def]
    status, headers, body = _serve(site, "/violations/0123")
    assert status == 200
    assert b"CommitGuard" in body
    csp = headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert "frame-ancestors 'none'" in csp
    assert headers["Cache-Control"] == "no-cache"
    status, headers, _ = _serve(site, "/assets/app-1234.js")
    assert status == 200
    assert headers["Content-Type"].startswith("text/javascript")
    assert "immutable" in headers["Cache-Control"]
    assert _serve(site, "/", "POST")[0] == 405
