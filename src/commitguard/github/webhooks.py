"""Webhook boundary: authenticity, headers, size and JSON validation.

Processing order for every delivery (nothing untrusted is parsed before the
signature is verified)::

    size limit -> X-Hub-Signature-256 (HMAC-SHA256, constant-time compare)
               -> X-GitHub-Event / X-GitHub-Delivery format
               -> strict JSON object (UTF-8, no duplicate keys, bounded depth)

A delivery that fails any step raises :class:`WebhookValidationError` carrying
the HTTP status to return (401 for authenticity, 400/413/415 otherwise); the
event is never processed. Replay protection (delivery ID deduplication) is
applied afterwards by :mod:`commitguard.github.storage`, because only a
verified delivery may consume a delivery ID.
"""

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from commitguard.github.errors import WebhookValidationError
from commitguard.security.secrets import Secret

SIGNATURE_HEADER = "x-hub-signature-256"
EVENT_HEADER = "x-github-event"
DELIVERY_HEADER = "x-github-delivery"
MAX_WEBHOOK_BYTES = 25 * 1024 * 1024  # GitHub caps webhook payloads at 25 MB
MAX_JSON_DEPTH = 64

_SIGNATURE_RE = re.compile(r"\Asha256=([0-9a-f]{64})\Z")
_EVENT_RE = re.compile(r"\A[a-z_]{1,64}\Z")
_DELIVERY_RE = re.compile(r"\A[0-9A-Za-z-]{8,72}\Z")


def compute_signature(secret: Secret, body: bytes) -> str:
    """The ``X-Hub-Signature-256`` value GitHub sends for ``body``."""
    digest = hmac.new(secret.reveal().encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: Secret, body: bytes, header: str | None) -> None:
    """Raise ``WebhookValidationError(401)`` unless ``header`` authenticates ``body``."""
    if not secret:
        raise WebhookValidationError("webhook secret is not configured", status=500)
    if header is None or header == "":
        raise WebhookValidationError("missing webhook signature", status=401)
    match = _SIGNATURE_RE.match(header.strip())
    if match is None:
        raise WebhookValidationError("malformed webhook signature", status=401)
    expected = compute_signature(secret, body)[len("sha256=") :]
    if not hmac.compare_digest(expected.encode("ascii"), match.group(1).encode("ascii")):
        raise WebhookValidationError("invalid webhook signature", status=401)


@dataclass(frozen=True, slots=True)
class WebhookDelivery:
    delivery_id: str
    event: str
    payload: dict[str, Any]
    body_sha256: str


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _depth_ok(value: Any, limit: int) -> bool:
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > limit:
            return False
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
    return True


def parse_json_object(body: bytes) -> dict[str, Any]:
    if not body:
        raise WebhookValidationError("empty webhook payload")
    try:
        document = json.loads(body.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise WebhookValidationError("webhook payload is not valid JSON") from None
    if not isinstance(document, dict):
        raise WebhookValidationError("webhook payload must be a JSON object")
    if not _depth_ok(document, MAX_JSON_DEPTH):
        raise WebhookValidationError("webhook payload is nested too deeply")
    return document


def header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup."""
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def parse_delivery(headers: Mapping[str, str], body: bytes, secret: Secret) -> WebhookDelivery:
    """Authenticate and parse one webhook delivery."""
    if len(body) > MAX_WEBHOOK_BYTES:
        raise WebhookValidationError("webhook payload too large", status=413)
    verify_signature(secret, body, header(headers, SIGNATURE_HEADER))
    event = header(headers, EVENT_HEADER)
    if event is None or not _EVENT_RE.match(event):
        raise WebhookValidationError("missing or invalid X-GitHub-Event header")
    delivery_id = header(headers, DELIVERY_HEADER)
    if delivery_id is None or not _DELIVERY_RE.match(delivery_id):
        raise WebhookValidationError("missing or invalid X-GitHub-Delivery header")
    payload = parse_json_object(body)
    return WebhookDelivery(
        delivery_id=delivery_id,
        event=event,
        payload=payload,
        body_sha256=hashlib.sha256(body).hexdigest(),
    )
