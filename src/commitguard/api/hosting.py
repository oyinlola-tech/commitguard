"""One process, one origin: webhooks, the dashboard API and the dashboard itself.

::

    /webhooks/github, /health, /ready  -> GitHub App service (Phase 5, unchanged)
    /api/v1/...                        -> DashboardApi
    everything else (GET/HEAD)         -> built dashboard files; unknown paths
                                          without a file extension -> index.html
                                          (client-side routes, deep links, refresh)

Serving the dashboard from the API's origin keeps cookies ``SameSite`` and
first-party and needs no CORS. The dashboard is a static bundle: it holds no
secrets, and its Content Security Policy allows scripts, styles, fonts and
API calls from the same origin only (no inline scripts, no ``eval``).
"""

import mimetypes
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from wsgiref.types import StartResponse, WSGIEnvironment

from commitguard.api.app import DashboardApi
from commitguard.api.settings import DashboardSettings, Environment
from commitguard.controlplane.commands import ControlPlaneCommands
from commitguard.controlplane.identity import AuthService
from commitguard.controlplane.members import MembershipService
from commitguard.controlplane.notifications import NotificationCenter
from commitguard.controlplane.queries import DashboardQueries
from commitguard.github.app import GitHubAppService, create_wsgi_app
from commitguard.github.enforcement_status import EnforcementProbe

type WSGIApp = Callable[[WSGIEnvironment, StartResponse], Iterable[bytes]]

MAX_STATIC_FILE_BYTES = 20 * 1024 * 1024

DASHBOARD_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)

_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".txt": "text/plain; charset=utf-8",
    ".webmanifest": "application/manifest+json",
}


class StaticSite:
    """Serves a built single-page application from a directory, read-only."""

    def __init__(self, root: Path, settings: DashboardSettings) -> None:
        self._root = root.resolve()
        self._settings = settings

    def _headers(self, path: Path) -> list[tuple[str, str]]:
        content_type = _TYPES.get(path.suffix) or mimetypes.guess_type(path.name)[0]
        immutable = path.parent.name == "assets"
        headers = [
            ("Content-Type", content_type or "application/octet-stream"),
            (
                "Cache-Control",
                "public, max-age=31536000, immutable" if immutable else "no-cache",
            ),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Frame-Options", "DENY"),
            ("Cross-Origin-Opener-Policy", "same-origin"),
            ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"),
            ("Content-Security-Policy", DASHBOARD_CSP),
        ]
        if self._settings.environment is Environment.PRODUCTION and self._settings.https:
            headers.append(("Strict-Transport-Security", "max-age=63072000; includeSubDomains"))
        return headers

    def resolve(self, request_path: str) -> Path | None:
        """The file for a request path, the SPA entry point, or None (404)."""
        relative = request_path.lstrip("/")
        if "\x00" in relative or "\\" in relative:
            return None
        parts = [p for p in relative.split("/") if p]
        if any(p in (".", "..") or p.startswith(".") for p in parts):
            return None
        candidate = (self._root.joinpath(*parts) if parts else self._root / "index.html").resolve()
        if not candidate.is_relative_to(self._root):
            return None
        if candidate.is_file():
            return candidate
        if parts and "." in parts[-1]:
            return None  # a missing asset is a 404, not the application shell
        index = self._root / "index.html"
        return index if index.is_file() else None

    def __call__(self, environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET")
        if method not in ("GET", "HEAD"):
            start_response(
                "405 Method Not Allowed",
                [("Allow", "GET, HEAD"), ("Content-Type", "text/plain"), ("Content-Length", "0")],
            )
            return [b""]
        path = self.resolve(str(environ.get("PATH_INFO", "/")))
        if path is None or path.stat().st_size > MAX_STATIC_FILE_BYTES:
            body = b"Not Found"
            start_response(
                "404 Not Found",
                [
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                    ("X-Content-Type-Options", "nosniff"),
                ],
            )
            return [body]
        data = path.read_bytes()
        headers = self._headers(path)
        headers.append(("Content-Length", str(len(data))))
        start_response("200 OK", headers)
        return [data] if method == "GET" else [b""]


def build_dashboard(
    service: GitHubAppService,
    settings: DashboardSettings,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> DashboardApi:
    """Wire the dashboard API to the GitHub App service's store, client and workers."""
    store = service.store
    queries = DashboardQueries(store, now=now)
    auth = AuthService(
        store,
        service.client,
        service.audit,
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        redirect_uri=settings.redirect_uri,
        now=now,
    )
    commands = ControlPlaneCommands(
        store,
        queries,
        service.audit,
        service.installations,
        EnforcementProbe(service.client, now=now),
        enqueue=service.queue.put,
        now=now,
    )
    service.add_maintenance_task(auth.purge_expired)
    return DashboardApi(
        settings=settings,
        auth=auth,
        queries=queries,
        commands=commands,
        policies=service.policies,
        members=MembershipService(store, service.audit, now=now),
        notifications=NotificationCenter(
            store, service.audit, service.notifications.settings, now=now
        ),
        now=now,
    )


def create_server_app(
    service: GitHubAppService,
    dashboard: DashboardApi | None = None,
) -> WSGIApp:
    webhooks = create_wsgi_app(service)
    static = (
        StaticSite(dashboard.settings.static_dir, dashboard.settings)
        if dashboard is not None and dashboard.settings.static_dir is not None
        else None
    )

    def application(environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        path = str(environ.get("PATH_INFO", ""))
        if path == "/api" or path.startswith("/api/"):
            if dashboard is None:
                return webhooks(environ, start_response)  # JSON 404
            return dashboard(environ, start_response)
        if path in ("/health", "/ready") or path.startswith("/webhooks/"):
            return webhooks(environ, start_response)
        if static is not None:
            return static(environ, start_response)
        return webhooks(environ, start_response)

    return application
