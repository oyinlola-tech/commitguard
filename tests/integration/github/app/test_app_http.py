"""The WSGI webhook endpoint, health probes and a real HTTP round trip."""

import http.client
import io
import json
import threading
import uuid
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from commitguard.github.app import create_wsgi_app
from commitguard.github.webhooks import compute_signature


class _Silent(WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


def call(
    app,
    method: str,
    path: str,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    content_length: str | None = None,
):  # type: ignore[no-untyped-def]
    environ: dict[str, Any] = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(body)) if content_length is None else content_length,
        "REMOTE_ADDR": "192.0.2.1",
        "wsgi.input": io.BytesIO(body),
    }
    for name, value in (headers or {}).items():
        key = name.upper().replace("-", "_")
        if key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            environ[key] = value
        else:
            environ[f"HTTP_{key}"] = value
    captured: dict[str, Any] = {}

    def start_response(status: str, response_headers: list[tuple[str, str]]) -> None:
        captured["status"] = int(status.split()[0])
        captured["headers"] = dict(response_headers)

    payload = b"".join(create_wsgi_app(app.service)(environ, start_response))
    return captured["status"], captured["headers"], json.loads(payload)


def signed(app, event: str, payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:  # type: ignore[no-untyped-def]
    body = json.dumps(payload).encode()
    return body, {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": str(uuid.uuid4()),
        "X-Hub-Signature-256": compute_signature(app.service._secret, body),
    }


def test_routes_and_security_headers(app) -> None:  # type: ignore[no-untyped-def]
    status, headers, body = call(app, "GET", "/health")
    assert (status, body) == (200, {"status": "ok"})
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert call(app, "GET", "/nope")[0] == 404
    status, headers, _ = call(app, "GET", "/webhooks/github")
    assert (status, headers["Allow"]) == (405, "POST")
    assert call(app, "POST", "/health")[0] == 405


def test_readiness_reflects_workers_and_store_without_secrets(app) -> None:  # type: ignore[no-untyped-def]
    status, _, body = call(app, "GET", "/ready")
    assert status == 503
    assert body["checks"]["workers"] == "not running"
    app.service.start()
    try:
        status, _, body = call(app, "GET", "/ready")
    finally:
        app.service.stop()
    assert (status, body["status"]) == (200, "ready")
    assert set(body["checks"]) == {"configuration", "store", "workers", "queue"}
    assert "secret" not in json.dumps(body).lower().replace('"configuration"', "")


def test_request_validation(app, payloads) -> None:  # type: ignore[no-untyped-def]
    body, headers = signed(app, "push", payloads.push("1" * 40, "2" * 40))
    assert (
        call(
            app,
            "POST",
            "/webhooks/github",
            body,
            {**headers, "Content-Type": "application/x-www-form-urlencoded"},
        )[0]
        == 415
    )
    assert call(app, "POST", "/webhooks/github", body, headers, content_length="")[0] == 411
    assert call(app, "POST", "/webhooks/github", body, headers, content_length="-1")[0] == 411
    status, _, _ = call(
        app, "POST", "/webhooks/github", b"", headers, content_length=str(26 * 1024 * 1024)
    )
    assert status == 413  # rejected from Content-Length without reading the body
    assert (
        call(app, "POST", "/webhooks/github", body[:10], headers, content_length=str(len(body)))[0]
        == 400
    )
    status, _, response = call(
        app,
        "POST",
        "/webhooks/github",
        body,
        {**headers, "X-Hub-Signature-256": "sha256=" + "0" * 64},
    )
    assert (status, response) == (401, {"error": "invalid webhook signature"})
    status, _, response = call(app, "POST", "/webhooks/github", body, headers)
    assert (status, response) == (202, {"status": "queued"})


def test_rate_limiting(make_app, payloads) -> None:  # type: ignore[no-untyped-def]
    app = make_app(rate_limit_per_minute=3)
    statuses = []
    for _ in range(5):
        body, headers = signed(app, "ping", {"zen": "hi"})
        statuses.append(call(app, "POST", "/webhooks/github", body, headers)[0])
    assert statuses == [202, 202, 202, 429, 429]


def test_internal_errors_are_generic(app, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("database password=hunter2 at /srv/secret")

    monkeypatch.setattr(app.service, "handle_webhook", explode)
    status, _, body = call(app, "POST", "/webhooks/github", b"{}", {"X-GitHub-Event": "push"})
    assert (status, body) == (500, {"error": "internal error"})


def test_real_http_round_trip(app, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    server: WSGIServer = make_server(
        "127.0.0.1", 0, create_wsgi_app(app.service), handler_class=_Silent
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body, headers = signed(app, "push", payloads.push("1" * 40, "2" * 40))
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        connection.request(
            "POST",
            "/webhooks/github",
            body=body,
            headers={**headers, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 202
        assert json.loads(response.read()) == {"status": "queued"}
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
