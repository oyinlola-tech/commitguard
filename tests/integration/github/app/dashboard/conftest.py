"""Dashboard API test harness on top of the GitHub App harness.

* Two tenants: ``octo-org`` (account 1001, installation 42, repository 5001) and
  ``globex`` (account 2002, installation 77, repository 7001). Both repositories
  are backed by real Git repositories, so scans run the real engine.
* Users sign in through the real OAuth flow against :class:`FakeGitHub`
  (state cookie, PKCE, code exchange, ``/user/installations``); nothing is
  injected into the session table.
* :class:`Browser` keeps cookies and sends the CSRF token and Origin header the
  dashboard sends; tests can omit or forge either.
"""

import io
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest

from commitguard.api.app import DashboardApi
from commitguard.api.hosting import build_dashboard, create_server_app
from commitguard.api.settings import DashboardSettings, Environment
from commitguard.audit.models import Actor, ActorType
from commitguard.controlplane.access import Role
from commitguard.controlplane.members import MembershipService
from commitguard.github.identifiers import RepositoryRef

ORIGIN = "https://commitguard.test"
ORG_A = 1001
ORG_B = 2002
INSTALLATION_A = 42
INSTALLATION_B = 77
REPO_A = RepositoryRef(id=5001, owner="octo-org", name="project")
REPO_B = RepositoryRef(id=7001, owner="globex", name="secret")

ALICE = (501, "alice")  # owner of octo-org
VICTOR = (502, "victor")  # viewer of octo-org
SAM = (503, "sam")  # security manager of octo-org
ADA = (504, "ada")  # admin of octo-org
BOB = (601, "bob")  # owner of globex


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: float) -> None:
        self.value += timedelta(**kwargs)


@dataclass
class ApiResult:
    status: int
    headers: list[tuple[str, str]]
    raw: bytes

    @property
    def json(self) -> Any:
        return json.loads(self.raw) if self.raw else None

    @property
    def data(self) -> Any:
        return self.json["data"]

    @property
    def meta(self) -> dict[str, Any]:
        return self.json["meta"]

    @property
    def error(self) -> dict[str, Any]:
        return self.json["error"]

    def header(self, name: str) -> str | None:
        return next((v for k, v in self.headers if k.lower() == name.lower()), None)

    def all(self, name: str) -> list[str]:
        return [v for k, v in self.headers if k.lower() == name.lower()]


