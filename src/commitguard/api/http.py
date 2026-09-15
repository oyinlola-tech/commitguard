"""HTTP primitives for the dashboard API: requests, responses, errors, cookies."""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl
from wsgiref.types import WSGIEnvironment

from pydantic import BaseModel

from commitguard.controlplane.access import AccessScope, Principal
from commitguard.controlplane.errors import ControlPlaneError

MAX_BODY_BYTES = 64 * 1024
MAX_QUERY_CHARS = 4096
MAX_QUERY_PARAMS = 32
MAX_COOKIE_HEADER_CHARS = 8192
MAX_JSON_DEPTH = 16

REASONS = {
    200: "OK",
    201: "Created",
    202: "Accepted",
    204: "No Content",
    302: "Found",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    411: "Length Required",
    413: "Payload Too Large",
    415: "Unsupported Media Type",
    422: "Unprocessable Content",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}

API_SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)


class ApiError(Exception):
    """An error returned to the client. ``message`` is safe to display."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        field: str | None = None,
        headers: Iterable[tuple[str, str]] = (),
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field
        self.headers = tuple(headers)

    @classmethod
    def from_control_plane(cls, exc: ControlPlaneError) -> "ApiError":
        return cls(exc.status, exc.code, str(exc), field=getattr(exc, "field", None))


def bad_request(message: str, field: str | None = None) -> ApiError:
    return ApiError(400, "VALIDATION_ERROR", message, field=field)


NOT_FOUND = "The requested resource was not found."


@dataclass
class Request:
    method: str
    path: str
    query: Mapping[str, str]
    headers: Mapping[str, str]
    cookies: Mapping[str, str]
    body: bytes
    remote_addr: str
    request_id: str
    route: str = "unmatched"
    params: dict[str, Any] = field(default_factory=dict)
    principal: Principal | None = None
    session_token: str | None = None
    scope: AccessScope | None = None

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def arg(self, name: str) -> str | None:
        return self.query.get(name)

    def json(self) -> dict[str, Any]:
        if not self.body:
            return {}
        content_type = (self.header("content-type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Send the request body as JSON.")
        try:
            document = json.loads(self.body.decode("utf-8"), object_pairs_hook=_no_duplicates)
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise bad_request("The request body is not valid JSON.") from None
        if not isinstance(document, dict):
            raise bad_request("The request body must be a JSON object.")
        if _depth(document) > MAX_JSON_DEPTH:
            raise bad_request("The request body is nested too deeply.")
        return document


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _depth(value: Any, level: int = 0) -> int:
    if level > MAX_JSON_DEPTH:
        return level
    if isinstance(value, dict):
        return max((_depth(v, level + 1) for v in value.values()), default=level + 1)
    if isinstance(value, list):
        return max((_depth(v, level + 1) for v in value), default=level + 1)
    return level


def parse_query(environ: WSGIEnvironment) -> dict[str, str]:
    raw = environ.get("QUERY_STRING", "") or ""
    if len(raw) > MAX_QUERY_CHARS:
        raise bad_request("The query string is too long.")
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=False, max_num_fields=64)
    except ValueError:
        raise bad_request("The query string is invalid.") from None
    if len(pairs) > MAX_QUERY_PARAMS:
        raise bad_request("Too many query parameters.")
    query: dict[str, str] = {}
    for key, value in pairs:
        if key in query:
            raise bad_request(f"Query parameter {key[:40]} is repeated.", field=key[:40])
        if any(ord(c) < 0x20 for c in key + value):
            raise bad_request("The query string contains control characters.")
        query[key] = value
    return query


def parse_cookies(header: str | None) -> dict[str, str]:
    """Parse a Cookie header without evaluating attributes; malformed parts are ignored."""
    cookies: dict[str, str] = {}
    if not header or len(header) > MAX_COOKIE_HEADER_CHARS:
        return cookies
    for part in header.split(";"):
        name, sep, value = part.strip().partition("=")
        if not sep or not name or not name.isascii():
            continue
        value = value.strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1]
        cookies.setdefault(name, value)
    return cookies


def set_cookie(
    name: str,
    value: str,
    *,
    max_age: int | None,
    http_only: bool = True,
    same_site: str = "Lax",
) -> tuple[str, str]:
    """A ``__Host-`` style cookie: Secure, Path=/, no Domain."""
    parts = [f"{name}={value}", "Path=/", "Secure", f"SameSite={same_site}"]
    if http_only:
        parts.append("HttpOnly")
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    return ("Set-Cookie", "; ".join(parts))


def clear_cookie(name: str) -> tuple[str, str]:
    return set_cookie(name, "", max_age=0)


@dataclass
class Response:
    status: int
    body: bytes = b""
    headers: list[tuple[str, str]] = field(default_factory=list)


def _encode(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list | tuple):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    return value


def json_body(document: Mapping[str, Any]) -> bytes:
    return json.dumps(_encode(document), ensure_ascii=True, separators=(",", ":")).encode("ascii")


def ok(data: Any, meta: Mapping[str, Any] | None = None, *, status: int = 200) -> Response:
    return Response(
        status,
        json_body({"data": data, "meta": dict(meta or {})}),
        [("Content-Type", "application/json; charset=utf-8")],
    )


def error_response(exc: ApiError, request_id: str) -> Response:
    error: dict[str, Any] = {"code": exc.code, "message": exc.message, "request_id": request_id}
    if exc.field:
        error["field"] = exc.field
    return Response(
        exc.status,
        json_body({"error": error}),
        [("Content-Type", "application/json; charset=utf-8"), *exc.headers],
    )


def redirect(location: str, headers: Iterable[tuple[str, str]] = ()) -> Response:
    return Response(302, b"", [("Location", location), *headers])
