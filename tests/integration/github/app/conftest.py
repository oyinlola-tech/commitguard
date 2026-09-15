"""An offline model of GitHub for the CommitGuard GitHub App.

* ``FakeGitHub`` implements the REST endpoints the App uses, with GitHub's
  security behaviour: JWTs are verified with the App's public key, installation
  tokens are down-scoped to repositories and permissions, repository and check
  run access is checked against the token, and failures can be injected.
* Commits live in the real bare repository from the ``hub`` fixture; the App
  fetches them over ``file://`` exactly as it fetches from github.com over HTTPS.
* Webhooks are signed with the configured secret and passed through the same
  entry point the HTTP endpoint uses.
"""

import base64
import hashlib
import json
import re
import secrets
import subprocess
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from commitguard.github.app import GitHubAppService, WebhookResult
from commitguard.github.checks import APP_CHECK_NAME
from commitguard.github.client import HttpRequest, HttpResponse
from commitguard.github.identifiers import RepositoryRef
from commitguard.github.permissions import REQUIRED_PERMISSIONS
from commitguard.github.storage import ScanJob
from commitguard.github.webhooks import compute_signature
from commitguard.security.secrets import Secret

APP_ID = 4242
INSTALLATION_ID = 42
REPO = RepositoryRef(id=5001, owner="octo-org", name="project")
WEBHOOK_SECRET = Secret("integration-webhook-secret-0f9e8d7c6b5a")
_LEVEL = {"none": 0, "read": 1, "write": 2, "admin": 3}


def _json(status: int, body: Any, **headers: str) -> HttpResponse:
    return HttpResponse(status, headers, json.dumps(body).encode())


@dataclass
class Installation:
    id: int
    login: str
    type: str
    repositories: dict[int, RepositoryRef]
    permissions: dict[str, str]
    suspended: bool = False
    account_id: int = 1001


@dataclass
class User:
    id: int
    login: str
    # installation ID -> repository IDs this user can access on GitHub
    access: dict[int, set[int]] = field(default_factory=dict)


CLIENT_ID = "Iv23liCommitGuardTest"
CLIENT_SECRET = Secret("integration-client-secret-5e4d3c2b1a")


@dataclass
class Failure:
    method: str
    path: re.Pattern[str]
    outcome: HttpResponse | Exception
    remaining: int


