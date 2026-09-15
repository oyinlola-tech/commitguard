"""Webhook authenticity: X-Hub-Signature-256, headers, size and JSON validation."""

import hashlib
import hmac

import pytest

from commitguard.github.errors import WebhookValidationError
from commitguard.github.webhooks import (
    MAX_WEBHOOK_BYTES,
    compute_signature,
    parse_delivery,
    verify_signature,
)
from commitguard.security.secrets import Secret

BODY = b'{"action":"opened","zen":"Keep it logically awesome."}'
DELIVERY = "72d3162e-cc78-11e3-81ab-4c9367dc0958"


def headers(body: bytes, secret: Secret, **overrides: str | None) -> dict[str, str]:
    values: dict[str, str | None] = {
        "X-Hub-Signature-256": compute_signature(secret, body),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": DELIVERY,
    }
    values.update(overrides)
    return {k: v for k, v in values.items() if v is not None}


def test_signature_matches_githubs_documented_example() -> None:
    # Example from GitHub's "Validating webhook deliveries" documentation.
    example = compute_signature(Secret("It's a Secret to Everybody"), b"Hello, World!")
    assert example == "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"


def test_valid_signature_is_processed(webhook_secret: Secret) -> None:
    delivery = parse_delivery(headers(BODY, webhook_secret), BODY, webhook_secret)
    assert delivery.delivery_id == DELIVERY
    assert delivery.event == "pull_request"
    assert delivery.payload["action"] == "opened"
    assert delivery.body_sha256 == hashlib.sha256(BODY).hexdigest()


def test_headers_are_case_insensitive(webhook_secret: Secret) -> None:
    lowered = {k.lower(): v for k, v in headers(BODY, webhook_secret).items()}
    assert parse_delivery(lowered, BODY, webhook_secret).event == "pull_request"


@pytest.mark.parametrize(
    "case", ["missing", "empty", "sha512", "sha1", "uppercase", "truncated", "invalid"]
)
def test_bad_signatures_are_rejected_with_401(webhook_secret: Secret, case: str) -> None:
    good = compute_signature(webhook_secret, BODY)
    value = {
        "missing": None,
        "empty": "",
        "sha512": "sha512=" + good[7:],
        "sha1": "sha1=" + "a" * 40,
        "uppercase": "sha256=" + good[7:].upper(),
        "truncated": good[:-2],
        "invalid": "sha256=" + "0" * 64,
    }[case]
    bad_headers = headers(BODY, webhook_secret, **{"X-Hub-Signature-256": value})
    with pytest.raises(WebhookValidationError) as info:
        parse_delivery(bad_headers, BODY, webhook_secret)
    assert info.value.status == 401


def test_modified_payload_is_rejected(webhook_secret: Secret) -> None:
    signed = headers(BODY, webhook_secret)
    with pytest.raises(WebhookValidationError) as info:
        parse_delivery(signed, BODY.replace(b"opened", b"closed"), webhook_secret)
    assert info.value.status == 401


def test_wrong_secret_is_rejected(webhook_secret: Secret) -> None:
    other = Secret("a-completely-different-secret-value")
    with pytest.raises(WebhookValidationError) as info:
        parse_delivery(headers(BODY, other), BODY, webhook_secret)
    assert info.value.status == 401


def test_signature_is_checked_before_json_parsing(webhook_secret: Secret) -> None:
    """Unauthenticated garbage never reaches the JSON parser (401, not 400)."""
    garbage = b"not json at all" * 100
    forged = headers(garbage, Secret("attacker-guess-secret-12345"))
    with pytest.raises(WebhookValidationError) as info:
        parse_delivery(forged, garbage, webhook_secret)
    assert info.value.status == 401


def test_verification_uses_constant_time_comparison(
    monkeypatch: pytest.MonkeyPatch, webhook_secret: Secret
) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr("commitguard.github.webhooks.hmac.compare_digest", spy)
    verify_signature(webhook_secret, BODY, compute_signature(webhook_secret, BODY))
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"", "empty"),
        (b"not json", "not valid JSON"),
        (b"[1, 2, 3]", "JSON object"),
        (b'{"a": 1, "a": 2}', "not valid JSON"),
        (b'{"a": "' + bytes([0xFF]) + b'"}', "not valid JSON"),
        (b"[" * 100_000 + b"]" * 100_000, "not valid JSON"),
        (b'{"a":' * 70 + b"1" + b"}" * 70, "nested too deeply"),
    ],
    ids=["empty", "garbage", "array", "duplicate-keys", "bad-utf8", "recursion", "depth"],
)
def test_malformed_authenticated_payloads_are_rejected(
    webhook_secret: Secret, body: bytes, message: str
) -> None:
    with pytest.raises(WebhookValidationError) as info:
        parse_delivery(headers(body, webhook_secret), body, webhook_secret)
    assert info.value.status == 400
    assert message in str(info.value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("X-GitHub-Event", None),
        ("X-GitHub-Event", "Pull-Request"),
        ("X-GitHub-Event", "push; injected"),
        ("X-GitHub-Delivery", None),
        ("X-GitHub-Delivery", "../../etc/passwd"),
        ("X-GitHub-Delivery", "x" * 200),
    ],
)
def test_event_and_delivery_headers_are_validated(
    webhook_secret: Secret, name: str, value: str | None
) -> None:
    with pytest.raises(WebhookValidationError) as info:
        parse_delivery(headers(BODY, webhook_secret, **{name: value}), BODY, webhook_secret)
    assert info.value.status == 400


def test_oversized_payload_is_rejected(
    webhook_secret: Secret, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert MAX_WEBHOOK_BYTES == 25 * 1024 * 1024
    monkeypatch.setattr("commitguard.github.webhooks.MAX_WEBHOOK_BYTES", 10)
    with pytest.raises(WebhookValidationError) as info:
        parse_delivery(headers(BODY, webhook_secret), BODY, webhook_secret)
    assert info.value.status == 413


def test_unconfigured_secret_fails_closed() -> None:
    with pytest.raises(WebhookValidationError) as info:
        verify_signature(Secret(""), BODY, "sha256=" + "0" * 64)
    assert info.value.status == 500


def test_errors_never_echo_signature_or_secret(webhook_secret: Secret) -> None:
    bad = "sha256=" + "ab" * 32
    bad_headers = headers(BODY, webhook_secret, **{"X-Hub-Signature-256": bad})
    with pytest.raises(WebhookValidationError) as info:
        parse_delivery(bad_headers, BODY, webhook_secret)
    assert bad not in str(info.value)
    assert webhook_secret.reveal() not in str(info.value)
