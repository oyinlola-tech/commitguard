"""GitHub API client: error normalisation, bounded retries, rate limits, pagination, SSRF."""

import json
from collections.abc import Callable
from typing import Any

import pytest

from commitguard.github.client import (
    GitHubClient,
    HttpRequest,
    HttpResponse,
    RetryPolicy,
    TransportError,
    UrllibTransport,
)
from commitguard.github.errors import (
    AuthenticationError,
    AuthorizationError,
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
from commitguard.github.identifiers import RepositoryRef
from commitguard.observability.metrics import InMemoryMetrics
from commitguard.security.secrets import Secret

TOKEN = Secret("ghs_" + "T" * 36)
REPO = RepositoryRef(id=5001, owner="octo-org", name="project")
REPO_JSON = {
    "id": 5001,
    "name": "project",
    "owner": {"id": 1, "login": "octo-org", "type": "Organization"},
    "default_branch": "main",
}
Step = HttpResponse | Exception | Callable[[HttpRequest], HttpResponse]


class ScriptedTransport:
    def __init__(self, *steps: Step) -> None:
        self.steps = list(steps)
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest, *, timeout: float) -> HttpResponse:
        self.requests.append(request)
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(request)
        return step


def ok(body: Any, **headers: str) -> HttpResponse:
    return HttpResponse(200, headers, json.dumps(body).encode())


def status(code: int, message: str = "", **headers: str) -> HttpResponse:
    return HttpResponse(code, headers, json.dumps({"message": message}).encode())


def make(
    *steps: Step, max_attempts: int = 3
) -> tuple[GitHubClient, ScriptedTransport, list[float]]:
    transport = ScriptedTransport(*steps)
    sleeps: list[float] = []
    client = GitHubClient(
        transport,
        retry=RetryPolicy(max_attempts=max_attempts, base_delay=1.0, max_delay=8.0),
        sleep=sleeps.append,
        clock=lambda: 1_000.0,
        jitter=lambda delay: delay,
        metrics=InMemoryMetrics(),
    )
    return client, transport, sleeps


def test_requests_use_bearer_token_api_version_and_fixed_host() -> None:
    client, transport, _ = make(ok(REPO_JSON))
    info = client.get_repository(TOKEN, 5001)
    assert info.ref == REPO
    assert info.default_branch == "main"
    request = transport.requests[0]
    assert request.url == "https://api.github.com/repositories/5001"
    assert request.headers["Authorization"] == f"Bearer {TOKEN.reveal()}"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert TOKEN.reveal() not in repr(request)


@pytest.mark.parametrize(
    ("code", "error", "also"),
    [
        (401, GitHubUnauthorizedError, AuthenticationError),
        (403, GitHubForbiddenError, AuthorizationError),
        (404, GitHubNotFoundError, GitHubAPIError),
        (409, GitHubConflictError, GitHubAPIError),
        (422, GitHubValidationError, GitHubAPIError),
        (301, GitHubAPIError, GitHubAPIError),
        (418, GitHubAPIError, GitHubAPIError),
    ],
)
def test_http_errors_are_normalised(
    code: int, error: type[Exception], also: type[Exception]
) -> None:
    client, transport, sleeps = make(status(code, f"Bad credentials {TOKEN.reveal()}"))
    with pytest.raises(error) as info:
        client.get_repository(TOKEN, 5001)
    assert isinstance(info.value, also)
    assert f"HTTP {code}" in str(info.value)
    assert TOKEN.reveal() not in str(info.value)
    assert len(transport.requests) == 1
    assert sleeps == []  # client errors are not retried


def test_server_errors_are_retried_with_bounded_backoff() -> None:
    client, transport, sleeps = make(status(502), status(503), ok(REPO_JSON))
    assert client.get_repository(TOKEN, 5001).id == 5001
    assert sleeps == [1.0, 2.0]
    client, transport, sleeps = make(status(500))
    with pytest.raises(GitHubServerError):
        client.get_repository(TOKEN, 5001)
    assert len(transport.requests) == 3
    assert sleeps == [1.0, 2.0]


@pytest.mark.parametrize(
    ("failure", "text"),
    [(TransportError("x", timeout=True), "timed out"), (TransportError("x"), "network error")],
)
def test_timeouts_and_network_failures_are_bounded(failure: TransportError, text: str) -> None:
    client, transport, sleeps = make(failure)
    with pytest.raises(GitHubUnavailableError, match=text):
        client.get_repository(TOKEN, 5001)
    assert len(transport.requests) == 3
    assert len(sleeps) == 2


def test_check_run_creation_is_not_retried_after_server_error() -> None:
    client, transport, _ = make(
        status(500), ok({"id": 1, "head_sha": "a" * 40, "status": "queued"})
    )
    with pytest.raises(GitHubServerError):
        client.create_check_run(TOKEN, REPO, {"name": "x"})
    assert len(transport.requests) == 1  # a retry could create a duplicate run