class FakeGitHub:
    def __init__(self, public_key: rsa.RSAPublicKey) -> None:
        self.public_key = public_key
        self.lock = threading.RLock()
        self.installations: dict[int, Installation] = {}
        self.default_branches: dict[int, str] = {}
        self.pulls: dict[tuple[int, int], dict[str, Any]] = {}
        self.check_runs: dict[int, dict[str, Any]] = {}
        self.tokens: dict[str, tuple[int, frozenset[int] | None, dict[str, str]]] = {}
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []
        self.failures: list[Failure] = []
        self.app_permissions = dict(REQUIRED_PERMISSIONS)
        self.app_events = ["pull_request", "push"]
        self.before_request: Callable[[str, str], None] | None = None
        self._ids = 9000
        # dashboard sign-in (GitHub App user authorization)
        self.users: dict[int, User] = {}
        self.oauth_codes: dict[str, tuple[int, str, str]] = {}  # code -> user, challenge, redirect
        self.user_tokens: dict[str, int] = {}
        # enforcement evidence per repository ID
        self.branches: dict[int, dict[str, Any]] = {}
        self.rulesets: dict[int, list[dict[str, Any]]] = {}
        self.workflows: dict[int, dict[str, str]] = {}

    # -- test controls --------------------------------------------------- #
    def add_installation(
        self,
        installation_id: int = INSTALLATION_ID,
        repositories: tuple[RepositoryRef, ...] = (REPO,),
        *,
        login: str = "octo-org",
        account_type: str = "Organization",
        permissions: dict[str, str] | None = None,
        account_id: int = 1001,
    ) -> None:
        self.installations[installation_id] = Installation(
            installation_id,
            login,
            account_type,
            {r.id: r for r in repositories},
            dict(permissions or REQUIRED_PERMISSIONS),
            account_id=account_id,
        )
        for repository in repositories:
            self.default_branches.setdefault(repository.id, "main")

    def fail(
        self, method: str, path: str, outcome: HttpResponse | Exception, times: int = 1_000_000
    ) -> None:
        self.failures.append(Failure(method, re.compile(path), outcome, times))

    def add_user(self, user_id: int, login: str, access: dict[int, set[int]]) -> User:
        user = User(user_id, login, {k: set(v) for k, v in access.items()})
        self.users[user_id] = user
        return user

    def authorize(self, user_id: int, authorize_url: str) -> tuple[str, str]:
        """The user approves the App on github.com: returns (code, state) for the callback."""
        from urllib.parse import parse_qs, urlsplit

        parts = urlsplit(authorize_url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
            "https://github.com/login/oauth/authorize"
        )
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        assert query["client_id"] == CLIENT_ID
        assert query["code_challenge_method"] == "S256"
        code = secrets.token_hex(10)
        self.oauth_codes[code] = (user_id, query["code_challenge"], query["redirect_uri"])
        return code, query["state"]

    def runs_for(self, head_sha: str, name: str = APP_CHECK_NAME) -> list[dict[str, Any]]:
        return [
            r for r in self.check_runs.values() if r["head_sha"] == head_sha and r["name"] == name
        ]

    # -- transport ------------------------------------------------------- #
    def _oauth_token(self, request: HttpRequest) -> HttpResponse:
        from urllib.parse import parse_qs

        form = {k: v[0] for k, v in parse_qs(request.body.decode()).items()}
        grant = self.oauth_codes.pop(form.get("code", ""), None)
        if (
            grant is None
            or form.get("client_id") != CLIENT_ID
            or form.get("client_secret") != CLIENT_SECRET.reveal()
            or form.get("redirect_uri") != grant[2]
        ):
            return _json(200, {"error": "bad_verification_code"})
        digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != grant[1]:
            return _json(200, {"error": "invalid_grant"})
        token = "ghu_" + secrets.token_hex(18)
        self.user_tokens[token] = grant[0]
        return _json(200, {"access_token": token, "token_type": "bearer", "expires_in": 28800})

    def send(self, request: HttpRequest, *, timeout: float) -> HttpResponse:
        if request.url == "https://github.com/login/oauth/access_token":
            with self.lock:
                self.requests.append((request.method, "/login/oauth/access_token", None))
                return self._oauth_token(request)
        path = request.url.removeprefix("https://api.github.com").split("?", 1)[0]
        body = json.loads(request.body) if request.body else None
        query = request.url.split("?", 1)[1] if "?" in request.url else ""
        with self.lock:
            self.requests.append((request.method, path, body))
            hook = self.before_request
        if hook is not None:
            hook(request.method, path)
        with self.lock:
            for failure in self.failures:
                if (
                    failure.remaining > 0
                    and failure.method == request.method
                    and failure.path.search(path)
                ):
                    failure.remaining -= 1
                    if isinstance(failure.outcome, Exception):
                        raise failure.outcome
                    return failure.outcome
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return _json(401, {"message": "Requires authentication"})
            credential = auth.removeprefix("Bearer ")
            if credential in self.user_tokens:
                return self._user_route(request.method, path, self.user_tokens[credential])
            return self._route(request.method, path, body, credential, query)

    def _verify_jwt(self, token: str) -> bool:
        try:
            header, payload, signature = token.split(".")
            pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
            self.public_key.verify(
                pad(signature), f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256()
            )
            claims = json.loads(pad(payload))
        except (ValueError, InvalidSignature):
            return False
        now = datetime.now(UTC).timestamp()
        return bool(claims["iss"] == str(APP_ID) and claims["iat"] <= now < claims["exp"])

    def _token(self, credential: str) -> tuple[int, frozenset[int] | None, dict[str, str]] | None:
        return self.tokens.get(credential)

    def _repo_for_token(self, credential: str, repository_id: int) -> RepositoryRef | None:
        grant = self._token(credential)
        if grant is None:
            return None
        installation = self.installations.get(grant[0])
        if installation is None or installation.suspended:
            return None
        if grant[1] is not None and repository_id not in grant[1]:
            return None
        return installation.repositories.get(repository_id)

    def _repo_json(self, repository: RepositoryRef) -> dict[str, Any]:
        return {
            "id": repository.id,
            "name": repository.name,
            "full_name": repository.full_name,
            "owner": {"id": 1, "login": repository.owner, "type": "Organization"},
            "default_branch": self.default_branches.get(repository.id, "main"),
            "private": True,
        }

    def _installation_json(self, i: Installation) -> dict[str, Any]:
        return {
            "id": i.id,
            "account": {"id": i.account_id, "login": i.login, "type": i.type},
            "repository_selection": "selected",
            "permissions": i.permissions,
            "suspended_at": "2026-01-01T00:00:00Z" if i.suspended else None,
        }

    def _user_route(self, method: str, path: str, user_id: int) -> HttpResponse:
        user = self.users[user_id]
        if path == "/user" and method == "GET":
            return _json(200, {"id": user.id, "login": user.login, "type": "User"})
        if path == "/user/installations" and method == "GET":
            visible = [
                self._installation_json(i)
                for i in self.installations.values()
                if i.id in user.access
            ]
            return _json(200, {"total_count": len(visible), "installations": visible})
        match = re.fullmatch(r"/user/installations/(\d+)/repositories", path)
        if match and method == "GET":
            installation = self.installations.get(int(match.group(1)))
            if installation is None or installation.id not in user.access:
                return _json(404, {"message": "Not Found"})
            repos = [
                self._repo_json(r)
                for rid, r in installation.repositories.items()
                if rid in user.access[installation.id]
            ]
            return _json(200, {"total_count": len(repos), "repositories": repos})
        return _json(404, {"message": "Not Found"})

    def _route(
        self, method: str, path: str, body: Any, credential: str, query: str = ""
    ) -> HttpResponse:
        if path == "/app" and method == "GET":
            if not self._verify_jwt(credential):
                return _json(401, {"message": "A JSON web token could not be decoded"})
            return _json(
                200,
                {
                    "id": APP_ID,
                    "slug": "commitguard",
                    "name": "CommitGuard",
                    "permissions": self.app_permissions,
                    "events": self.app_events,
                },
            )
        if path == "/app/installations" and method == "GET":
            if not self._verify_jwt(credential):
                return _json(401, {"message": "bad jwt"})
            return _json(
                200,
                [self._installation_json(i) for i in self.installations.values()],
            )
        match = re.fullmatch(r"/app/installations/(\d+)", path)
        if match and method == "GET":
            if not self._verify_jwt(credential):
                return _json(401, {"message": "bad jwt"})
            installation = self.installations.get(int(match.group(1)))
            if installation is None:
                return _json(404, {"message": "Not Found"})
            return _json(200, self._installation_json(installation))
        match = re.fullmatch(r"/app/installations/(\d+)/access_tokens", path)
        if match and method == "POST":
            if not self._verify_jwt(credential):
                return _json(401, {"message": "bad jwt"})
            installation = self.installations.get(int(match.group(1)))
            if installation is None:
                return _json(404, {"message": "Not Found"})
            if installation.suspended:
                return _json(403, {"message": "This installation has been suspended"})
            repository_ids = body.get("repository_ids")
            if repository_ids is not None and not set(repository_ids) <= set(
                installation.repositories
            ):
                return _json(
                    422,
                    {
                        "message": "There is at least one repository that does not exist "
                        "or is not accessible"
                    },
                )
            requested = body.get("permissions") or installation.permissions
            for name, level in requested.items():
                if _LEVEL[installation.permissions.get(name, "none")] < _LEVEL[level]:
                    return _json(
                        422,
                        {
                            "message": "The permissions requested are not granted "
                            "to this installation."
                        },
                    )
            token = "ghs_" + secrets.token_hex(18)
            scope = frozenset(repository_ids) if repository_ids is not None else None
            self.tokens[token] = (installation.id, scope, dict(requested))
            expires = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return _json(
                201,
                {
                    "token": token,
                    "expires_at": expires,
                    "permissions": requested,
                    "repositories": [{"id": r} for r in (repository_ids or [])],
                },
            )
        if path == "/installation/repositories" and method == "GET":
            grant = self._token(credential)
            if grant is None:
                return _json(401, {"message": "Bad credentials"})
            installation = self.installations[grant[0]]
            repos = [
                self._repo_json(r)
                for rid, r in installation.repositories.items()
                if grant[1] is None or rid in grant[1]
            ]
            return _json(200, {"total_count": len(repos), "repositories": repos})
        match = re.fullmatch(r"/repositories/(\d+)", path)
        if match and method == "GET":
            if self._token(credential) is None:
                return _json(401, {"message": "Bad credentials"})
            repository = self._repo_for_token(credential, int(match.group(1)))
            if repository is None:
                return _json(404, {"message": "Not Found"})
            return _json(200, self._repo_json(repository))
        match = re.fullmatch(
            r"/repos/([^/]+)/([^/]+)/(branches|rules/branches|contents)/(.+)", path
        )
        if match and method == "GET":
            return self._repository_content(credential, *match.groups(), query)
        match = re.fullmatch(r"/repos/([^/]+)/([^/]+)/(pulls|check-runs)(?:/(\d+))?", path)
        if match:
            grant = self._token(credential)
            if grant is None:
                return _json(401, {"message": "Bad credentials"})
            owner, name, kind, number = match.groups()
            repository = next(
                (
                    r
                    for i in self.installations.values()
                    for r in i.repositories.values()
                    if (r.owner, r.name) == (owner, name)
                ),
                None,
            )
            if repository is None or self._repo_for_token(credential, repository.id) is None:
                return _json(404, {"message": "Not Found"})
            if kind == "pulls" and method == "GET" and number:
                pull = self.pulls.get((repository.id, int(number)))
                if pull is None:
                    return _json(404, {"message": "Not Found"})
                return _json(200, pull)
            if kind == "check-runs":
                if _LEVEL[grant[2].get("checks", "none")] < _LEVEL["write"]:
                    return _json(403, {"message": "Resource not accessible by integration"})
                if method == "POST" and number is None:
                    self._ids += 1
                    run = {
                        "id": self._ids,
                        "repository_id": repository.id,
                        "name": body["name"],
                        "head_sha": body["head_sha"],
                        "status": body["status"],
                        "conclusion": None,
                        "external_id": body.get("external_id"),
                        "output": body.get("output"),
                        "history": [body["status"]],
                    }
                    self.check_runs[run["id"]] = run
                    return _json(
                        201, {"id": run["id"], "head_sha": run["head_sha"], "status": run["status"]}
                    )
                if method == "PATCH" and number is not None:
                    run = self.check_runs.get(int(number))
                    if run is None or run["repository_id"] != repository.id:
                        return _json(404, {"message": "Not Found"})
                    run["status"] = body["status"]
                    run["conclusion"] = body.get("conclusion")
                    run["output"] = body.get("output")
                    run["history"].append(body.get("conclusion") or body["status"])
                    return _json(
                        200, {"id": run["id"], "head_sha": run["head_sha"], "status": run["status"]}
                    )
        return _json(404, {"message": "Not Found"})


    def _repository_content(
        self, credential: str, owner: str, name: str, kind: str, rest: str, query: str
    ) -> HttpResponse:
        from urllib.parse import parse_qs, unquote

        grant = self._token(credential)
        if grant is None:
            return _json(401, {"message": "Bad credentials"})
        repository = next(
            (
                r
                for i in self.installations.values()
                for r in i.repositories.values()
                if (r.owner, r.name) == (owner, name)
            ),
            None,
        )
        if repository is None or self._repo_for_token(credential, repository.id) is None:
            return _json(404, {"message": "Not Found"})
        if kind == "branches":
            branch = self.branches.get(repository.id)
            if branch is None:
                return _json(200, {"name": unquote(rest), "protected": False})
            return _json(200, {"name": unquote(rest), **branch})
        if kind == "rules/branches":
            return _json(200, self.rulesets.get(repository.id, []))
        ref = parse_qs(query).get("ref", [""])[0]
        assert ref, "contents requests must name a ref"
        path = unquote(rest)
        files = self.workflows.get(repository.id, {})
        if path == ".github/workflows":
            entries = [
                {"name": p.rsplit("/", 1)[-1], "path": p, "type": "file", "size": len(t)}
                for p, t in files.items()
            ]
            return _json(200, entries) if entries else _json(404, {"message": "Not Found"})
        if path in files:
            return _json(
                200,
                {
                    "encoding": "base64",
                    "size": len(files[path]),
                    "content": base64.b64encode(files[path].encode()).decode(),
                },
            )
        return _json(404, {"message": "Not Found"})


