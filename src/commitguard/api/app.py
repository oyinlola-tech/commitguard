"""The ``/api/v1`` WSGI application.

Every route declares, in the table at the bottom of :class:`DashboardApi`:

* whether it is public (only sign-in routes are);
* the permission whose access scope its handler receives - authentication,
  tenant resolution and the permission check happen in :meth:`DashboardApi._dispatch`,
  never ad hoc in handlers;
* a rate-limit category.

Handlers only parse input, call a control plane service and serialise its
result. Resource-level checks (does this scan belong to a repository the user
can see? may their role acknowledge violations in that organisation?) live in
the services, which answer "not found" for anything outside the caller's scope.
"""

import re
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from wsgiref.types import StartResponse, WSGIEnvironment

from commitguard.api.http import (
    API_SECURITY_HEADERS,
    MAX_BODY_BYTES,
    NOT_FOUND,
    REASONS,
    ApiError,
    Request,
    Response,
    bad_request,
    clear_cookie,
    error_response,
    ok,
    parse_cookies,
    parse_query,
    redirect,
    set_cookie,
)
from commitguard.api.settings import DashboardSettings, Environment
from commitguard.audit.models import Actor, AuditEventType
from commitguard.controlplane.access import Permission, Principal, Role
from commitguard.controlplane.commands import ControlPlaneCommands
from commitguard.controlplane.errors import ControlPlaneError
from commitguard.controlplane.identity import (
    SESSION_LIFETIME,
    AuthService,
    SessionExpiredError,
    csrf_token_for,
)
from commitguard.controlplane.members import MembershipService
from commitguard.controlplane.pagination import (
    encode_cursor,
    offset_cursor,
    parse_choice,
    parse_int_id,
    parse_limit,
    parse_search,
    parse_timestamp,
)
from commitguard.controlplane.policies import (
    REAUTHENTICATION_WINDOW,
    OrganizationPolicyService,
    policy_changes,
    validate_floors,
)
from commitguard.controlplane.queries import (
    AUDIT_SORTS,
    PERIODS,
    REPOSITORY_SORTS,
    SCAN_RESULTS,
    SCAN_SORTS,
    VIOLATION_SORTS,
    AuditFilters,
    DashboardQueries,
    RepositoryFilters,
    ScanFilters,
    ViolationFilters,
)
from commitguard.controlplane.rules import list_rules, rule_detail
from commitguard.controlplane.views import (
    OrganizationRef,
    OrganizationView,
    ProtectionStatus,
    SessionInfo,
    UserView,
    ViolationStatus,
)
from commitguard.core.decision import Action
from commitguard.core.result import Severity
from commitguard.observability.logging import get_logger
from commitguard.policies.defaults import KNOWN_POLICY_IDS
from commitguard.security.rate_limit import RequestRateLimiter

log = get_logger(__name__)

API_PREFIX = "/api/v1"
SESSION_COOKIE = "__Host-commitguard_session"
STATE_COOKIE = "__Host-commitguard_oauth_state"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_CONVERTERS = {
    "int": (r"[1-9][0-9]{0,15}", int),
    "hex": (r"[0-9a-f]{32}", str),
    "ident": (r"[a-z][a-z0-9_]{0,63}", str),
    "session": (r"[0-9a-f]{16}", str),
    "version": (r"[1-9][0-9]{0,8}", int),
}

#: requests per minute, per user (or client address before sign-in)
RATE_LIMITS = {
    "auth": 20,
    "read": 600,
    "search": 120,
    "write": 30,
    "github": 10,
}


@dataclass(frozen=True, slots=True)
class Route:
    method: str
    template: str
    handler: Callable[[Request], Response]
    permission: Permission | None
    public: bool
    rate: str
    pattern: re.Pattern[str]
    converters: Mapping[str, Callable[[str], Any]]


def _compile(template: str) -> tuple[re.Pattern[str], dict[str, Callable[[str], Any]]]:
    converters: dict[str, Callable[[str], Any]] = {}

    def replace(match: re.Match[str]) -> str:
        name, kind = match.group(1), match.group(2)
        regex, convert = _CONVERTERS[kind]
        converters[name] = convert
        return f"(?P<{name}>{regex})"

    pattern = re.sub(
        r"\{([a-z_]+):([a-z]+)\}",
        replace,
        re.escape(template).replace(r"\{", "{").replace(r"\}", "}"),
    )
    return re.compile(rf"\A{pattern}\Z"), converters