@dataclass
class Browser:
    env: "DashboardEnv"
    cookies: dict[str, str] = field(default_factory=dict)
    csrf: str | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
        raw_body: bytes | None = None,
        headers: dict[str, str] | None = None,
        origin: str | None = ORIGIN,
        send_csrf: bool = True,
    ) -> ApiResult:
        payload = raw_body if raw_body is not None else (
            json.dumps(body).encode() if body is not None else b""
        )
        environ: dict[str, Any] = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": urlencode(query or {}),
            "CONTENT_LENGTH": str(len(payload)),
            "CONTENT_TYPE": "application/json" if payload else "",
            "REMOTE_ADDR": "198.51.100.7",
            "wsgi.input": io.BytesIO(payload),
            "HTTP_USER_AGENT": "pytest-browser",
        }
        if self.cookies:
            environ["HTTP_COOKIE"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if origin is not None and method not in ("GET", "HEAD"):
            environ["HTTP_ORIGIN"] = origin
        if send_csrf and self.csrf and method not in ("GET", "HEAD"):
            environ["HTTP_X_CSRF_TOKEN"] = self.csrf
        for name, value in (headers or {}).items():
            key = name.upper().replace("-", "_")
            environ[key if key in ("CONTENT_TYPE", "CONTENT_LENGTH") else f"HTTP_{key}"] = value
        captured: dict[str, Any] = {}

        def start_response(status: str, response_headers: list[tuple[str, str]]) -> None:
            captured["status"] = int(status.split()[0])
            captured["headers"] = response_headers

        raw = b"".join(self.env.wsgi(environ, start_response))
        result = ApiResult(captured["status"], captured["headers"], raw)
        for cookie in result.all("Set-Cookie"):
            name, _, rest = cookie.partition("=")
            value = rest.split(";", 1)[0]
            if "Max-Age=0" in cookie:
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value
        return result

    def get(self, path: str, **query: Any) -> ApiResult:
        return self.request("GET", path, query={k: v for k, v in query.items() if v is not None})

    def put(self, path: str, body: Any = None, **kwargs: Any) -> ApiResult:
        return self.request("PUT", path, body=body if body is not None else {}, **kwargs)

    def post(self, path: str, body: Any = None, **kwargs: Any) -> ApiResult:
        return self.request("POST", path, body=body, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> ApiResult:
        return self.request("DELETE", path, **kwargs)

    def refresh_session(self) -> ApiResult:
        result = self.get("/api/v1/auth/session")
        if result.status == 200:
            self.csrf = result.data["csrf_token"]
        return result


@dataclass
class DashboardEnv:
    app: Any  # AppEnv
    api: DashboardApi
    wsgi: Callable[..., Any]
    clock: Clock

    @property
    def github(self) -> Any:
        return self.app.github

    @property
    def store(self) -> Any:
        return self.app.service.store

    def grant(self, account_id: int, user: tuple[int, str], role: Role) -> None:
        MembershipService(self.store, self.app.service.audit, now=self.clock).grant(
            account_id=account_id,
            user_id=user[0],
            role=role,
            actor=Actor(type=ActorType.SYSTEM, login="test"),
            login=user[1],
        )

    def anonymous(self) -> Browser:
        return Browser(self)

    def sign_in(
        self,
        user: tuple[int, str],
        access: dict[int, set[int]] | None = None,
        *,
        return_to: str = "/dashboard",
    ) -> Browser:
        if user[0] not in self.github.users or access is not None:
            self.github.add_user(user[0], user[1], access or {})
        browser = Browser(self)
        start = browser.get("/api/v1/auth/login", return_to=return_to)
        assert start.status == 302, start.raw
        code, state = self.github.authorize(user[0], start.header("Location") or "")
        callback = browser.get("/api/v1/auth/callback", code=code, state=state)
        assert callback.status == 302, callback.raw
        location = callback.header("Location") or ""
        assert not location.startswith("/login"), location
        assert browser.refresh_session().status == 200
        return browser


def _second_remote(hub: Any) -> Path:
    """A second bare repository for the globex tenant, seeded from the octo-org one."""
    bare = hub.tmp / "globex.git"
    if not bare.exists():
        subprocess.run(
            ["git", "clone", "--quiet", "--bare", str(hub.bare), str(bare)], check=True
        )
        for key in ("uploadpack.allowFilter", "uploadpack.allowAnySHA1InWant"):
            subprocess.run(["git", "-C", str(bare), "config", key, "true"], check=True)
    return bare


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make_dashboard(make_app, clock, payloads) -> Callable[..., DashboardEnv]:  # type: ignore[no-untyped-def]
    def factory(
        *,
        static_dir: Path | None = None,
        allowed_origins: tuple[str, ...] = (),
        **options: Any,
    ) -> DashboardEnv:
        app = make_app(now=clock, **options)
        assert app.install().status == 200  # octo-org: installation 42, repository 5001
        app.github.add_installation(
            INSTALLATION_B, (REPO_B,), login="globex", account_id=ORG_B
        )
        app.github.default_branches[REPO_B.id] = "main"
        app.remotes.paths[REPO_B.id] = _second_remote(app.hub)
        created = app.deliver(
            "installation",
            payloads.installation(
                "created", INSTALLATION_B, (REPO_B,), login="globex", account_id=ORG_B
            ),
        )
        assert created.status == 200
        settings = DashboardSettings(
            origin=ORIGIN,
            client_id=payloads.CLIENT_ID,
            client_secret=payloads.CLIENT_SECRET,
            environment=Environment.TEST,
            static_dir=static_dir,
            allowed_origins=allowed_origins,
        )
        api = build_dashboard(app.service, settings, now=clock)
        env = DashboardEnv(app, api, create_server_app(app.service, api), clock)
        env.grant(ORG_A, ALICE, Role.OWNER)
        env.grant(ORG_A, VICTOR, Role.VIEWER)
        env.grant(ORG_A, SAM, Role.SECURITY_MANAGER)
        env.grant(ORG_A, ADA, Role.ADMIN)
        env.grant(ORG_B, BOB, Role.OWNER)
        for user in (ALICE, VICTOR, SAM, ADA):
            env.github.add_user(user[0], user[1], {INSTALLATION_A: {REPO_A.id}})
        env.github.add_user(BOB[0], BOB[1], {INSTALLATION_B: {REPO_B.id}})
        return env

    return factory


@pytest.fixture
def dash(make_dashboard) -> DashboardEnv:  # type: ignore[no-untyped-def]
    return make_dashboard()


@dataclass
class Ops:
    """Drive GitHub events through the App and run the resulting scans."""

    env: DashboardEnv
    payloads: Any

    def pull_request(
        self,
        base: str,
        head: str,
        *,
        number: int = 7,
        action: str = "synchronize",
        repository: RepositoryRef = REPO_A,
        installation_id: int = INSTALLATION_A,
        run: bool = True,
    ) -> Any:
        app = self.env.app
        app.github.pulls[(repository.id, number)] = {
            "number": number,
            "state": "open",
            "merged": False,
            "head": {"sha": head, "ref": "feature", "repo": {"id": repository.id}},
            "base": {"sha": base, "ref": "main", "repo": {"id": repository.id}},
        }
        result = app.deliver(
            "pull_request",
            self.payloads.pr(
                base,
                head,
                number=number,
                action=action,
                repository=repository,
                installation_id=installation_id,
            ),
        )
        if run:
            app.run()
        return result

    def close_pull_request(
        self, base: str, head: str, *, number: int = 7, merged: bool = False
    ) -> Any:
        self.env.app.github.pulls[(REPO_A.id, number)]["state"] = "closed"
        return self.env.app.deliver(
            "pull_request",
            self.payloads.pr(base, head, number=number, action="closed", merged=merged),
        )

    def push(
        self,
        before: str,
        after: str,
        *,
        ref: str = "refs/heads/main",
        repository: RepositoryRef = REPO_A,
        installation_id: int = INSTALLATION_A,
        run: bool = True,
    ) -> Any:
        result = self.env.app.deliver(
            "push",
            self.payloads.push(
                before, after, ref=ref, repository=repository, installation_id=installation_id
            ),
        )
        if run:
            self.env.app.run()
        return result


@pytest.fixture
def ops(dash, payloads) -> Ops:  # type: ignore[no-untyped-def]
    return Ops(dash, payloads)


@dataclass(frozen=True)
class Dataset:
    repositories: list[RepositoryRef]
    scan_ids: list[str]
    violation_ids: list[str]


def _seed(
    env: DashboardEnv,
    *,
    repositories: int,
    scans: int,
    findings: int,
    audit_events: int,
) -> Dataset:
    """Insert a representative dataset directly (the write paths are tested elsewhere)."""
    import uuid

    from commitguard.audit.models import AuditEvent, AuditEventType
    from commitguard.ci.context import CIContext, CIEventKind, CIProvider

    store = env.store
    now = env.clock().timestamp()
    installation = env.github.installations[INSTALLATION_A]
    repos = [REPO_A] + [
        RepositoryRef(id=9000 + i, owner="octo-org", name=f"service-{i:03d}")
        for i in range(repositories - 1)
    ]
    for repository in repos:
        installation.repositories[repository.id] = repository
    sha = "a" * 40
    context = CIContext(
        provider=CIProvider.GITHUB,
        event=CIEventKind.PUSH,
        event_name="push",
        repository="octo-org/x",
        ref="refs/heads/main",
        before_sha="b" * 40,
        after_sha=sha,
    ).model_dump_json()
    states = ("passed", "failed", "passed", "error", "passed")
    scan_ids = [uuid.uuid4().hex for _ in range(scans)]
    violation_ids = [uuid.uuid4().hex for _ in range(max(1, findings // 5))]
    evidence = json.dumps(
        [
            {
                "source": "coauthor_trailer",
                "source_label": "Co-authored-by trailer",
                "value": "Claude <noreply@anthropic.com>",
                "line_number": 3,
                "matched": [],
                "notes": [],
            }
        ]
    )
    with store.transaction() as db:
        db.executemany(
            "INSERT OR IGNORE INTO installation_repositories VALUES (?, ?, ?, ?, ?)",
            [(INSTALLATION_A, r.id, r.owner, r.name, now) for r in repos],
        )
        db.executemany(
            "INSERT OR IGNORE INTO known_repositories (installation_id, repository_id, owner, "
            "name, default_branch, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, 'main', ?, ?)",
            [(INSTALLATION_A, r.id, r.owner, r.name, now, now) for r in repos],
        )
        db.executemany(
            "INSERT INTO scan_jobs (job_id, job_key, installation_id, repository_id, owner, name, "
            "event, group_key, head_sha, check_name, context, state, attempts, created_at, "
            "updated_at, started_at, completed_at, result_action, commits_scanned, violations, "
            "warnings, findings_count) VALUES (?, ?, ?, ?, ?, ?, 'push', 'push:main', ?, "
            "'commitguard-app/push', ?, ?, 1, ?, ?, ?, ?, ?, 3, ?, 0, ?)",
            [
                (
                    job_id,
                    job_id,
                    INSTALLATION_A,
                    repos[i % len(repos)].id,
                    "octo-org",
                    repos[i % len(repos)].name,
                    sha,
                    context,
                    states[i % len(states)],
                    now - (scans - i) * 60,
                    now - (scans - i) * 60,
                    now - (scans - i) * 60,
                    now - (scans - i) * 60 + 2,
                    "block" if states[i % len(states)] == "failed" else "allow",
                    1 if states[i % len(states)] == "failed" else 0,
                    5 if states[i % len(states)] == "failed" else 0,
                )
                for i, job_id in enumerate(scan_ids)
            ],
        )
        db.executemany(
            "INSERT INTO violations (violation_id, installation_id, repository_id, fingerprint, "
            "rule_id, detector, severity, severity_rank, action, title, commit_sha, author, status, "
            "first_detected_at, last_detected_at, first_job_id, last_job_id, detections, "
            "updated_at) VALUES (?, ?, ?, ?, 'ai_coauthor', 'coauthor', ?, ?, 'block', "
            "'AI coauthor detected', ?, 'Dev <dev@example.com>', ?, ?, ?, ?, ?, 5, ?)",
            [
                (
                    violation_id,
                    INSTALLATION_A,
                    repos[i % len(repos)].id,
                    violation_id,
                    ("high", "low", "critical")[i % 3],
                    (3, 1, 4)[i % 3],
                    f"{i:040x}",
                    "open" if i % 4 else "resolved",
                    now - i * 30,
                    now - i * 30,
                    scan_ids[i % len(scan_ids)],
                    scan_ids[i % len(scan_ids)],
                    now - i * 30,
                )
                for i, violation_id in enumerate(violation_ids)
            ],
        )
        db.executemany(
            "INSERT INTO findings (job_id, installation_id, repository_id, violation_id, "
            "fingerprint, commit_sha, rule_id, detector, severity, severity_rank, confidence, "
            "action, policy_id, reason, title, message, remediation, evidence, author, committer, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, 'ai_coauthor', 'coauthor', 'high', 3, 'high', "
            "'block', 'ai_coauthor', 'policy action is block', 'AI coauthor detected', 'm', 'r', "
            "?, 'Dev <dev@example.com>', 'Dev <dev@example.com>', ?)",
            [
                (
                    scan_ids[i % len(scan_ids)],
                    INSTALLATION_A,
                    repos[i % len(repos)].id,
                    violation_ids[i % len(violation_ids)],
                    violation_ids[i % len(violation_ids)],
                    f"{i % len(violation_ids):040x}",
                    evidence,
                    now - i,
                )
                for i in range(findings)
            ],
        )
        documents = []
        for i in range(audit_events):
            repository = repos[i % len(repos)]
            event = AuditEvent(
                type=AuditEventType.SCAN_PASSED if i % 2 else AuditEventType.SCAN_QUEUED,
                occurred_at=datetime.fromtimestamp(now - i, UTC),
                account_id=ORG_A,
                installation_id=INSTALLATION_A,
                repository_id=repository.id,
                repository=repository.full_name,
                head_sha=sha,
            )
            documents.append(
                (
                    event.event_id,
                    now - i,
                    event.type.value,
                    INSTALLATION_A,
                    repository.id,
                    ORG_A,
                    None,
                    event.model_dump_json(),
                )
            )
        db.executemany(
            "INSERT INTO audit_events (event_id, occurred_at, type, installation_id, "
            "repository_id, account_id, actor_login, document) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            documents,
        )
    return Dataset(repos, scan_ids, violation_ids)


@pytest.fixture
def seed() -> Callable[..., Dataset]:
    return _seed