class LocalRemotes:
    """Maps repository IDs to local bare repositories (``file://`` URLs)."""

    def __init__(self) -> None:
        self.paths: dict[int, Path] = {}

    def url_for(self, repository: RepositoryRef) -> str:
        return self.paths[repository.id].as_uri()


def enable_partial_fetch(bare: Path) -> None:
    for key in ("uploadpack.allowFilter", "uploadpack.allowAnySHA1InWant"):
        subprocess.run(["git", "-C", str(bare), "config", key, "true"], check=True)


@dataclass
class AppEnv:
    service: GitHubAppService
    github: FakeGitHub
    hub: Any
    remotes: LocalRemotes
    private_key_pem: Secret
    data_dir: Path
    sleeps: list[float] = field(default_factory=list)

    # -- webhooks -------------------------------------------------------- #
    def deliver(
        self,
        event: str,
        payload: dict[str, Any],
        *,
        delivery: str | None = None,
        secret: Secret = WEBHOOK_SECRET,
        body: bytes | None = None,
    ) -> WebhookResult:
        raw = body if body is not None else json.dumps(payload).encode()
        headers = {
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery or str(uuid.uuid4()),
            "X-Hub-Signature-256": compute_signature(secret, raw),
            "Content-Type": "application/json",
        }
        return self.service.handle_webhook(headers, raw, remote_addr="192.0.2.10")

    def install(
        self,
        repositories: tuple[RepositoryRef, ...] = (REPO,),
        installation_id: int = INSTALLATION_ID,
    ) -> WebhookResult:
        if installation_id not in self.github.installations:
            self.github.add_installation(installation_id, repositories)
        return self.deliver(
            "installation", installation_payload("created", installation_id, repositories)
        )

    def open_pull_request(
        self,
        base: str,
        head: str,
        *,
        number: int = 7,
        action: str = "opened",
        fork: bool = False,
        repository: RepositoryRef = REPO,
    ) -> dict[str, Any]:
        self.github.pulls[(repository.id, number)] = {
            "number": number,
            "state": "open",
            "merged": False,
            "head": {"sha": head, "ref": "feature", "repo": {"id": 999 if fork else repository.id}},
            "base": {"sha": base, "ref": "main", "repo": {"id": repository.id}},
        }
        return pr_payload(
            base, head, number=number, action=action, fork=fork, repository=repository
        )

    def run(self) -> int:
        return self.service.process_pending()

    def jobs(self) -> list[ScanJob]:
        return self.service.store.list_jobs(installation_id=INSTALLATION_ID)

    def latest_run(self, head_sha: str, name: str = APP_CHECK_NAME) -> dict[str, Any]:
        runs = self.github.runs_for(head_sha, name)
        assert runs, f"no check run {name} for {head_sha[:12]}"
        return runs[-1]


