"""A small GitHub REST API client for the CommitGuard GitHub App.

Only the operations CommitGuard needs exist; there is no generic "call any
endpoint" method. Commit metadata is read from Git objects (see
:mod:`commitguard.github.repositories`), not from the API, so the App analyses
exactly the bytes the GitHub Action and the Git hooks analyse.

Security properties:

* HTTPS only, to a fixed API base URL; URLs are built from constant route
  templates and validated, percent-encoded path segments - webhook data can
  never choose a host (no SSRF);
* redirects are never followed (a redirect could forward the ``Authorization``
  header elsewhere); only the HTTPS handler is installed (no ``file:``/``ftp:``);
* pagination links must point back to the API base URL;
* responses are size-limited and parsed as JSON data only;
* credentials are :class:`~commitguard.security.secrets.Secret` values, sent only
  in the ``Authorization`` header and never included in errors or logs;
* bounded retries: rate limits wait for ``Retry-After``/``X-RateLimit-Reset``
  up to a cap, 5xx and network errors back off exponentially, and only
  idempotent requests are retried after a server or network error.
"""

import base64
import json
import random
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from http.client import HTTPException
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urlsplit

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from commitguard import __version__
from commitguard.github.errors import (
    GitHubAPIError,
    GitHubConflictError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubServerError,
    GitHubUnauthorizedError,
    GitHubUnavailableError,
    GitHubValidationError,
)
from commitguard.github.identifiers import (
    MAX_GITHUB_ID,
    AccountType,
    RepositoryRef,
    validate_login,
    validate_repository_name,
)
from commitguard.observability.logging import get_logger
from commitguard.observability.metrics import (
    GITHUB_API_ERRORS,
    GITHUB_RATE_LIMITS,
    Metrics,
    NullMetrics,
)
from commitguard.security.secrets import Secret, register_secret
from commitguard.security.validation import validate_git_sha

API_URL = "https://api.github.com"
WEB_URL = "https://github.com"
API_VERSION = "2022-11-28"
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_PAGES = 100
PER_PAGE = 100
MAX_CONTENT_BYTES = 512 * 1024

log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None = None

    def __repr__(self) -> str:  # headers carry the Authorization credential
        return f"HttpRequest({self.method} {self.url})"


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)  # lower-case names
    body: bytes = b""


class TransportError(Exception):
    """No HTTP response was received (network failure, TLS error, timeout)."""

    def __init__(self, reason: str, *, timeout: bool = False) -> None:
        self.timeout = timeout
        super().__init__(reason)


class Transport(Protocol):
    def send(self, request: HttpRequest, *, timeout: float) -> HttpResponse: ...


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # urllib then raises HTTPError carrying the 3xx status