def test_rate_limit_waits_and_retries() -> None:
    reset = status(
        403,
        "API rate limit exceeded",
        **{"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1030"},
    )
    client, _, sleeps = make(reset, status(429, "slow down", **{"retry-after": "5"}), ok(REPO_JSON))
    assert client.get_repository(TOKEN, 5001).id == 5001
    assert sleeps == [30.0, 5.0]


def test_persistent_rate_limit_fails_after_bounded_attempts() -> None:
    client, transport, sleeps = make(status(429, **{"retry-after": "2"}))
    with pytest.raises(GitHubRateLimitError) as info:
        client.get_repository(TOKEN, 5001)
    assert len(transport.requests) == 3
    assert sleeps == [2.0, 2.0]
    assert info.value.retry_after == 2.0


def test_long_rate_limit_fails_immediately_instead_of_blocking() -> None:
    client, transport, sleeps = make(status(403, "secondary rate limit", **{"retry-after": "3600"}))
    with pytest.raises(GitHubRateLimitError):
        client.get_repository(TOKEN, 5001)
    assert len(transport.requests) == 1
    assert sleeps == []


def test_secondary_rate_limit_without_headers_waits_a_minute() -> None:
    client, _, sleeps = make(status(403, "You have exceeded a secondary rate limit"), ok(REPO_JSON))
    client.get_repository(TOKEN, 5001)
    assert sleeps == [60.0]


def test_pagination_follows_same_origin_links_only() -> None:
    page1 = HttpResponse(
        200,
        {
            "link": (
                '<https://api.github.com/installation/repositories?page=2>; rel="next", '
                '<https://api.github.com/installation/repositories?page=3>; rel="last"'
            )
        },
        json.dumps({"repositories": [REPO_JSON]}).encode(),
    )
    page2 = ok({"repositories": [REPO_JSON | {"id": 5002, "name": "service"}]})
    client, transport, _ = make(page1, page2)
    repos = client.list_installation_repositories(TOKEN)
    assert [r.id for r in repos] == [5001, 5002]
    assert transport.requests[0].url.endswith("per_page=100")

    evil = HttpResponse(
        200,
        {"link": '<https://evil.example/steal>; rel="next"'},
        json.dumps({"repositories": []}).encode(),
    )
    client, transport, _ = make(evil)
    with pytest.raises(GitHubAPIError, match="unexpected location"):
        client.list_installation_repositories(TOKEN)
    assert len(transport.requests) == 1


def test_pagination_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    looping = HttpResponse(
        200, {"link": '<https://api.github.com/app/installations?page=2>; rel="next"'}, b"[]"
    )
    client, transport, _ = make(looping)
    monkeypatch.setattr("commitguard.github.client.MAX_PAGES", 3)
    with pytest.raises(GitHubAPIError, match="pages"):
        client.list_app_installations(TOKEN)
    assert len(transport.requests) == 3


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        json.dumps({"id": "x"}).encode(),
        json.dumps({**REPO_JSON, "name": "../x"}).encode(),
    ],
)
def test_malformed_responses_are_errors(body: bytes) -> None:
    client, _, _ = make(HttpResponse(200, {}, body))
    with pytest.raises(GitHubAPIError):
        client.get_repository(TOKEN, 5001)


def test_path_segments_are_encoded() -> None:
    pr = {"number": 1, "state": "open", "head": {"sha": "a" * 40}, "base": {"sha": "b" * 40}}
    client, transport, _ = make(ok(pr))
    client.get_pull_request(TOKEN, REPO, 1)
    assert transport.requests[0].url == "https://api.github.com/repos/octo-org/project/pulls/1"


@pytest.mark.parametrize(
    "url",
    [
        "http://api.github.com",
        "https://user:pass@api.github.com",
        "https://api.github.com?x=1",
        "file:///etc/passwd",
    ],
)
def test_api_url_must_be_plain_https(url: str) -> None:
    with pytest.raises(ValueError, match="https"):
        GitHubClient(ScriptedTransport(ok({})), api_url=url)


def test_urllib_transport_refuses_non_https() -> None:
    with pytest.raises(TransportError, match="non-HTTPS"):
        UrllibTransport().send(HttpRequest("GET", "http://127.0.0.1/", {}), timeout=1)
    with pytest.raises(TransportError, match="non-HTTPS"):
        UrllibTransport().send(HttpRequest("GET", "file:///etc/passwd", {}), timeout=1)


def test_token_response_is_wrapped_as_secret() -> None:
    body = {
        "token": "ghs_" + "Z" * 36,
        "expires_at": "2026-09-01T13:00:00Z",
        "permissions": {"checks": "write"},
        "repositories": [{"id": 5001}],
    }
    client, transport, _ = make(ok(body))
    grant = client.create_installation_token(
        TOKEN, 42, repository_ids=[5001], permissions={"checks": "write"}
    )
    assert isinstance(grant.token, Secret)
    assert "ZZZZ" not in repr(grant)
    sent = json.loads(transport.requests[0].body or b"{}")
    assert sent == {"permissions": {"checks": "write"}, "repository_ids": [5001]}