def installation_payload(
    action: str,
    installation_id: int,
    repositories: tuple[RepositoryRef, ...],
    *,
    login: str = "octo-org",
    account_type: str = "Organization",
    account_id: int = 1001,
) -> dict[str, Any]:
    return {
        "action": action,
        "installation": {
            "id": installation_id,
            "account": {"id": account_id, "login": login, "type": account_type},
            "repository_selection": "selected",
            "permissions": dict(REQUIRED_PERMISSIONS),
        },
        "repositories": [
            {"id": r.id, "name": r.name, "full_name": r.full_name} for r in repositories
        ],
    }


def repositories_payload(
    action: str,
    repositories: tuple[RepositoryRef, ...],
    installation_id: int = INSTALLATION_ID,
    *,
    login: str = "octo-org",
    account_id: int = 1001,
) -> dict[str, Any]:
    entries = [{"id": r.id, "name": r.name, "full_name": r.full_name} for r in repositories]
    return {
        "action": action,
        "installation": {
            "id": installation_id,
            "account": {"id": account_id, "login": login, "type": "Organization"},
        },
        "repository_selection": "selected",
        "repositories_added": entries if action == "added" else [],
        "repositories_removed": entries if action == "removed" else [],
    }


def _repository_json(repository: RepositoryRef) -> dict[str, Any]:
    return {
        "id": repository.id,
        "name": repository.name,
        "full_name": repository.full_name,
        "owner": {"login": repository.owner},
        "default_branch": "main",
    }


