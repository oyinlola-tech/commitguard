"""Dashboard sign-in and sessions.

Authentication uses the GitHub App's own user authorization (the OAuth web
flow of a GitHub App). No passwords, personal access tokens or third-party
identity providers are involved.

::

    browser -> GET /api/v1/auth/login
               state (random, bound to the browser by an HttpOnly cookie)
               PKCE verifier (kept server side), return path (validated)
            -> github.com/login/oauth/authorize  -> user approves
            -> GET /api/v1/auth/callback?code&state
               state must match the cookie and an unexpired, unused record
               code + verifier -> user access token (used immediately, never stored)
               GET /user                                    who signed in
               GET /user/installations                      installations they can access
               GET /user/installations/{id}/repositories    repositories they can access
               -> session row + the installation/repository lists, token discarded

Sessions
========

* The session token is 256 bits from :mod:`secrets`, sent only in a
  ``__Host-`` cookie (``HttpOnly``, ``Secure``, ``SameSite=Lax``, ``Path=/``).
  The database stores its SHA-256 hash, so a copy of the database cannot be
  replayed as a session.
* Sessions end after :data:`SESSION_LIFETIME` (the lifetime of a GitHub user
  token) or :data:`SESSION_IDLE_TIMEOUT` without use, on sign-out, or when
  revoked from the settings page. Changes to GitHub access take effect at the
  next sign-in; role changes in CommitGuard take effect on the next request.
* The CSRF token is derived from the session token (``SHA-256("csrf" || token)``):
  JavaScript receives it from ``GET /api/v1/auth/session`` and sends it in
  ``X-CSRF-Token``; an attacker's page can read neither.
"""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from commitguard.audit.models import Actor, AuditEvent, AuditEventType
from commitguard.controlplane.access import Membership, Principal, Role
from commitguard.controlplane.errors import (
    ControlPlaneError,
    InputValidationError,
    NotFoundError,
    UpstreamUnavailableError,
)
from commitguard.controlplane.results import clean_text
from commitguard.controlplane.views import SessionView
from commitguard.github.client import GitHubClient
from commitguard.github.errors import GitHubAPIError, GitHubUnauthorizedError
from commitguard.github.identifiers import AccountType
from commitguard.github.storage import InstallationRecord, InstallationState, SqliteStateStore
from commitguard.observability.logging import get_logger
from commitguard.security.secrets import Secret, default_redactor
from commitguard.services.audit import AuditService

log = get_logger(__name__)

SESSION_LIFETIME = timedelta(hours=8)
SESSION_IDLE_TIMEOUT = timedelta(hours=2)
OAUTH_STATE_LIFETIME = timedelta(minutes=10)
LAST_SEEN_RESOLUTION = timedelta(minutes=1)
MAX_REPOSITORIES_PER_INSTALLATION = 10_000
MAX_USER_AGENT_CHARS = 200
MAX_RETURN_PATH_CHARS = 512
SESSION_TOKEN_BYTES = 32


class SessionExpiredError(ControlPlaneError):
    code = "SESSION_EXPIRED"
    status = 401

    def __init__(self) -> None:
        super().__init__("Your session has expired. Sign in again.")


class SignInError(ControlPlaneError):
    code = "SIGN_IN_FAILED"
    status = 400


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _ts(value: datetime) -> float:
    return value.timestamp()


def _dt(value: float) -> datetime:
    return datetime.fromtimestamp(value, UTC)


def safe_return_path(value: str | None) -> str:
    """A same-origin path to return to after sign-in (open redirects are impossible)."""
    if not value or len(value) > MAX_RETURN_PATH_CHARS:
        return "/dashboard"
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/dashboard"
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in value):
        return "/dashboard"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or value.startswith("/api/"):
        return "/dashboard"
    return value


def csrf_token_for(session_token: str) -> str:
    return hashlib.sha256(b"commitguard-csrf\x00" + session_token.encode("ascii")).hexdigest()


def pkce_challenge(verifier: str) -> str:
    import base64

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True)
class SignInStart:
    authorize_url: str
    state: str  # set as the browser-binding cookie