def _page_meta(next_cursor: str | None, limit: int, **extra: Any) -> dict[str, Any]:
    return {"next_cursor": next_cursor, "limit": limit, **extra}


class DashboardApi:
    def __init__(
        self,
        *,
        settings: DashboardSettings,
        auth: AuthService,
        queries: DashboardQueries,
        commands: ControlPlaneCommands,
        policies: OrganizationPolicyService,
        members: MembershipService,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self._auth = auth
        self._queries = queries
        self._commands = commands
        self._policies = policies
        self._members = members
        self._now = now
        self._limiters = {k: RequestRateLimiter(v, clock) for k, v in RATE_LIMITS.items()}
        self._routes = self._build_routes()

    # ------------------------------------------------------------------ #
    # WSGI
    # ------------------------------------------------------------------ #
    def __call__(self, environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        started = time.monotonic()
        request_id = uuid.uuid4().hex
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", ""))
        origin = environ.get("HTTP_ORIGIN")
        request: Request | None = None
        try:
            if method == "OPTIONS":
                response = self._preflight(origin, environ)
            else:
                request = self._parse(environ, method, path, request_id)
                response = self._dispatch(request)
        except ApiError as exc:
            response = error_response(exc, request_id)
        except ControlPlaneError as exc:
            response = error_response(ApiError.from_control_plane(exc), request_id)
        except Exception as exc:  # noqa: BLE001 - never leak internals to the client
            log.error("api_internal_error", request_id=request_id, error_type=type(exc).__name__)
            response = error_response(
                ApiError(500, "INTERNAL_ERROR", "Something went wrong. Try again."), request_id
            )
        headers = list(response.headers)
        headers.extend(API_SECURITY_HEADERS)
        headers.append(("X-Request-ID", request_id))
        if self.settings.environment is Environment.PRODUCTION and self.settings.https:
            headers.append(("Strict-Transport-Security", "max-age=63072000; includeSubDomains"))
        headers.extend(self._cors_headers(origin))
        headers.append(("Content-Length", str(len(response.body))))
        start_response(f"{response.status} {REASONS.get(response.status, 'Error')}", headers)
        log.info(
            "api_request",
            request_id=request_id,
            method=method,
            route=request.route if request else "preflight",
            status=response.status,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            user_id=request.principal.user_id if request and request.principal else None,
        )
        return [response.body] if method != "HEAD" else [b""]

    def _parse(self, environ: WSGIEnvironment, method: str, path: str, request_id: str) -> Request:
        headers = {
            key[5:].replace("_", "-").lower(): value
            for key, value in environ.items()
            if key.startswith("HTTP_") and isinstance(value, str)
        }
        if environ.get("CONTENT_TYPE"):
            headers["content-type"] = str(environ["CONTENT_TYPE"])
        body = b""
        if method in UNSAFE_METHODS:
            raw_length = str(environ.get("CONTENT_LENGTH", "") or "0")
            if not raw_length.isascii() or not raw_length.isdigit():
                raise ApiError(411, "LENGTH_REQUIRED", "A valid Content-Length is required.")
            length = int(raw_length)
            if length > MAX_BODY_BYTES:
                raise ApiError(413, "PAYLOAD_TOO_LARGE", "The request body is too large.")
            body = environ["wsgi.input"].read(length) if length else b""
            if len(body) != length:
                raise bad_request("The request body is incomplete.")
        return Request(
            method=method,
            path=path,
            query=parse_query(environ),
            headers=headers,
            cookies=parse_cookies(headers.get("cookie")),
            body=body,
            remote_addr=str(environ.get("REMOTE_ADDR", "unknown")),
            request_id=request_id,
        )

    def _match(self, request: Request) -> Route:
        allowed: list[str] = []
        for route in self._routes:
            match = route.pattern.match(request.path)
            if match is None:
                continue
            if route.method != request.method and not (
                request.method == "HEAD" and route.method == "GET"
            ):
                allowed.append(route.method)
                continue
            request.params = {k: route.converters[k](v) for k, v in match.groupdict().items()}
            request.route = f"{route.method} {route.template}"
            return route
        if allowed:
            raise ApiError(
                405,
                "METHOD_NOT_ALLOWED",
                "This method is not allowed for this resource.",
                headers=[("Allow", ", ".join(sorted(set(allowed))))],
            )
        raise ApiError(404, "NOT_FOUND", NOT_FOUND)

    def _dispatch(self, request: Request) -> Response:
        route = self._match(request)
        if not route.public:
            self._authenticate(request)
        principal = request.principal
        category = "search" if route.rate == "read" and request.arg("q") else route.rate
        key = f"user:{principal.user_id}" if principal else f"addr:{request.remote_addr}"
        if not self._limiters[category].allow(key):
            raise ApiError(
                429,
                "RATE_LIMITED",
                "Too many requests. Wait a minute and try again.",
                headers=[("Retry-After", "60")],
            )
        if request.method in UNSAFE_METHODS:
            self._check_csrf(request)
        if route.permission is not None and principal is not None:
            organization = parse_int_id(request.arg("organization"), "organization")
            if organization is not None and organization not in principal.accounts_with(
                route.permission
            ):
                raise ApiError(404, "NOT_FOUND", NOT_FOUND)
            request.scope = principal.scope(route.permission, account_id=organization)
        return route.handler(request)

    def _authenticate(self, request: Request) -> None:
        token = request.cookies.get(SESSION_COOKIE)
        try:
            principal = self._auth.authenticate(token)
        except SessionExpiredError as exc:
            raise ApiError(
                401, exc.code, str(exc), headers=[clear_cookie(SESSION_COOKIE)]
            ) from None
        if principal is None:
            raise ApiError(401, "UNAUTHENTICATED", "Sign in to continue.")
        request.principal = principal
        request.session_token = token

    def _check_csrf(self, request: Request) -> None:
        origin = request.header("origin")
        if origin is None or origin not in self.settings.trusted_origins:
            raise ApiError(403, "CSRF_FAILED", "The request origin is not allowed.")
        if not self._auth.verify_csrf(request.session_token, request.header("x-csrf-token")):
            raise ApiError(403, "CSRF_FAILED", "The request is missing a valid CSRF token.")

    def _cors_headers(self, origin: str | None) -> list[tuple[str, str]]:
        if origin is None or origin not in self.settings.cors_origins:
            return [("Vary", "Origin")]
        return [
            ("Access-Control-Allow-Origin", origin),
            ("Access-Control-Allow-Credentials", "true"),
            ("Access-Control-Expose-Headers", "X-Request-ID"),
            ("Vary", "Origin"),
        ]

    def _preflight(self, origin: str | None, environ: WSGIEnvironment) -> Response:
        if origin is None or origin not in self.settings.cors_origins:
            raise ApiError(
                403, "CORS_REJECTED", "Cross-origin requests from this origin are not allowed."
            )
        return Response(
            204,
            b"",
            [
                ("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE"),
                ("Access-Control-Allow-Headers", "Content-Type, X-CSRF-Token"),
                ("Access-Control-Max-Age", "600"),
            ],
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _principal(request: Request) -> Principal:
        if request.principal is None:  # pragma: no cover - guaranteed by _dispatch
            raise ApiError(401, "UNAUTHENTICATED", "Sign in to continue.")
        return request.principal

    @staticmethod
    def _scope(request: Request):  # type: ignore[no-untyped-def]
        if request.scope is None:  # pragma: no cover - guaranteed by _dispatch
            raise ApiError(403, "FORBIDDEN", "You do not have permission to access this resource.")
        return request.scope

    @staticmethod
    def _organization(
        principal: Principal, organization_id: int, permission: Permission
    ) -> OrganizationRef:
        """An organization the caller belongs to (else 404), after checking ``permission``."""
        membership = principal.memberships.get(organization_id)
        if membership is None:
            raise ApiError(404, "NOT_FOUND", NOT_FOUND)
        if permission not in membership.role.permissions:
            raise ApiError(403, "FORBIDDEN", "You do not have permission to perform this action.")
        return OrganizationRef(
            id=organization_id, login=membership.account_login, type=membership.account_type
        )

    @staticmethod
    def _bool(value: object, field: str) -> bool:
        if not isinstance(value, bool):
            raise bad_request(f"{field} must be true or false", field=field)
        return value

    # ------------------------------------------------------------------ #
    # Authentication
    # ------------------------------------------------------------------ #
    def login(self, request: Request) -> Response:
        start = self._auth.begin_sign_in(request.arg("return_to"))
        return redirect(start.authorize_url, [set_cookie(STATE_COOKIE, start.state, max_age=600)])

    def callback(self, request: Request) -> Response:
        if request.arg("error"):
            return redirect("/login?error=access_denied", [clear_cookie(STATE_COOKIE)])
        try:
            completed = self._auth.complete_sign_in(
                code=request.arg("code"),
                state=request.arg("state"),
                cookie_state=request.cookies.get(STATE_COOKIE),
                user_agent=request.header("user-agent") or "",
            )
        except ControlPlaneError as exc:
            code = "github_unavailable" if exc.code == "GITHUB_UNAVAILABLE" else "sign_in_failed"
            log.warning("sign_in_failed", request_id=request.request_id, code=exc.code)
            return redirect(f"/login?error={code}", [clear_cookie(STATE_COOKIE)])
        return redirect(
            quote(completed.return_to, safe="/?=&%-_.~"),
            [
                clear_cookie(STATE_COOKIE),
                set_cookie(
                    SESSION_COOKIE,
                    completed.session_token.reveal(),
                    max_age=int(SESSION_LIFETIME.total_seconds()),
                ),
            ],
        )

    def session(self, request: Request) -> Response:
        principal = self._principal(request)
        sessions = self._auth.list_sessions(principal)
        current = next(s for s in sessions if s.current)
        organizations = self._organization_views(principal)
        info = SessionInfo(
            user=UserView(id=principal.user_id, login=principal.login),
            session=current,
            csrf_token=csrf_token_for(request.session_token or ""),
            organizations=organizations,
            reauthentication_required_after=principal.authenticated_at + REAUTHENTICATION_WINDOW,
        )
        return ok(info)

    def logout(self, request: Request) -> Response:
        self._auth.sign_out(self._principal(request))
        response = ok({"signed_out": True})
        response.headers.append(clear_cookie(SESSION_COOKIE))
        return response

    def sessions(self, request: Request) -> Response:
        return ok(self._auth.list_sessions(self._principal(request)))

    def revoke_session(self, request: Request) -> Response:
        principal = self._principal(request)
        self._auth.revoke_session(principal, request.params["session_id"])
        response = ok({"revoked": True})
        if request.params["session_id"] == principal.session_public_id:
            response.headers.append(clear_cookie(SESSION_COOKIE))
        return response

    # ------------------------------------------------------------------ #
    # Overview, organizations and members
    # ------------------------------------------------------------------ #
    def overview(self, request: Request) -> Response:
        period = parse_choice(request.arg("period"), {k: k for k in PERIODS}, "period") or "7d"
        organization = parse_int_id(request.arg("organization"), "organization")
        view = self._queries.overview(
            self._principal(request), period=period, organization_id=organization
        )
        return ok(view)

    @staticmethod
    def _organization_views(principal: Principal) -> tuple[OrganizationView, ...]:
        return tuple(
            OrganizationView(
                organization=OrganizationRef(
                    id=m.account_id, login=m.account_login, type=m.account_type
                ),
                role=m.role.value,
                implicit_role=m.implicit,
                permissions=tuple(sorted(p.value for p in m.role.permissions)),
                installation_ids=tuple(
                    sorted(i for i, a in principal.installations.items() if a == m.account_id)
                ),
            )
            for m in sorted(principal.memberships.values(), key=lambda m: m.account_login.lower())
        )

    def organizations(self, request: Request) -> Response:
        return ok(self._organization_views(self._principal(request)))

    def list_members(self, request: Request) -> Response:
        principal = self._principal(request)
        organization = self._organization(
            principal, request.params["organization_id"], Permission.MEMBERS_READ
        )
        offset = offset_cursor(request.arg("cursor"))
        limit = parse_limit(request.arg("limit"))
        members = self._members.list_members(organization.id, offset=offset, limit=limit + 1)
        next_cursor = None
        if len(members) > limit:
            members = members[:limit]
            next_cursor = encode_cursor([offset + limit])
        return ok(members, _page_meta(next_cursor, limit))

    def put_member(self, request: Request) -> Response:
        principal = self._principal(request)
        organization = self._organization(
            principal, request.params["organization_id"], Permission.MEMBERS_MANAGE
        )
        body = request.json()
        role = parse_choice(
            body.get("role") if isinstance(body.get("role"), str) else "",
            {r.value: r for r in Role},
            "role",
        )
        if role is None:
            raise bad_request("role is required", field="role")
        login = body.get("login")
        if login is not None and not isinstance(login, str):
            raise bad_request("login must be text", field="login")
        member = self._members.grant(
            account_id=organization.id,
            user_id=request.params["user_id"],
            role=role,
            actor=Actor.user(principal.user_id, principal.login),
            login=login,
        )
        return ok(member)

    def delete_member(self, request: Request) -> Response:
        principal = self._principal(request)
        organization = self._organization(
            principal, request.params["organization_id"], Permission.MEMBERS_MANAGE
        )
        self._members.remove(
            account_id=organization.id,
            user_id=request.params["user_id"],
            actor=Actor.user(principal.user_id, principal.login),
        )
        return ok({"removed": True})

    # ------------------------------------------------------------------ #
    # Repositories
    # ------------------------------------------------------------------ #
    def list_repositories(self, request: Request) -> Response:
        filters = RepositoryFilters(
            organization_id=parse_int_id(request.arg("organization"), "organization"),
            protection=parse_choice(
                request.arg("protection"), {p.value: p for p in ProtectionStatus}, "protection"
            ),
            q=parse_search(request.arg("q")),
            sort=parse_choice(request.arg("sort"), {s: s for s in REPOSITORY_SORTS}, "sort")
            or "name",
        )
        limit = parse_limit(request.arg("limit"))
        page = self._queries.list_repositories(
            self._scope(request), filters, offset=offset_cursor(request.arg("cursor")), limit=limit
        )
        return ok(page.items, _page_meta(page.next_cursor, page.limit))

    def get_repository(self, request: Request) -> Response:
        detail = self._queries.get_repository(
            self._scope(request),
            request.params["repository_id"],
            principal=self._principal(request),
        )
        if detail is None:
            raise ApiError(404, "NOT_FOUND", NOT_FOUND)
        return ok(detail)

    def put_monitoring(self, request: Request) -> Response:
        body = request.json()
        self._commands.set_monitoring(
            self._principal(request),
            request.params["repository_id"],
            enabled=body.get("enabled"),
            confirm=body.get("confirm"),
            reason=body.get("reason"),
        )
        return self.get_repository(request)

    def refresh_enforcement(self, request: Request) -> Response:
        self._commands.refresh_enforcement(
            self._principal(request), request.params["repository_id"]
        )
        return self.get_repository(request)

    # ------------------------------------------------------------------ #
    # Scans
    # ------------------------------------------------------------------ #
    def list_scans(self, request: Request) -> Response:
        rule = request.arg("rule")
        if rule and rule not in KNOWN_POLICY_IDS:
            raise bad_request("unknown rule", field="rule")
        filters = ScanFilters(
            organization_id=parse_int_id(request.arg("organization"), "organization"),
            repository_id=parse_int_id(request.arg("repository"), "repository"),
            result=parse_choice(request.arg("result"), dict(SCAN_RESULTS), "result"),
            event=parse_choice(
                request.arg("event"), {"pull_request": "pull_request", "push": "push"}, "event"
            ),
            rule_id=rule or None,
            severity=parse_choice(
                request.arg("severity"), {s.value: s for s in Severity}, "severity"
            ),
            start=parse_timestamp(request.arg("from"), "from"),
            end=parse_timestamp(request.arg("to"), "to"),
            q=parse_search(request.arg("q")),
            sort=parse_choice(request.arg("sort"), {s: s for s in SCAN_SORTS}, "sort") or "newest",
        )
        limit = parse_limit(request.arg("limit"))
        page = self._queries.list_scans(
            self._scope(request), filters, cursor=request.arg("cursor"), limit=limit
        )
        return ok(page.items, _page_meta(page.next_cursor, page.limit))

    def get_scan(self, request: Request) -> Response:
        detail = self._queries.get_scan(
            self._scope(request), request.params["scan_id"], principal=self._principal(request)
        )
        if detail is None:
            raise ApiError(404, "NOT_FOUND", NOT_FOUND)
        return ok(detail)

    def compare_scan(self, request: Request) -> Response:
        comparison = self._queries.compare_scan(self._scope(request), request.params["scan_id"])
        if comparison is None:
            raise ApiError(404, "NOT_FOUND", NOT_FOUND)
        return ok(comparison)

    def rescan(self, request: Request) -> Response:
        job_id = self._commands.request_rescan(self._principal(request), request.params["scan_id"])
        return ok({"scan": job_id, "result": "queued"}, status=202)

    # ------------------------------------------------------------------ #
    # Violations
    # ------------------------------------------------------------------ #
    def list_violations(self, request: Request) -> Response:
        rule = request.arg("rule")
        if rule and rule not in KNOWN_POLICY_IDS:
            raise bad_request("unknown rule", field="rule")
        filters = ViolationFilters(
            organization_id=parse_int_id(request.arg("organization"), "organization"),
            repository_id=parse_int_id(request.arg("repository"), "repository"),
            status=parse_choice(
                request.arg("status"), {s.value: s for s in ViolationStatus}, "status"
            ),
            severity=parse_choice(
                request.arg("severity"), {s.value: s for s in Severity}, "severity"
            ),
            rule_id=rule or None,
            action=parse_choice(
                request.arg("action"), {a.value: a for a in (Action.BLOCK, Action.WARN)}, "action"
            ),
            start=parse_timestamp(request.arg("from"), "from"),
            end=parse_timestamp(request.arg("to"), "to"),
            q=parse_search(request.arg("q")),
            sort=parse_choice(request.arg("sort"), {s: s for s in VIOLATION_SORTS}, "sort")
            or "newest",
        )
        limit = parse_limit(request.arg("limit"))
        page = self._queries.list_violations(
            self._scope(request), filters, cursor=request.arg("cursor"), limit=limit
        )
        return ok(page.items, _page_meta(page.next_cursor, page.limit))

    def get_violation(self, request: Request) -> Response:
        detail = self._queries.get_violation(
            self._scope(request), request.params["violation_id"], principal=self._principal(request)
        )
        if detail is None:
            raise ApiError(404, "NOT_FOUND", NOT_FOUND)
        return ok(detail)

    def acknowledge(self, request: Request) -> Response:
        body = request.json()
        self._commands.acknowledge_violation(
            self._principal(request), request.params["violation_id"], body.get("note")
        )
        return self.get_violation(request)

    def unacknowledge(self, request: Request) -> Response:
        self._commands.remove_acknowledgement(
            self._principal(request), request.params["violation_id"]
        )
        return self.get_violation(request)

    # ------------------------------------------------------------------ #
    # Policies and rules
    # ------------------------------------------------------------------ #
    def list_policies(self, request: Request) -> Response:
        principal = self._principal(request)
        organization = parse_int_id(request.arg("organization"), "organization")
        views = []
        for account_id in principal.accounts_with(Permission.POLICIES_READ):
            if organization is not None and account_id != organization:
                continue
            membership = principal.memberships[account_id]
            views.append(
                self._policies.view(
                    OrganizationRef(
                        id=account_id, login=membership.account_login, type=membership.account_type
                    ),
                    can_write=principal.can(Permission.POLICIES_WRITE, account_id),
                )
            )
        return ok(views)

    def get_policy(self, request: Request) -> Response:
        principal = self._principal(request)
        organization = self._organization(
            principal, request.params["organization_id"], Permission.POLICIES_READ
        )
        return ok(
            self._policies.view(
                organization, can_write=principal.can(Permission.POLICIES_WRITE, organization.id)
            )
        )

    def preview_policy(self, request: Request) -> Response:
        principal = self._principal(request)
        organization = self._organization(
            principal, request.params["organization_id"], Permission.POLICIES_READ
        )
        floors = validate_floors(request.json().get("floors"))
        current = self._policies.current(organization.id)
        changes = policy_changes(current.floors, floors)
        return ok(
            {
                "version": current.version,
                "changes": changes,
                "weakening": any(c.weakening for c in changes),
            }
        )

    def put_policy(self, request: Request) -> Response:
        principal = self._principal(request)
        organization = self._organization(
            principal, request.params["organization_id"], Permission.POLICIES_WRITE
        )
        body = request.json()
        expected = body.get("expected_version")
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
            raise bad_request("expected_version must be a non-negative integer", "expected_version")
        reason = body.get("reason")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 500):
            raise bad_request("reason must be text of at most 500 characters", "reason")
        confirm = body.get("confirm_weakening", False)
        _, changes = self._policies.update(
            account_id=organization.id,
            actor=Actor.user(principal.user_id, principal.login),
            authenticated_at=principal.authenticated_at,
            expected_version=expected,
            floors=validate_floors(body.get("floors")),
            reason=reason,
            confirm_weakening=self._bool(confirm, "confirm_weakening"),
        )
        view = self._policies.view(organization, can_write=True)
        return ok(view, {"changes": [c.model_dump(mode="json") for c in changes]})

    def policy_versions(self, request: Request) -> Response:
        principal = self._principal(request)
        organization = self._organization(
            principal, request.params["organization_id"], Permission.POLICIES_READ
        )
        offset = offset_cursor(request.arg("cursor"))
        limit = parse_limit(request.arg("limit"))
        versions = self._policies.versions(organization.id, offset=offset, limit=limit + 1)
        next_cursor = encode_cursor([offset + limit]) if len(versions) > limit else None
        return ok(versions[:limit], _page_meta(next_cursor, limit))

    def policy_version(self, request: Request) -> Response:
        principal = self._principal(request)
        organization = self._organization(
            principal, request.params["organization_id"], Permission.POLICIES_READ
        )
        version = self._policies.version(organization.id, request.params["version"])
        if version is None:
            raise ApiError(404, "NOT_FOUND", NOT_FOUND)
        return ok(self._policies.version_view(version))

    def list_rules(self, request: Request) -> Response:
        return ok(list_rules())

    def get_rule(self, request: Request) -> Response:
        detail = rule_detail(request.params["rule_id"])
        if detail is None:
            raise ApiError(404, "NOT_FOUND", NOT_FOUND)
        return ok(detail)

    # ------------------------------------------------------------------ #
    # Audit
    # ------------------------------------------------------------------ #
    def list_audit(self, request: Request) -> Response:
        filters = AuditFilters(
            organization_id=parse_int_id(request.arg("organization"), "organization"),
            repository_id=parse_int_id(request.arg("repository"), "repository"),
            event_type=parse_choice(
                request.arg("type"), {t.value: t for t in AuditEventType}, "type"
            ),
            actor=parse_search(request.arg("actor")),
            start=parse_timestamp(request.arg("from"), "from"),
            end=parse_timestamp(request.arg("to"), "to"),
            sort=parse_choice(request.arg("sort"), {s: s for s in AUDIT_SORTS}, "sort") or "newest",
        )
        limit = parse_limit(request.arg("limit"))
        page = self._queries.list_audit(
            self._scope(request), filters, cursor=request.arg("cursor"), limit=limit
        )
        return ok(page.items, _page_meta(page.next_cursor, page.limit))

    def get_audit_event(self, request: Request) -> Response:
        event = self._queries.get_audit_event(self._scope(request), request.params["event_id"])
        if event is None:
            raise ApiError(404, "NOT_FOUND", NOT_FOUND)
        return ok(event)

    # ------------------------------------------------------------------ #
    # GitHub
    # ------------------------------------------------------------------ #
    def list_installations(self, request: Request) -> Response:
        return ok(self._queries.list_installations(self._scope(request), self._principal(request)))

    def get_installation(self, request: Request) -> Response:
        detail = self._queries.get_installation(
            self._scope(request), request.params["installation_id"], self._principal(request)
        )
        if detail is None:
            raise ApiError(404, "NOT_FOUND", NOT_FOUND)
        return ok(detail)

    def installation_repositories(self, request: Request) -> Response:
        limit = parse_limit(request.arg("limit"))
        page = self._queries.installation_repositories(
            self._scope(request),
            request.params["installation_id"],
            offset=offset_cursor(request.arg("cursor")),
            limit=limit,
        )
        if page is None:
            raise ApiError(404, "NOT_FOUND", NOT_FOUND)
        return ok(page.items, _page_meta(page.next_cursor, page.limit))

    def sync_installation(self, request: Request) -> Response:
        result = self._commands.sync_installation(
            self._principal(request), request.params["installation_id"]
        )
        return ok(result)

    # ------------------------------------------------------------------ #
    # Route table
    # ------------------------------------------------------------------ #
    def _build_routes(self) -> tuple[Route, ...]:
        p = Permission
        table: list[
            tuple[str, str, Callable[[Request], Response], Permission | None, bool, str]
        ] = [
            ("GET", "/auth/login", self.login, None, True, "auth"),
            ("GET", "/auth/callback", self.callback, None, True, "auth"),
            ("GET", "/auth/session", self.session, None, False, "read"),
            ("POST", "/auth/logout", self.logout, None, False, "write"),
            ("GET", "/auth/sessions", self.sessions, None, False, "read"),
            (
                "DELETE",
                "/auth/sessions/{session_id:session}",
                self.revoke_session,
                None,
                False,
                "write",
            ),
            ("GET", "/dashboard/overview", self.overview, p.REPOSITORIES_READ, False, "read"),
            ("GET", "/organizations", self.organizations, None, False, "read"),
            (
                "GET",
                "/organizations/{organization_id:int}/members",
                self.list_members,
                None,
                False,
                "read",
            ),
            (
                "PUT",
                "/organizations/{organization_id:int}/members/{user_id:int}",
                self.put_member,
                None,
                False,
                "write",
            ),
            (
                "DELETE",
                "/organizations/{organization_id:int}/members/{user_id:int}",
                self.delete_member,
                None,
                False,
                "write",
            ),
            ("GET", "/repositories", self.list_repositories, p.REPOSITORIES_READ, False, "read"),
            (
                "GET",
                "/repositories/{repository_id:int}",
                self.get_repository,
                p.REPOSITORIES_READ,
                False,
                "read",
            ),
            (
                "PUT",
                "/repositories/{repository_id:int}/monitoring",
                self.put_monitoring,
                p.REPOSITORIES_READ,
                False,
                "write",
            ),
            (
                "POST",
                "/repositories/{repository_id:int}/enforcement/refresh",
                self.refresh_enforcement,
                p.REPOSITORIES_READ,
                False,
                "github",
            ),
            ("GET", "/scans", self.list_scans, p.SCANS_READ, False, "read"),
            ("GET", "/scans/{scan_id:hex}", self.get_scan, p.SCANS_READ, False, "read"),
            (
                "GET",
                "/scans/{scan_id:hex}/comparison",
                self.compare_scan,
                p.SCANS_READ,
                False,
                "read",
            ),
            ("POST", "/scans/{scan_id:hex}/rescan", self.rescan, p.SCANS_READ, False, "github"),
            ("GET", "/violations", self.list_violations, p.VIOLATIONS_READ, False, "read"),
            (
                "GET",
                "/violations/{violation_id:hex}",
                self.get_violation,
                p.VIOLATIONS_READ,
                False,
                "read",
            ),
            (
                "PUT",
                "/violations/{violation_id:hex}/acknowledgement",
                self.acknowledge,
                p.VIOLATIONS_READ,
                False,
                "write",
            ),
            (
                "DELETE",
                "/violations/{violation_id:hex}/acknowledgement",
                self.unacknowledge,
                p.VIOLATIONS_READ,
                False,
                "write",
            ),
            ("GET", "/policies", self.list_policies, None, False, "read"),
            ("GET", "/policies/{organization_id:int}", self.get_policy, None, False, "read"),
            ("PUT", "/policies/{organization_id:int}", self.put_policy, None, False, "write"),
            (
                "POST",
                "/policies/{organization_id:int}/preview",
                self.preview_policy,
                None,
                False,
                "read",
            ),
            (
                "GET",
                "/policies/{organization_id:int}/versions",
                self.policy_versions,
                None,
                False,
                "read",
            ),
            (
                "GET",
                "/policies/{organization_id:int}/versions/{version:version}",
                self.policy_version,
                None,
                False,
                "read",
            ),
            ("GET", "/rules", self.list_rules, None, False, "read"),
            ("GET", "/rules/{rule_id:ident}", self.get_rule, None, False, "read"),
            ("GET", "/audit", self.list_audit, p.AUDIT_READ, False, "read"),
            ("GET", "/audit/{event_id:hex}", self.get_audit_event, p.AUDIT_READ, False, "read"),
            (
                "GET",
                "/github/installations",
                self.list_installations,
                p.REPOSITORIES_READ,
                False,
                "read",
            ),
            (
                "GET",
                "/github/installations/{installation_id:int}",
                self.get_installation,
                p.REPOSITORIES_READ,
                False,
                "read",
            ),
            (
                "GET",
                "/github/installations/{installation_id:int}/repositories",
                self.installation_repositories,
                p.REPOSITORIES_READ,
                False,
                "read",
            ),
            (
                "POST",
                "/github/installations/{installation_id:int}/sync",
                self.sync_installation,
                p.REPOSITORIES_READ,
                False,
                "github",
            ),
        ]
        routes = []
        for method, template, handler, permission, public, rate in table:
            full = API_PREFIX + template
            pattern, converters = _compile(full)
            routes.append(
                Route(method, full, handler, permission, public, rate, pattern, converters)
            )
        return tuple(routes)

    @property
    def routes(self) -> tuple[Route, ...]:
        return self._routes