def pr_payload(
    base: str,
    head: str,
    *,
    number: int = 7,
    action: str = "synchronize",
    fork: bool = False,
    repository: RepositoryRef = REPO,
    installation_id: int = INSTALLATION_ID,
    merged: bool = False,
) -> dict[str, Any]:
    repo_json = _repository_json(repository)
    head_repo = {"full_name": "stranger/project", "id": 999} if fork else repo_json
    return {
        "action": action,
        "number": number,
        "pull_request": {
            "number": number,
            "state": "closed" if action == "closed" else "open",
            "merged": merged,
            "base": {"sha": base, "ref": "main", "repo": repo_json},
            "head": {"sha": head, "ref": "feature", "repo": head_repo},
        },
        "repository": repo_json,
        "installation": {"id": installation_id},
    }


def push_payload(
    before: str,
    after: str,
    *,
    ref: str = "refs/heads/main",
    repository: RepositoryRef = REPO,
    installation_id: int = INSTALLATION_ID,
) -> dict[str, Any]:
    zero = "0" * 40
    return {
        "ref": ref,
        "before": before,
        "after": after,
        "created": before == zero,
        "deleted": after == zero,
        "repository": _repository_json(repository),
        "installation": {"id": installation_id},
    }


@pytest.fixture(scope="session")
def app_private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def make_app(
    hub, tmp_path: Path, app_private_key: rsa.RSAPrivateKey
) -> Iterator[Callable[..., AppEnv]]:  # type: ignore[no-untyped-def]
    services: list[GitHubAppService] = []

    def factory(**options: Any) -> AppEnv:
        enable_partial_fetch(hub.bare)
        pem = Secret(
            app_private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode("ascii")
        )
        github = FakeGitHub(app_private_key.public_key())
        remotes = LocalRemotes()
        remotes.paths[REPO.id] = hub.bare
        sleeps: list[float] = []
        data_dir = tmp_path / f"app-data-{len(services)}"
        service = GitHubAppService.create(
            app_id=APP_ID,
            private_key=pem,
            webhook_secret=WEBHOOK_SECRET,
            data_dir=data_dir,
            transport=github,
            remote_locator=remotes,
            allowed_git_protocols=("file",),
            sleep=sleeps.append,
            **options,
        )
        services.append(service)
        return AppEnv(service, github, hub, remotes, pem, data_dir, sleeps)

    yield factory
    for service in services:
        service.stop()
        service.store.close()


@pytest.fixture
def app(make_app: Callable[..., AppEnv]) -> AppEnv:
    return make_app()


@pytest.fixture
def payloads():  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    return SimpleNamespace(
        pr=pr_payload,
        push=push_payload,
        installation=installation_payload,
        repositories=repositories_payload,
        REPO=REPO,
        INSTALLATION_ID=INSTALLATION_ID,
        WEBHOOK_SECRET=WEBHOOK_SECRET,
        APP_ID=APP_ID,
        CLIENT_ID=CLIENT_ID,
        CLIENT_SECRET=CLIENT_SECRET,
        enable_partial_fetch=enable_partial_fetch,
    )