@dataclass(frozen=True, slots=True)
class SignInComplete:
    session_token: Secret
    return_to: str
    principal: Principal


class AuthService:
    def __init__(
        self,
        store: SqliteStateStore,
        client: GitHubClient,
        audit: AuditService,
        *,
        client_id: str,
        client_secret: Secret,
        redirect_uri: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._client = client
        self._audit = audit
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._now = now

    # ------------------------------------------------------------------ #
    # Sign-in
    # ------------------------------------------------------------------ #
    def begin_sign_in(self, return_to: str | None) -> SignInStart:
        now = self._now()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        with self._store.transaction() as db:
            db.execute("DELETE FROM oauth_states WHERE expires_at < ?", (_ts(now),))
            db.execute(
                "INSERT INTO oauth_states (state_hash, verifier, return_to, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    _hash(state),
                    verifier,
                    safe_return_path(return_to),
                    _ts(now + OAUTH_STATE_LIFETIME),
                ),
            )
        url = self._client.authorize_url(
            client_id=self._client_id,
            redirect_uri=self._redirect_uri,
            state=state,
            code_challenge=pkce_challenge(verifier),
        )
        return SignInStart(authorize_url=url, state=state)

    def complete_sign_in(
        self, *, code: str | None, state: str | None, cookie_state: str | None, user_agent: str
    ) -> SignInComplete:
        now = self._now()
        if not code or not state or not cookie_state or len(code) > 512 or len(state) > 128:
            raise SignInError("The sign-in request is incomplete. Start again.")
        if not hmac.compare_digest(state.encode("utf-8"), cookie_state.encode("utf-8")):
            raise SignInError("The sign-in request did not start in this browser. Start again.")
        with self._store.transaction() as db:
            row = db.execute(
                "SELECT verifier, return_to, expires_at FROM oauth_states WHERE state_hash = ?",
                (_hash(state),),
            ).fetchone()
            db.execute("DELETE FROM oauth_states WHERE state_hash = ?", (_hash(state),))
        if row is None or row["expires_at"] < _ts(now):
            raise SignInError("The sign-in request expired or was already used. Start again.")

        try:
            grant = self._client.exchange_oauth_code(
                client_id=self._client_id,
                client_secret=self._client_secret,
                code=code,
                redirect_uri=self._redirect_uri,
                code_verifier=row["verifier"],
            )
        except GitHubUnauthorizedError:
            raise SignInError("GitHub did not accept the sign-in. Start again.") from None
        except GitHubAPIError:
            raise UpstreamUnavailableError("GitHub could not complete the sign-in.") from None
        try:
            user, installations = self._github_access(grant.access_token)
        finally:
            default_redactor().forget(grant.access_token)  # the token is not kept anywhere

        token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        session_hash = _hash(token)
        public_id = secrets.token_hex(8)
        events: list[AuditEvent] = []
        with self._store.transaction() as db:
            db.execute(
                "INSERT INTO users (user_id, login, created_at, last_login_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (user_id) DO UPDATE SET login = excluded.login, "
                "last_login_at = excluded.last_login_at",
                (user[0], user[1], _ts(now), _ts(now)),
            )
            db.execute(
                "INSERT INTO sessions (session_hash, public_id, user_id, created_at, "
                "authenticated_at, last_seen_at, expires_at, user_agent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_hash,
                    public_id,
                    user[0],
                    _ts(now),
                    _ts(now),
                    _ts(now),
                    _ts(now + SESSION_LIFETIME),
                    clean_text(user_agent or "unknown", MAX_USER_AGENT_CHARS),
                ),
            )
            for installation_id, repository_ids in installations.items():
                db.execute(
                    "INSERT INTO session_installations (session_hash, installation_id) VALUES (?, ?)",
                    (session_hash, installation_id),
                )
                db.executemany(
                    "INSERT OR IGNORE INTO session_repositories (session_hash, installation_id, "
                    "repository_id) VALUES (?, ?, ?)",
                    [(session_hash, installation_id, rid) for rid in repository_ids],
                )
        principal = self._principal(session_hash)
        if principal is None:  # pragma: no cover - the rows were written above
            raise SignInError("The session could not be created.")
        actor = Actor.user(principal.user_id, principal.login)
        for account_id in principal.memberships:
            events.append(
                self._audit.build(
                    AuditEventType.USER_SIGNED_IN,
                    actor=actor,
                    account_id=account_id,
                    session=public_id,
                )
            )
        with self._store.transaction() as db:
            events = [self._store.insert_audit_event(db, e) for e in events]
        for event in events:
            self._audit.log_stored(event)
        return SignInComplete(Secret(token), row["return_to"], principal)

    def _github_access(self, user_token: Secret) -> tuple[tuple[int, str], dict[int, list[int]]]:
        try:
            user = self._client.get_authenticated_user(user_token)
            installations = self._client.list_user_installations(user_token)
            access: dict[int, list[int]] = {}
            for info in installations:
                record = self._store.get_installation(info.id)
                if record is None:
                    # GitHub vouches for this installation of the App; remember its account.
                    now = self._now()
                    self._store.upsert_installation(
                        InstallationRecord(
                            installation_id=info.id,
                            account_id=info.account.id,
                            account_login=info.account.login,
                            account_type=AccountType(info.account.type),
                            repository_selection=info.repository_selection,
                            state=InstallationState.SUSPENDED
                            if info.suspended_at
                            else InstallationState.ACTIVE,
                            permissions=info.permissions,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                elif record.account_id != info.account.id:
                    log.warning("installation_account_mismatch", installation_id=info.id)
                    continue
                elif record.state is InstallationState.DELETED:
                    continue
                ids = self._client.list_user_installation_repository_ids(user_token, info.id)
                access[info.id] = ids[:MAX_REPOSITORIES_PER_INSTALLATION]
        except GitHubUnauthorizedError:
            raise SignInError("GitHub did not accept the sign-in. Start again.") from None
        except GitHubAPIError:
            raise UpstreamUnavailableError("GitHub could not list your access.") from None
        return (user.id, user.login), access

    # ------------------------------------------------------------------ #
    # Sessions
    # ------------------------------------------------------------------ #
    def authenticate(self, session_token: str | None) -> Principal | None:
        """The principal for a session cookie, or None. Raises when it expired."""
        if not session_token or len(session_token) > 128 or not session_token.isascii():
            return None
        now = self._now()
        session_hash = _hash(session_token)
        rows = self._store.query(
            "SELECT expires_at, last_seen_at FROM sessions WHERE session_hash = ?", (session_hash,)
        )
        if not rows:
            return None
        row = rows[0]
        if row["expires_at"] <= _ts(now) or row[
            "last_seen_at"
        ] + SESSION_IDLE_TIMEOUT.total_seconds() <= _ts(now):
            with self._store.transaction() as db:
                db.execute("DELETE FROM sessions WHERE session_hash = ?", (session_hash,))
            raise SessionExpiredError()
        if _ts(now) - row["last_seen_at"] >= LAST_SEEN_RESOLUTION.total_seconds():
            with self._store.transaction() as db:
                db.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE session_hash = ?",
                    (_ts(now), session_hash),
                )
        return self._principal(session_hash)

    def _principal(self, session_hash: str) -> Principal | None:
        rows = self._store.query(
            "SELECT s.public_id, s.user_id, s.authenticated_at, s.expires_at, u.login "
            "FROM sessions s JOIN users u ON u.user_id = s.user_id WHERE s.session_hash = ?",
            (session_hash,),
        )
        if not rows:
            return None
        session = rows[0]
        user_id = int(session["user_id"])
        installations = {
            int(r["installation_id"]): int(r["account_id"])
            for r in self._store.query(
                "SELECT si.installation_id, i.account_id FROM session_installations si "
                "JOIN installations i ON i.installation_id = si.installation_id "
                "WHERE si.session_hash = ? AND i.state != 'deleted'",
                (session_hash,),
            )
        }
        memberships: dict[int, Membership] = {}
        accounts = {
            int(r["account_id"]): r
            for r in self._store.query(
                "SELECT account_id, account_login, account_type FROM installations "
                "WHERE installation_id IN (SELECT installation_id FROM session_installations "
                "WHERE session_hash = ?) AND state != 'deleted'",
                (session_hash,),
            )
        }
        for row in self._store.query(
            "SELECT account_id, role FROM memberships WHERE user_id = ?", (user_id,)
        ):
            account = accounts.get(int(row["account_id"]))
            if account is None:
                continue  # GitHub did not report access to this account at sign-in
            memberships[int(row["account_id"])] = Membership(
                account_id=int(row["account_id"]),
                account_login=account["account_login"],
                account_type=account["account_type"],
                role=Role(row["role"]),
            )
        for account_id, account in accounts.items():
            if account["account_type"] == AccountType.USER.value and account_id == user_id:
                memberships[account_id] = Membership(
                    account_id=account_id,
                    account_login=account["account_login"],
                    account_type=account["account_type"],
                    role=Role.OWNER,
                    implicit=True,
                )
        return Principal(
            user_id=user_id,
            login=session["login"],
            session_hash=session_hash,
            session_public_id=session["public_id"],
            authenticated_at=_dt(session["authenticated_at"]),
            expires_at=_dt(session["expires_at"]),
            memberships=memberships,
            installations={i: a for i, a in installations.items() if a in memberships},
        )

    @staticmethod
    def verify_csrf(session_token: str | None, header: str | None) -> bool:
        if not session_token or not header or len(header) > 128:
            return False
        return hmac.compare_digest(csrf_token_for(session_token).encode(), header.encode())

    def sign_out(self, principal: Principal) -> None:
        with self._store.transaction() as db:
            db.execute("DELETE FROM sessions WHERE session_hash = ?", (principal.session_hash,))
            events = [
                self._store.insert_audit_event(
                    db,
                    self._audit.build(
                        AuditEventType.USER_SIGNED_OUT,
                        actor=Actor.user(principal.user_id, principal.login),
                        account_id=account_id,
                        session=principal.session_public_id,
                    ),
                )
                for account_id in principal.memberships
            ]
        for event in events:
            self._audit.log_stored(event)

    def list_sessions(self, principal: Principal) -> list[SessionView]:
        now = _ts(self._now())
        rows = self._store.query(
            "SELECT public_id, created_at, last_seen_at, expires_at, user_agent FROM sessions "
            "WHERE user_id = ? AND expires_at > ? ORDER BY last_seen_at DESC LIMIT 50",
            (principal.user_id, now),
        )
        return [
            SessionView(
                id=r["public_id"],
                created_at=_dt(r["created_at"]),
                last_seen_at=_dt(r["last_seen_at"]),
                expires_at=_dt(r["expires_at"]),
                user_agent=r["user_agent"],
                current=r["public_id"] == principal.session_public_id,
            )
            for r in rows
        ]

    def revoke_session(self, principal: Principal, public_id: str) -> None:
        """Revoke one of the caller's own sessions (another user's session is 'not found')."""
        if not public_id.isascii() or not public_id.isalnum() or len(public_id) > 32:
            raise InputValidationError("invalid session ID")
        with self._store.transaction() as db:
            cursor = db.execute(
                "DELETE FROM sessions WHERE public_id = ? AND user_id = ?",
                (public_id, principal.user_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError()
            events = [
                self._store.insert_audit_event(
                    db,
                    self._audit.build(
                        AuditEventType.SESSION_REVOKED,
                        actor=Actor.user(principal.user_id, principal.login),
                        account_id=account_id,
                        session=public_id,
                    ),
                )
                for account_id in principal.memberships
            ]
        for event in events:
            self._audit.log_stored(event)

    def purge_expired(self) -> int:
        now = _ts(self._now())
        idle = now - SESSION_IDLE_TIMEOUT.total_seconds()
        with self._store.transaction() as db:
            db.execute("DELETE FROM oauth_states WHERE expires_at < ?", (now,))
            return db.execute(
                "DELETE FROM sessions WHERE expires_at <= ? OR last_seen_at <= ?", (now, idle)
            ).rowcount
