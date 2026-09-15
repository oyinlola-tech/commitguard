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

Local demo (everything a reviewer would want to click through, on real data)::

    cd web && npm run build && cd ..
    python tests/e2e/dashboard_harness.py --demo
    # open http://localhost:4173 and choose "Continue with GitHub" (signs in as alice)

``--demo`` replays the complete lifecycle through the real stack - clean, blocked
and warning pull requests, a fix, a GitHub "Re-run", a merge queue group, a GitHub
outage that fails closed, published policy versions and a rollback, an installation
suspended and reconnected, notification settings and delivered notifications - and
replaces the github.com authorization page with an immediate local sign-in. It is a
development tool: the production server has none of these routes.
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
from commitguard.api.http import set_cookie  # noqa: E402
from commitguard.api.settings import DashboardSettings, Environment  # noqa: E402
from commitguard.audit.models import Actor, ActorType  # noqa: E402
from commitguard.controlplane.access import Role  # noqa: E402
from commitguard.controlplane.members import MembershipService  # noqa: E402
from commitguard.core.decision import Action  # noqa: E402
from commitguard.github.app import GitHubAppService  # noqa: E402
from commitguard.github.client import TransportError  # noqa: E402
from commitguard.github.identifiers import RepositoryRef  # noqa: E402
from commitguard.github.permissions import OPTIONAL_PERMISSIONS, REQUIRED_PERMISSIONS  # noqa: E402
from commitguard.notifications.settings import (  # noqa: E402
    NotificationMode,
    NotificationSettings,
)
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
SECURITY = (503, "sam")
ADMIN = (504, "ada")
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
    def __init__(self, port: int, static_dir: Path | None, *, demo: bool = False) -> None:
        self.demo_mode = demo
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
            # E-mail and webhooks go through the whole pipeline but are only recorded.
            notification_settings=NotificationSettings(
                mode=NotificationMode.TEST,
                signing_key=Secret("e2e-notification-signing-key-0123456789"),
                dashboard_origin=self.origin,
                production=False,
            ),
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
        permissions = {**REQUIRED_PERMISSIONS, **OPTIONAL_PERMISSIONS}
        self.github.add_installation(
            fake.INSTALLATION_ID, repositories, account_id=ORG, permissions=permissions
        )
        installation = fake.installation_payload(
            "created", fake.INSTALLATION_ID, repositories, account_id=ORG
        )
        installation["installation"]["permissions"] = permissions
        self.env.deliver("installation", installation)
        members = MembershipService(self.service.store, self.service.audit)
        people = ((OWNER, Role.OWNER), (VIEWER, Role.VIEWER))
        if self.demo_mode:
            people += ((SECURITY, Role.SECURITY_MANAGER), (ADMIN, Role.ADMIN))
        for user, role in people:
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

    # -- Phase 7 ------------------------------------------------------------ #
    def phase7_commit(self) -> dict[str, str]:
        """Pull request #21: an AI-attributed commit (its own branch, independent of #9)."""
        project = self.repos[PROJECT.id]
        git(project.dev, "checkout", "-q", "main")
        base = project.head("main")
        git(project.dev, "checkout", "-q", "-B", "feature-21")
        bad = project.commit(f"feat(invoices): add PDF export\n\n{AI_TRAILER}\n")
        git(project.dev, "push", "-q", "-f", "origin", "feature-21")
        self.pull_request(PROJECT, 21, base, bad, "opened")
        return {"base": base, "head": bad}

    def phase7_fix(self) -> dict[str, str]:
        project = self.repos[PROJECT.id]
        base = project.head("main")
        git(project.dev, "checkout", "-q", "feature-21")
        git(project.dev, "reset", "-q", "--hard", base)
        fixed = project.commit("feat(invoices): add PDF export\n")
        git(project.dev, "push", "-q", "-f", "origin", "feature-21")
        self.pull_request(PROJECT, 21, base, fixed, "synchronize")
        return {"head": fixed}

    def rerun(self, sha: str) -> dict[str, Any]:
        """GitHub "Re-run" on the newest CommitGuard check run for ``sha``."""
        run = self.github.runs_for(sha)[-1]
        result = self.env.deliver("check_run", fake.check_run_payload(run, repository=PROJECT))
        return {"status": result.body.get("status")}

    def merge_group(self) -> dict[str, str]:
        """The merge queue builds a merge group for pull request #21 and requests checks."""
        project = self.repos[PROJECT.id]
        feature = project.head("feature-21")
        git(project.dev, "checkout", "-q", "--detach", "main")
        base = project.head("HEAD")
        git(
            project.dev,
            "merge",
            "-q",
            "--no-ff",
            "--no-verify",
            "-m",
            "Merge pull request #21",
            feature,
        )
        group = project.head("HEAD")
        git(
            project.dev,
            "push",
            "-q",
            "-f",
            "origin",
            f"HEAD:refs/heads/gh-readonly-queue/main/pr-21-{feature}",
        )
        git(project.dev, "checkout", "-q", "feature-21")
        result = self.env.deliver(
            "merge_group", fake.merge_group_payload(base, group, number=21, repository=PROJECT)
        )
        return {"base": base, "head": group, "status": str(result.body.get("status"))}

    def notify(self) -> dict[str, int]:
        result = self.service.notifications.run_once()
        return {"dispatched": result.dispatched, "attempted": result.attempted}

    # -- Demo --------------------------------------------------------------- #
    def sign_in(self, user_id: int, return_to: str = "/dashboard") -> tuple[str, str]:
        """Complete the real OAuth flow against the fake GitHub; returns (token, path)."""
        auth = self.dashboard._auth
        start = auth.begin_sign_in(return_to)
        code, state = self.github.authorize(user_id, start.authorize_url)
        done = auth.complete_sign_in(
            code=code, state=state, cookie_state=start.state, user_agent="CommitGuard demo"
        )
        return done.session_token.reveal(), done.return_to

    def demo(self) -> None:
        """Replay the complete CommitGuard lifecycle, as GitHub would deliver it."""
        from commitguard.controlplane.notifications import NotificationCenter

        self.seed()
        store, service = self.service.store, self.service
        token, _ = self.sign_in(OWNER[0])
        alice = self.dashboard._auth.authenticate(token)
        assert alice is not None

        # Enforcement evidence: payments-api requires the check and uses a merge queue.
        self.github.rulesets[PROJECT.id] = [
            {
                "type": "required_status_checks",
                "parameters": {"required_status_checks": [{"context": "commitguard-app"}]},
            },
            {"type": "merge_queue", "parameters": {}},
        ]
        self.github.workflows[DOCS.id] = {
            ".github/workflows/commitguard.yml": (
                ROOT / ".github/workflows/commitguard.yml"
            ).read_text(encoding="utf-8")
        }
        for repository in (PROJECT, WEB, DOCS):
            self.dashboard._commands.refresh_enforcement(alice, repository.id)

        # Organization notification settings: e-mail for blocked violations, a webhook.
        center = NotificationCenter(store, service.audit, service.notifications.settings)
        settings = center.organization_settings(alice, ORG)
        document = {t.type: t.organization.model_dump() for t in settings.types}
        document["high_violation"]["email"] = True
        center.update_organization(
            alice,
            ORG,
            {
                "expected_version": settings.version,
                "types": document,
                "email_recipients": ["security@octo-org.example"],
            },
        )
        center.add_webhook(
            alice, ORG, {"url": "https://hooks.octo-org.example/commitguard", "confirm": True}
        )

        # Organization policy v1 and v2.
        policies = service.policies
        actor = Actor.user(OWNER[0], OWNER[1])
        now = self.dashboard._now()
        policies.update(
            account_id=ORG,
            actor=actor,
            authenticated_at=now,
            expected_version=0,
            floors={"ai_coauthor": Action.BLOCK},
            reason="Organization rule: AI agents may assist but are never credited as authors",
            confirm_weakening=False,
        )

        # Pull requests: blocked (#9), then #21 blocked, fixed, re-run, merge queue.
        self.ai_commit()
        service.process_pending()
        blocked = self.phase7_commit()
        service.process_pending()
        fixed = self.phase7_fix()
        service.process_pending()
        self.rerun(fixed["head"])
        service.process_pending()
        self.merge_group()
        service.process_pending()

        # A GitHub outage while scanning a push: the check fails closed.
        docs = self.repos[DOCS.id]
        before = docs.head("main")
        git(docs.dev, "checkout", "-q", "main")
        after = docs.commit("docs(handbook): describe incident escalation\n")
        git(docs.dev, "push", "-q", "origin", "main")
        self.github.fail("GET", rf"/repositories/{DOCS.id}$", TransportError("connection reset"))
        self.env.deliver("push", fake.push_payload(before, after, repository=DOCS))
        service.process_pending()
        self.github.failures.clear()

        # A problematic policy is published and rolled back.
        policies.update(
            account_id=ORG,
            actor=Actor.user(ADMIN[0], ADMIN[1]),
            authenticated_at=self.dashboard._now(),
            expected_version=1,
            floors={"bot_identity": Action.BLOCK},
            reason="Tighten bot identities",
            confirm_weakening=True,
        )
        policies.rollback(
            account_id=ORG,
            actor=actor,
            authenticated_at=self.dashboard._now(),
            target_version=1,
            expected_current_version=2,
            reason="v2 dropped the AI co-author floor by mistake",
            confirm=True,
        )

        # The installation is suspended and reconnected.
        installation = self.github.installations[fake.INSTALLATION_ID]
        repositories = (PROJECT, WEB, DOCS)

        def lifecycle(action: str) -> None:
            payload = fake.installation_payload(
                action, fake.INSTALLATION_ID, repositories, account_id=ORG
            )
            payload["installation"]["permissions"] = dict(installation.permissions)
            self.env.deliver("installation", payload)

        installation.suspended = True
        lifecycle("suspend")
        installation.suspended = False
        lifecycle("unsuspend")
        service.notifications.run_once()
        self.dashboard._auth.sign_out(alice)
        print(f"demo: blocked {blocked['head'][:12]}, fixed {fixed['head'][:12]}", flush=True)

    def demo_sign_in(
        self, environ: WSGIEnvironment, start_response: StartResponse
    ) -> Iterable[bytes]:
        """Local stand-in for github.com: sign in as the requested demo user."""
        from urllib.parse import parse_qs

        query = parse_qs(str(environ.get("QUERY_STRING", "")))
        users = {str(u[0]): u for u in (OWNER, VIEWER, SECURITY, ADMIN)}
        user = users.get(query.get("user", [str(OWNER[0])])[0], OWNER)
        token, return_to = self.sign_in(user[0], query.get("return_to", ["/dashboard"])[0])
        start_response(
            "302 Found",
            [
                ("Location", return_to),
                set_cookie("__Host-commitguard_session", token, max_age=8 * 3600),
                ("Content-Length", "0"),
            ],
        )
        return [b""]

    # -- WSGI -------------------------------------------------------------- #
    def wsgi(self, environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        path = environ.get("PATH_INFO", "")
        if self.demo_mode and path in ("/api/v1/auth/login", "/demo/sign-in"):
            return self.demo_sign_in(environ, start_response)
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
            "/__e2e/phase7-commit": self.phase7_commit,
            "/__e2e/phase7-fix": self.phase7_fix,
            "/__e2e/rerun": lambda: self.rerun(str(body["sha"])),
            "/__e2e/merge-group": self.merge_group,
            "/__e2e/notify": self.notify,
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
    parser.add_argument("--seed", action="store_true", help="seed test data at start-up")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="replay the full lifecycle and enable local sign-in (development only)",
    )
    args = parser.parse_args()
    static = args.static.resolve() if (args.static / "index.html").is_file() else None
    stack = Stack(args.port, static, demo=args.demo)
    if args.demo:
        stack.demo()
    elif args.seed:
        stack.seed()
    stack.service.start()
    server = make_server(
        "127.0.0.1", args.port, stack.wsgi, server_class=_Server, handler_class=_Quiet
    )
    print(f"CommitGuard e2e stack on {stack.origin} (data: {stack.tmp})", flush=True)
    if args.demo:
        for user, role in (
            (OWNER, "owner"),
            (ADMIN, "admin"),
            (SECURITY, "security manager"),
            (VIEWER, "viewer"),
        ):
            print(
                f"  sign in as {user[1]} ({role}): {stack.origin}/demo/sign-in?user={user[0]}",
                flush=True,
            )
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