class UrllibTransport:
    """Standard-library HTTPS transport (certificate verification on)."""

    def __init__(self) -> None:
        context = ssl.create_default_context()
        opener = urllib.request.OpenerDirector()
        for handler in (
            urllib.request.ProxyHandler(),  # honours HTTPS_PROXY set by the operator
            urllib.request.UnknownHandler(),
            urllib.request.HTTPSHandler(context=context),
            urllib.request.HTTPDefaultErrorHandler(),
            _RefuseRedirects(),
            urllib.request.HTTPErrorProcessor(),
        ):
            opener.add_handler(handler)
        self._opener = opener

    def send(self, request: HttpRequest, *, timeout: float) -> HttpResponse:
        if urlsplit(request.url).scheme != "https":
            raise TransportError("refusing non-HTTPS request")
        req = urllib.request.Request(  # noqa: S310 - scheme checked above, https handler only
            request.url, data=request.body, method=request.method, headers=dict(request.headers)
        )
        try:
            with self._opener.open(req, timeout=timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                status = int(response.status)
                headers = {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            body = exc.read(MAX_RESPONSE_BYTES + 1) if exc.fp is not None else b""
            status = int(exc.code)
            headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
        except TimeoutError:
            raise TransportError("request timed out", timeout=True) from None
        except (urllib.error.URLError, ssl.SSLError, HTTPException, OSError) as exc:
            raise TransportError(f"network error ({type(exc).__name__})") from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise TransportError("response too large")
        return HttpResponse(status=status, headers=headers, body=body)


# --------------------------------------------------------------------------- #
# Response models (only the fields CommitGuard uses)
# --------------------------------------------------------------------------- #
class _Loose(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


def _permissions(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(k)[:64]: str(v)[:16]
        for k, v in value.items()
        if isinstance(k, str) and isinstance(v, str)
    }


class AccountInfo(_Loose):
    id: int
    login: str
    type: AccountType

    @field_validator("login")
    @classmethod
    def _login(cls, value: str) -> str:
        return validate_login(value)


class AppInfo(_Loose):
    id: int
    slug: str
    name: str
    permissions: dict[str, str] = {}
    events: tuple[str, ...] = ()

    @field_validator("permissions", mode="before")
    @classmethod
    def _perms(cls, value: object) -> dict[str, str]:
        return _permissions(value)


class InstallationInfo(_Loose):
    id: int
    account: AccountInfo
    repository_selection: str = "selected"
    permissions: dict[str, str] = {}
    suspended_at: str | None = None

    @field_validator("permissions", mode="before")
    @classmethod
    def _perms(cls, value: object) -> dict[str, str]:
        return _permissions(value)


class RepositoryInfo(_Loose):
    id: int
    name: str
    owner: AccountInfo
    default_branch: str | None = None
    private: bool = True
    archived: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return validate_repository_name(value)

    @property
    def ref(self) -> RepositoryRef:
        return RepositoryRef(id=self.id, owner=self.owner.login, name=self.name)


class _PRRepo(_Loose):
    id: int


class _PRSide(_Loose):
    sha: str
    ref: str | None = None
    repo: _PRRepo | None = None

    @field_validator("sha")
    @classmethod
    def _sha(cls, value: str) -> str:
        return validate_git_sha(value)


class PullRequestInfo(_Loose):
    number: int
    state: str
    merged: bool | None = None
    head: _PRSide
    base: _PRSide


class CheckRunInfo(_Loose):
    id: int
    head_sha: str
    status: str

    @field_validator("id")
    @classmethod
    def _id(cls, value: int) -> int:
        if not 0 < value < MAX_GITHUB_ID:
            raise ValueError("invalid check run id")
        return value


class UserInfo(_Loose):
    id: int
    login: str

    @field_validator("id")
    @classmethod
    def _id(cls, value: int) -> int:
        if not 0 < value < MAX_GITHUB_ID:
            raise ValueError("invalid user id")
        return value

    @field_validator("login")
    @classmethod
    def _login(cls, value: str) -> str:
        return validate_login(value)


class _StatusChecks(_Loose):
    contexts: tuple[str, ...] = ()


class _BranchProtectionSummary(_Loose):
    enabled: bool | None = None
    required_status_checks: _StatusChecks | None = None


class BranchInfo(_Loose):
    name: str
    protected: bool
    protection: _BranchProtectionSummary | None = None

    @property
    def required_contexts(self) -> tuple[str, ...]:
        checks = self.protection.required_status_checks if self.protection else None
        return checks.contexts if checks else ()


class BranchRule(_Loose):
    type: str
    parameters: dict[str, Any] | None = None

    @property
    def required_contexts(self) -> tuple[str, ...]:
        if self.type != "required_status_checks" or not self.parameters:
            return ()
        checks = self.parameters.get("required_status_checks")
        if not isinstance(checks, list):
            return ()
        return tuple(
            str(c["context"])[:200]
            for c in checks
            if isinstance(c, Mapping) and isinstance(c.get("context"), str)
        )


class ContentEntry(_Loose):
    name: str
    path: str
    type: str
    size: int = 0


@dataclass(frozen=True, slots=True)
class OAuthGrant:
    access_token: Secret
    expires_in: int | None


@dataclass(frozen=True, slots=True)
class InstallationTokenGrant:
    token: Secret
    expires_at: datetime
    permissions: Mapping[str, str]
    repository_ids: tuple[int, ...]


class _TokenResponse(_Loose):
    token: str
    expires_at: datetime
    permissions: dict[str, str] = {}
    repositories: tuple[_PRRepo, ...] = ()

    @field_validator("permissions", mode="before")
    @classmethod
    def _perms(cls, value: object) -> dict[str, str]:
        return _permissions(value)


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 16.0
    max_rate_limit_wait: float = 60.0  # longer waits fail instead of holding a worker


_ERRORS: dict[int, type[GitHubAPIError]] = {
    401: GitHubUnauthorizedError,
    403: GitHubForbiddenError,
    404: GitHubNotFoundError,
    409: GitHubConflictError,
    422: GitHubValidationError,
}


def _validate_api_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("GitHub API URL must be an https URL without credentials or query")
    return url.rstrip("/")


def _segment(value: str) -> str:
    return quote(value, safe="")


class GitHubClient:
    def __init__(
        self,
        transport: Transport | None = None,
        *,
        api_url: str = API_URL,
        web_url: str = WEB_URL,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        jitter: Callable[[float], float] | None = None,
        metrics: Metrics | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport or UrllibTransport()
        self._api_url = _validate_api_url(api_url)
        self._web_url = _validate_api_url(web_url)
        self._retry = retry or RetryPolicy()
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter or (lambda delay: delay * random.uniform(0.5, 1.0))  # noqa: S311
        self._metrics = metrics or NullMetrics()
        self._timeout = timeout

    # -- plumbing ------------------------------------------------------- #
    def _headers(self, credential: Secret, has_body: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {credential.reveal()}",
            "User-Agent": f"CommitGuard-App/{__version__}",
            "X-GitHub-Api-Version": API_VERSION,
        }
        if has_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _backoff(self, attempt: int) -> float:
        delay = min(self._retry.max_delay, self._retry.base_delay * (2 ** (attempt - 1)))
        return max(0.0, self._jitter(delay))

    def _rate_limit_wait(self, response: HttpResponse) -> float | None:
        headers = response.headers
        limited = response.status == 429 or (
            response.status == 403
            and (
                headers.get("x-ratelimit-remaining") == "0"
                or "retry-after" in headers
                or b"rate limit" in response.body[:2000].lower()
            )
        )
        if not limited:
            return None
        retry_after = headers.get("retry-after", "")
        if retry_after.isascii() and retry_after.isdigit():
            return float(retry_after)
        reset = headers.get("x-ratelimit-reset", "")
        if headers.get("x-ratelimit-remaining") == "0" and reset.isascii() and reset.isdigit():
            return max(0.0, float(reset) - self._clock())
        return 60.0  # secondary rate limit without guidance: GitHub asks for at least a minute

    @staticmethod
    def _detail(response: HttpResponse) -> str:
        try:
            document = json.loads(response.body[:65536].decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return ""
        message = document.get("message") if isinstance(document, dict) else None
        return message if isinstance(message, str) else ""

    def _send(
        self,
        operation: str,
        method: str,
        url: str,
        credential: Secret,
        *,
        payload: Mapping[str, Any] | None = None,
        idempotent: bool = True,
    ) -> HttpResponse:
        body = (
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
            if payload is not None
            else None
        )
        request = HttpRequest(method, url, self._headers(credential, body is not None), body)
        attempts = max(1, self._retry.max_attempts)
        for attempt in range(1, attempts + 1):
            try:
                response = self._transport.send(request, timeout=self._timeout)
            except TransportError as exc:
                self._metrics.increment(GITHUB_API_ERRORS, category="unavailable")
                log.warning("github_api_unavailable", operation=operation, attempt=attempt)
                if idempotent and attempt < attempts:
                    self._sleep(self._backoff(attempt))
                    continue
                raise GitHubUnavailableError(
                    operation, detail="request timed out" if exc.timeout else "network error"
                ) from None
            if 200 <= response.status < 300:
                return response
            request_id = response.headers.get("x-github-request-id")
            wait = self._rate_limit_wait(response)
            if wait is not None:
                self._metrics.increment(GITHUB_RATE_LIMITS)
                log.warning(
                    "github_rate_limited", operation=operation, attempt=attempt, wait_seconds=wait
                )
                if attempt < attempts and wait <= self._retry.max_rate_limit_wait:
                    self._sleep(max(wait, 1.0))
                    continue
                raise GitHubRateLimitError(
                    operation, retry_after=wait, status=response.status, request_id=request_id
                )
            self._metrics.increment(GITHUB_API_ERRORS, category=str(response.status))
            if response.status >= 500:
                log.warning(
                    "github_api_server_error",
                    operation=operation,
                    attempt=attempt,
                    status=response.status,
                )
                if idempotent and attempt < attempts:
                    self._sleep(self._backoff(attempt))
                    continue
                raise GitHubServerError(
                    operation,
                    status=response.status,
                    detail=self._detail(response),
                    request_id=request_id,
                )
            error = _ERRORS.get(response.status, GitHubAPIError)
            raise error(
                operation,
                status=response.status,
                detail=self._detail(response)
                or ("unexpected redirect" if 300 <= response.status < 400 else ""),
                request_id=request_id,
            )
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _json(operation: str, response: HttpResponse) -> Any:
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise GitHubAPIError(
                operation, status=response.status, detail="malformed JSON response"
            ) from None

    def _model[M: BaseModel](self, operation: str, response: HttpResponse, model: type[M]) -> M:
        document = self._json(operation, response)
        try:
            return model.model_validate(document)
        except (ValidationError, ValueError):
            raise GitHubAPIError(
                operation, status=response.status, detail="unexpected response shape"
            ) from None

    def _url(self, path: str, query: Mapping[str, str | int] | None = None) -> str:
        return self._api_url + path + (f"?{urlencode(query)}" if query else "")

    def _paginate(
        self,
        operation: str,
        path: str,
        credential: Secret,
        *,
        key: str | None = None,
        max_pages: int | None = None,
    ) -> Iterator[Any]:
        limit = MAX_PAGES if max_pages is None else max_pages
        url: str | None = self._url(path, {"per_page": PER_PAGE})
        pages = 0
        while url is not None:
            pages += 1
            if pages > limit:
                raise GitHubAPIError(operation, detail=f"more than {limit} pages")
            response = self._send(operation, "GET", url, credential)
            document = self._json(operation, response)
            items = document.get(key) if key and isinstance(document, dict) else document
            if not isinstance(items, list):
                raise GitHubAPIError(operation, detail="unexpected response shape")
            yield from items
            url = self._next_link(operation, response.headers.get("link", ""))

    def _next_link(self, operation: str, header: str) -> str | None:
        for part in header.split(","):
            section = part.strip()
            if not section.endswith('rel="next"'):
                continue
            target = section.split(";", 1)[0].strip()
            if not (target.startswith("<") and target.endswith(">")):
                raise GitHubAPIError(operation, detail="malformed pagination link")
            link = target[1:-1]
            if not link.startswith(self._api_url + "/") or urlsplit(link).fragment:
                raise GitHubAPIError(operation, detail="pagination link to an unexpected location")
            return link
        return None

    # -- App (JWT) operations ------------------------------------------- #
    def get_app(self, jwt: Secret) -> AppInfo:
        op = "GET /app"
        return self._model(op, self._send(op, "GET", self._url("/app"), jwt), AppInfo)

    def list_app_installations(self, jwt: Secret) -> list[InstallationInfo]:
        op = "GET /app/installations"
        items = self._paginate(op, "/app/installations", jwt)
        try:
            return [InstallationInfo.model_validate(item) for item in items]
        except ValidationError:
            raise GitHubAPIError(op, detail="unexpected response shape") from None

    def get_installation(self, jwt: Secret, installation_id: int) -> InstallationInfo:
        op = "GET /app/installations/{installation_id}"
        url = self._url(f"/app/installations/{int(installation_id)}")
        return self._model(op, self._send(op, "GET", url, jwt), InstallationInfo)

    def create_installation_token(
        self,
        jwt: Secret,
        installation_id: int,
        *,
        repository_ids: Sequence[int] | None,
        permissions: Mapping[str, str],
    ) -> InstallationTokenGrant:
        op = "POST /app/installations/{installation_id}/access_tokens"
        payload: dict[str, Any] = {"permissions": dict(permissions)}
        if repository_ids is not None:
            payload["repository_ids"] = [int(r) for r in repository_ids]
        url = self._url(f"/app/installations/{int(installation_id)}/access_tokens")
        # Minting a token has no side effect beyond the token itself: safe to retry.
        response = self._send(op, "POST", url, jwt, payload=payload, idempotent=True)
        parsed = self._model(op, response, _TokenResponse)
        if not parsed.token or len(parsed.token) > 1024:
            raise GitHubAPIError(op, status=response.status, detail="invalid token response")
        token = Secret(parsed.token)
        register_secret(token)
        return InstallationTokenGrant(
            token=token,
            expires_at=parsed.expires_at,
            permissions=parsed.permissions,
            repository_ids=tuple(r.id for r in parsed.repositories),
        )

    # -- installation token operations ---------------------------------- #
    def list_installation_repositories(self, token: Secret) -> list[RepositoryInfo]:
        op = "GET /installation/repositories"
        items = self._paginate(op, "/installation/repositories", token, key="repositories")
        try:
            return [RepositoryInfo.model_validate(item) for item in items]
        except (ValidationError, ValueError):
            raise GitHubAPIError(op, detail="unexpected response shape") from None

    def get_repository(self, token: Secret, repository_id: int) -> RepositoryInfo:
        """Look a repository up by immutable ID (renames cannot redirect the lookup)."""
        op = "GET /repositories/{repository_id}"
        url = self._url(f"/repositories/{int(repository_id)}")
        return self._model(op, self._send(op, "GET", url, token), RepositoryInfo)

    def _repo_path(self, repository: RepositoryRef) -> str:
        return f"/repos/{_segment(repository.owner)}/{_segment(repository.name)}"

    def get_pull_request(
        self, token: Secret, repository: RepositoryRef, number: int
    ) -> PullRequestInfo:
        op = "GET /repos/{owner}/{repo}/pulls/{pull_number}"
        url = self._url(f"{self._repo_path(repository)}/pulls/{int(number)}")
        return self._model(op, self._send(op, "GET", url, token), PullRequestInfo)

    def create_check_run(
        self, token: Secret, repository: RepositoryRef, payload: Mapping[str, Any]
    ) -> CheckRunInfo:
        op = "POST /repos/{owner}/{repo}/check-runs"
        url = self._url(f"{self._repo_path(repository)}/check-runs")
        # Not retried after a 5xx/network error: a retry could create a duplicate run.
        response = self._send(op, "POST", url, token, payload=payload, idempotent=False)
        return self._model(op, response, CheckRunInfo)

    def update_check_run(
        self,
        token: Secret,
        repository: RepositoryRef,
        check_run_id: int,
        payload: Mapping[str, Any],
    ) -> CheckRunInfo:
        op = "PATCH /repos/{owner}/{repo}/check-runs/{check_run_id}"
        url = self._url(f"{self._repo_path(repository)}/check-runs/{int(check_run_id)}")
        response = self._send(op, "PATCH", url, token, payload=payload, idempotent=True)
        return self._model(op, response, CheckRunInfo)

    # -- enforcement evidence (installation token) ----------------------- #
    def get_branch(self, token: Secret, repository: RepositoryRef, branch: str) -> BranchInfo:
        op = "GET /repos/{owner}/{repo}/branches/{branch}"
        url = self._url(f"{self._repo_path(repository)}/branches/{_segment(branch)}")
        return self._model(op, self._send(op, "GET", url, token), BranchInfo)

    def get_branch_rules(
        self, token: Secret, repository: RepositoryRef, branch: str
    ) -> list[BranchRule]:
        """Active ruleset rules for a branch (``GET /repos/{o}/{r}/rules/branches/{b}``)."""
        op = "GET /repos/{owner}/{repo}/rules/branches/{branch}"
        items = self._paginate(
            op, f"{self._repo_path(repository)}/rules/branches/{_segment(branch)}", token
        )
        try:
            return [BranchRule.model_validate(item) for item in items]
        except (ValidationError, ValueError):
            raise GitHubAPIError(op, detail="unexpected response shape") from None

    def list_directory(
        self, token: Secret, repository: RepositoryRef, path: str, ref: str
    ) -> list[ContentEntry]:
        op = "GET /repos/{owner}/{repo}/contents/{path}"
        encoded = "/".join(_segment(part) for part in path.split("/"))
        url = self._url(f"{self._repo_path(repository)}/contents/{encoded}", {"ref": ref})
        document = self._json(op, self._send(op, "GET", url, token))
        if not isinstance(document, list):
            return []  # a file, not a directory
        try:
            return [ContentEntry.model_validate(item) for item in document[:200]]
        except (ValidationError, ValueError):
            raise GitHubAPIError(op, detail="unexpected response shape") from None

    def get_file_text(
        self, token: Secret, repository: RepositoryRef, path: str, ref: str
    ) -> str | None:
        """A small UTF-8 file's contents, or None when it is too large or not text."""
        op = "GET /repos/{owner}/{repo}/contents/{path}"
        encoded = "/".join(_segment(part) for part in path.split("/"))
        url = self._url(f"{self._repo_path(repository)}/contents/{encoded}", {"ref": ref})
        document = self._json(op, self._send(op, "GET", url, token))
        if not isinstance(document, dict) or document.get("encoding") != "base64":
            return None
        content = document.get("content")
        size = document.get("size")
        if not isinstance(content, str) or not isinstance(size, int) or size > MAX_CONTENT_BYTES:
            return None
        try:
            return base64.b64decode(content, validate=False).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None

    # -- user authorization (OAuth web flow of the GitHub App) ----------- #
    def authorize_url(
        self, *, client_id: str, redirect_uri: str, state: str, code_challenge: str
    ) -> str:
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "allow_signup": "false",
            }
        )
        return f"{self._web_url}/login/oauth/authorize?{query}"

    def exchange_oauth_code(
        self,
        *,
        client_id: str,
        client_secret: Secret,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OAuthGrant:
        """Exchange an authorization code for a user access token (never retried)."""
        op = "POST /login/oauth/access_token"
        body = urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret.reveal(),
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            }
        ).encode("ascii")
        request = HttpRequest(
            "POST",
            f"{self._web_url}/login/oauth/access_token",
            {
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": f"CommitGuard-App/{__version__}",
            },
            body,
        )
        try:
            response = self._transport.send(request, timeout=self._timeout)
        except TransportError as exc:
            raise GitHubUnavailableError(
                op, detail="request timed out" if exc.timeout else "network error"
            ) from None
        if response.status != 200:
            self._metrics.increment(GITHUB_API_ERRORS, category=str(response.status))
            raise GitHubAPIError(op, status=response.status, detail="token exchange failed")
        document = self._json(op, response)
        if not isinstance(document, dict) or "error" in document:
            raise GitHubUnauthorizedError(op, status=401, detail="authorization code rejected")
        token = document.get("access_token")
        if not isinstance(token, str) or not token or len(token) > 1024:
            raise GitHubAPIError(op, status=response.status, detail="invalid token response")
        expires = document.get("expires_in")
        grant = OAuthGrant(
            access_token=Secret(token),
            expires_in=expires if isinstance(expires, int) else None,
        )
        register_secret(grant.access_token)
        return grant

    def get_authenticated_user(self, user_token: Secret) -> UserInfo:
        op = "GET /user"
        return self._model(op, self._send(op, "GET", self._url("/user"), user_token), UserInfo)

    def list_user_installations(self, user_token: Secret) -> list[InstallationInfo]:
        """Installations of this App the signed-in user can access."""
        op = "GET /user/installations"
        items = self._paginate(op, "/user/installations", user_token, key="installations")
        try:
            return [InstallationInfo.model_validate(item) for item in items]
        except (ValidationError, ValueError):
            raise GitHubAPIError(op, detail="unexpected response shape") from None

    def list_user_installation_repository_ids(
        self, user_token: Secret, installation_id: int
    ) -> list[int]:
        """IDs of repositories in an installation that the signed-in user can access."""
        op = "GET /user/installations/{installation_id}/repositories"
        items = self._paginate(
            op,
            f"/user/installations/{int(installation_id)}/repositories",
            user_token,
            key="repositories",
        )
        ids: list[int] = []
        for item in items:
            if isinstance(item, Mapping) and isinstance(item.get("id"), int):
                if 0 < item["id"] < MAX_GITHUB_ID:
                    ids.append(item["id"])
        return ids
