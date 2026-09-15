"""Run the complete CommitGuard stack for browser end-to-end tests.

Real components: the GitHub App service (webhooks, queue, workers), real Git
repositories fetched into metadata mirrors, the detection and policy engines,
the SQLite state store, the dashboard API and the built dashboard.

Simulated: GitHub itself (``FakeGitHub`` from the integration test harness,
with OAuth, installations, Checks and repository contents). The browser test
intercepts the github.com authorization page and asks ``/__e2e/authorize`` for
the code GitHub would issue.

Test-only control endpoints live under ``/__e2e/`` and exist only in this
script; the production server has no such routes.

Usage::

    python tests/e2e/dashboard_harness.py --port 4173 --static web/dist
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server
from wsgiref.types import StartResponse, WSGIEnvironment

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

import commitguard.api.app as dashboard_app  # noqa: E402
from commitguard.api.hosting import build_dashboard, create_server_app  # noqa: E402
from commitguard.api.settings import DashboardSettings, Environment  # noqa: E402
from commitguard.audit.models import Actor, ActorType  # noqa: E402
from commitguard.controlplane.access import Role  # noqa: E402
from commitguard.controlplane.members import MembershipService  # noqa: E402
from commitguard.github.app import GitHubAppService  # noqa: E402
from commitguard.github.identifiers import RepositoryRef  # noqa: E402
from commitguard.security.secrets import Secret  # noqa: E402


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fake = _load("commitguard_e2e_fake_github", ROOT / "tests/integration/github/app/conftest.py")

ORG = 1001
OWNER = (501, "alice")
VIEWER = (502, "victor")
AI_TRAILER = "Co-authored-by: Claude <noreply@anthropic.com>"
BLOCK_CONFIG = "version: 1\npolicies:\n  ai_coauthor:\n    enabled: true\n    action: block\n"
PROJECT = RepositoryRef(id=5001, owner="octo-org", name="payments-api")
WEB = RepositoryRef(id=5002, owner="octo-org", name="web-console")
DOCS = RepositoryRef(id=5003, owner="octo-org", name="engineering-handbook")


def git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": str(cwd.parent / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Dana Reyes",
        "GIT_AUTHOR_EMAIL": "dana@northwind.dev",
        "GIT_COMMITTER_NAME": "Dana Reyes",
        "GIT_COMMITTER_EMAIL": "dana@northwind.dev",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, check=True, text=True
    ).stdout.strip()


class Repo:
    def __init__(self, base: Path, name: str) -> None:
        self.bare = base / f"{name}.git"
        self.dev = base / f"{name}-dev"
        subprocess.run(
            ["git", "init", "-q", "--bare", "--initial-branch=main", str(self.bare)], check=True
        )
        for key in ("uploadpack.allowFilter", "uploadpack.allowAnySHA1InWant"):
            subprocess.run(["git", "-C", str(self.bare), "config", key, "true"], check=True)
        subprocess.run(
            ["git", "clone", "-q", str(self.bare), str(self.dev)], check=True, capture_output=True
        )
        git(self.dev, "config", "commit.gpgsign", "false")
        git(self.dev, "checkout", "-q", "-B", "main")
        self.commit(
            "chore: initial import\n",
            {".commitguard.yaml": BLOCK_CONFIG, "README.md": "# service\n"},
        )
        git(self.dev, "push", "-q", "origin", "main")

    def commit(
        self, message: str, files: dict[str, str] | None = None, author: str | None = None
    ) -> str:
        for name, content in (files or {"CHANGES.md": message}).items():
            path = self.dev / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(content)
            git(self.dev, "add", "--", name)
        args = ["commit", "-q", "--no-verify", "--cleanup=verbatim", "-m", message]
        if author:
            args.append(f"--author={author}")
        git(self.dev, *args)
        return git(self.dev, "rev-parse", "HEAD")

    def head(self, ref: str = "HEAD") -> str:
        return git(self.dev, "rev-parse", ref)


class Stack:
    def __init__(self, port: int, static_dir: Path | None) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="commitguard-e2e-"))
        self.origin = f"http://localhost:{port}"
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = Secret(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode()
        )
        self.github = fake.FakeGitHub(key.public_key())
        self.remotes = fake.LocalRemotes()
        self.repos = {r.id: Repo(self.tmp, r.name) for r in (PROJECT, WEB, DOCS)}
        for repository_id, repo in self.repos.items():
            self.remotes.paths[repository_id] = repo.bare
        self.service = GitHubAppService.create(
            app_id=fake.APP_ID,
            private_key=pem,
            webhook_secret=fake.WEBHOOK_SECRET,
            data_dir=self.tmp / "data",
            transport=self.github,
            remote_locator=self.remotes,
            allowed_git_protocols=("file",),
            sleep=lambda _seconds: None,
            workers=2,
        )
        settings = DashboardSettings(
            origin=self.origin,
            client_id=fake.CLIENT_ID,
            client_secret=fake.CLIENT_SECRET,
            environment=Environment.DEVELOPMENT,
            static_dir=static_dir,
        )
        # Every browser test signs in from 127.0.0.1; the sign-in limit (20/min per
        # address) is exercised by the API tests, so it is raised for this harness only.
        dashboard_app.RATE_LIMITS["auth"] = 10_000
        self.dashboard = build_dashboard(self.service, settings)
        self.app = create_server_app(self.service, self.dashboard)
        self.env = fake.AppEnv(
            self.service, self.github, None, self.remotes, pem, self.tmp / "data"
        )

    # -- GitHub events ------------------------------------------------------ #
    def pull_request(
        self, repository: RepositoryRef, number: int, base: str, head: str, action: str
    ) -> None:
        self.github.pulls[(repository.id, number)] = {
            "number": number,
            "state": "open",
            "merged": False,
            "head": {"sha": head, "ref": f"feature-{number}", "repo": {"id": repository.id}},
            "base": {"sha": base, "ref": "main", "repo": {"id": repository.id}},
        }
        self.env.deliver(
            "pull_request",
            fake.pr_payload(base, head, number=number, action=action, repository=repository),
        )

    def seed(self) -> dict[str, Any]:
        repositories = (PROJECT, WEB, DOCS)
        self.github.add_installation(fake.INSTALLATION_ID, repositories, account_id=ORG)
        self.env.deliver(
            "installation",
            fake.installation_payload(
                "created", fake.INSTALLATION_ID, repositories, account_id=ORG
            ),
        )
        members = MembershipService(self.service.store, self.service.audit)
        for user, role in ((OWNER, Role.OWNER), (VIEWER, Role.VIEWER)):
            members.grant(
                account_id=ORG,
                user_id=user[0],
                role=role,
                actor=Actor(type=ActorType.SYSTEM, login="e2e"),
                login=user[1],
            )
            self.github.add_user(
                user[0], user[1], {fake.INSTALLATION_ID: {r.id for r in repositories}}
            )
        self.github.rulesets[WEB.id] = [
            {
                "type": "required_status_checks",
                "parameters": {"required_status_checks": [{"context": "commitguard-app"}]},
            }
        ]

        project = self.repos[PROJECT.id]
        base = project.head()
        git(project.dev, "checkout", "-q", "-b", "feature-3")
        clean = project.commit("feat(payments): verify refund webhook signatures\n")
        git(project.dev, "push", "-q", "origin", "feature-3")
        self.pull_request(PROJECT, 3, base, clean, "opened")

        web = self.repos[WEB.id]
        web_base = web.head()
        git(web.dev, "checkout", "-q", "-b", "deps")
        bot = web.commit(
            "chore(deps): bump vite\n",
            author="dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>",
        )
        git(web.dev, "push", "-q", "origin", "deps")
        self.pull_request(WEB, 12, web_base, bot, "opened")
        self.service.process_pending()
        return {"base": base, "clean": clean}

    def ai_commit(self) -> dict[str, str]:
        project = self.repos[PROJECT.id]
        git(project.dev, "checkout", "-q", "main")
        base = project.head("main")
        git(project.dev, "checkout", "-q", "-B", "feature-9")
        good = project.commit("feat(ledger): add settlement export\n")
        bad = project.commit(f"fix(ledger): round settlement totals\n\n{AI_TRAILER}\n")
        git(project.dev, "push", "-q", "-f", "origin", "feature-9")
        self.pull_request(PROJECT, 9, base, bad, "opened")
        return {"base": base, "good": good, "head": bad}

    def fix_commit(self) -> dict[str, str]:
        project = self.repos[PROJECT.id]
        base = project.head("main")
        good = project.head("HEAD~1")
        git(project.dev, "reset", "-q", "--hard", good)
        fixed = project.commit("fix(ledger): round settlement totals\n")
        git(project.dev, "push", "-q", "-f", "origin", "feature-9")
        self.pull_request(PROJECT, 9, base, fixed, "synchronize")
        return {"head": fixed}

    # -- WSGI -------------------------------------------------------------- #
    def wsgi(self, environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        path = environ.get("PATH_INFO", "")
        if not path.startswith("/__e2e/"):
            return self.app(environ, start_response)
        length = int(environ.get("CONTENT_LENGTH") or 0)
        body = json.loads(environ["wsgi.input"].read(length) or b"{}")
        routes: dict[str, Callable[[], Any]] = {
            "/__e2e/authorize": lambda: dict(
                zip(
                    ("code", "state"),
                    self.github.authorize(int(body["user_id"]), body["url"]),
                    strict=True,
                )
            ),
            "/__e2e/seed": self.seed,
            "/__e2e/ai-commit": self.ai_commit,
            "/__e2e/fix-commit": self.fix_commit,
            "/__e2e/check": lambda: {
                "conclusion": (self.github.runs_for(body["sha"]) or [{}])[-1].get("conclusion")
            },
            "/__e2e/drain": lambda: {"processed": self.service.process_pending()},
        }
        handler = routes.get(path)
        if handler is None:
            start_response("404 Not Found", [("Content-Type", "application/json")])
            return [b"{}"]
        payload = json.dumps(handler()).encode()
        start_response(
            "200 OK", [("Content-Type", "application/json"), ("Content-Length", str(len(payload)))]
        )
        return [payload]


class _Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _Quiet(WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--static", type=Path, default=ROOT / "web" / "dist")
    parser.add_argument("--seed", action="store_true", help="seed demo data at start-up")
    args = parser.parse_args()
    static = args.static.resolve() if (args.static / "index.html").is_file() else None
    stack = Stack(args.port, static)
    if args.seed:
        stack.seed()
    stack.service.start()
    server = make_server(
        "127.0.0.1", args.port, stack.wsgi, server_class=_Server, handler_class=_Quiet
    )
    print(f"CommitGuard e2e stack on {stack.origin} (data: {stack.tmp})", flush=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        stack.service.stop()
        server.shutdown()


if __name__ == "__main__":
    main()
