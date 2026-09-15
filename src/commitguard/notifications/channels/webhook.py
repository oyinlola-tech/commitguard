"""Signed outbound webhooks.

Request::

    POST <endpoint URL>                       (HTTPS; redirects are not followed)
    Content-Type: application/json
    User-Agent: CommitGuard-Notifications
    X-CommitGuard-Event: policy_rolled_back
    X-CommitGuard-Delivery: <idempotency key>  (same value on every retry)
    X-CommitGuard-Timestamp: 1767225600        (Unix seconds)
    X-CommitGuard-Signature: v1=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>

Receivers verify the signature over the timestamp *and* the raw body with
:func:`verify_signature` (or the equivalent in their language), reject
timestamps outside a small window (default five minutes) to stop replays, and
use ``X-CommitGuard-Delivery`` to discard a retried delivery they already
processed.

Signing secrets: each endpoint has its own secret, derived from the
deployment's signing key (``COMMITGUARD_NOTIFICATION_SIGNING_KEY``) and the
endpoint ID with HMAC. Nothing secret is stored in the database; the secret is
shown once, when an administrator adds the endpoint. A tenant's secret reveals
nothing about another tenant's.

Network safety: the endpoint host is resolved at send time and the connection
is made to the resolved address itself (TLS still verifies the certificate for
the host name), so a DNS change between the address check and the connection
cannot redirect the request. Loopback, private, link-local and other
non-public addresses are refused in production.
"""

import hashlib
import hmac
import http.client
import ipaddress
import socket
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from commitguard.notifications.channels.base import DeliveryError, DeliveryReceipt
from commitguard.security.secrets import Secret

WEBHOOK_TIMEOUT_SECONDS = 10.0
SIGNATURE_TOLERANCE_SECONDS = 300
MAX_URL_CHARS = 2048
SIGNATURE_VERSION = "v1"
SECRET_PREFIX = "whsec_"  # noqa: S105 - display prefix, not a secret


def endpoint_secret(signing_key: Secret, endpoint_id: str) -> Secret:
    digest = hmac.new(
        signing_key.reveal().encode("utf-8"),
        b"commitguard-webhook-endpoint\x1f" + endpoint_id.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return Secret(SECRET_PREFIX + digest)


def sign(secret: Secret, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.reveal().encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"{SIGNATURE_VERSION}={mac.hexdigest()}"


def verify_signature(
    secret: Secret,
    *,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: float,
    tolerance: int = SIGNATURE_TOLERANCE_SECONDS,
) -> bool:
    """Receiver-side check: authentic body, fresh timestamp (replay window)."""
    if not timestamp or not signature or not timestamp.isascii() or not timestamp.isdigit():
        return False
    sent = int(timestamp)
    if abs(now - sent) > tolerance:
        return False
    expected = sign(secret, sent, body)
    return hmac.compare_digest(expected.encode("ascii"), signature.strip().encode("ascii"))


class WebhookUrlError(ValueError):
    pass


def validate_webhook_url(url: object, *, allow_insecure_local: bool) -> str:
    """An endpoint URL administrators may register."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_CHARS:
        raise WebhookUrlError("the webhook URL must be an https:// URL")
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in url):
        raise WebhookUrlError("the webhook URL contains invalid characters")
    parts = urlsplit(url)
    host = parts.hostname
    local = host in ("localhost", "127.0.0.1", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local and allow_insecure_local):
        raise WebhookUrlError("the webhook URL must use https")
    if not host or parts.username or parts.password or parts.fragment:
        raise WebhookUrlError("the webhook URL must not contain credentials or a fragment")
    try:
        _ = parts.port
    except ValueError:
        raise WebhookUrlError("the webhook URL has an invalid port") from None
    if not allow_insecure_local:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if local or (address is not None and not address.is_global):
            raise WebhookUrlError("the webhook URL must point to a public host")
    return url


@dataclass(frozen=True, slots=True)
class WebhookRequest:
    url: str
    body: bytes
    headers: Mapping[str, str]


class WebhookTransport(Protocol):
    def post(self, request: WebhookRequest, *, timeout: float) -> int:
        """Send the request and return the HTTP status (redirects are not followed)."""
        ...


type Resolver = Callable[[str, int], list[str]]


def _resolve(host: str, port: int) -> list[str]:
    return sorted(
        {str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    )


class PinnedHttpsTransport:
    """HTTPS POST to the address that passed the network check (no DNS rebinding)."""

    def __init__(self, *, allow_private: bool = False, resolver: Resolver = _resolve) -> None:
        self._allow_private = allow_private
        self._resolver = resolver

    def post(self, request: WebhookRequest, *, timeout: float) -> int:
        parts = urlsplit(request.url)
        host = parts.hostname or ""
        secure = parts.scheme == "https"
        port = parts.port or (443 if secure else 80)
        try:
            addresses = self._resolver(host, port)
        except OSError:
            raise DeliveryError("webhook_dns_failed") from None
        allowed = [a for a in addresses if self._allow_private or ipaddress.ip_address(a).is_global]
        if not allowed:
            raise DeliveryError("webhook_address_refused", permanent=True)
        address = allowed[0]
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        connection: http.client.HTTPConnection
        if secure:
            connection = _PinnedHTTPSConnection(host, port, address, timeout)
        else:
            connection = http.client.HTTPConnection(address, port, timeout=timeout)
        try:
            connection.request("POST", path, body=request.body, headers=dict(request.headers))
            response = connection.getresponse()
            response.read(65536)
            return int(response.status)
        except TimeoutError:
            raise DeliveryError("webhook_timeout") from None
        except (ssl.SSLError, http.client.HTTPException, OSError) as exc:
            raise DeliveryError(f"webhook_unreachable_{type(exc).__name__}"[:64]) from None
        finally:
            connection.close()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        context = ssl.create_default_context()
        self.sock = context.wrap_socket(sock, server_hostname=self.host)


class WebhookProvider:
    name = "webhook"

    def __init__(
        self,
        signing_key: Secret,
        transport: WebhookTransport,
        *,
        timeout: float = WEBHOOK_TIMEOUT_SECONDS,
    ) -> None:
        self._signing_key = signing_key
        self._transport = transport
        self._timeout = timeout

    def secret_for(self, endpoint_id: str) -> Secret:
        return endpoint_secret(self._signing_key, endpoint_id)

    def send(
        self,
        *,
        url: str,
        endpoint_id: str,
        event_type: str,
        body: bytes,
        idempotency_key: str,
        timestamp: int,
    ) -> DeliveryReceipt:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "CommitGuard-Notifications",
            "X-CommitGuard-Event": event_type,
            "X-CommitGuard-Delivery": idempotency_key,
            "X-CommitGuard-Timestamp": str(timestamp),
            "X-CommitGuard-Signature": sign(self.secret_for(endpoint_id), timestamp, body),
        }
        status = self._transport.post(
            WebhookRequest(url=url, body=body, headers=headers), timeout=self._timeout
        )
        if 200 <= status < 300:
            return DeliveryReceipt(self.name, idempotency_key)
        if 300 <= status < 400:
            raise DeliveryError("webhook_redirect_refused", permanent=True)
        if status in (408, 425, 429) or status >= 500:
            raise DeliveryError(f"webhook_http_{status}")
        raise DeliveryError(f"webhook_http_{status}", permanent=True)
