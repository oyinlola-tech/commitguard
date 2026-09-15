"""Notification building blocks: signing, URL safety, rendering, settings, dedup, retries."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from commitguard.controlplane.errors import InputValidationError
from commitguard.core.result import Severity
from commitguard.github.errors import AppConfigurationError
from commitguard.notifications.channels.base import DeliveryError
from commitguard.notifications.channels.email import OutgoingEmail, build_message
from commitguard.notifications.channels.webhook import (
    PinnedHttpsTransport,
    WebhookProvider,
    WebhookRequest,
    WebhookUrlError,
    endpoint_secret,
    sign,
    validate_webhook_url,
    verify_signature,
)
from commitguard.notifications.deduplication import (
    delivery_idempotency_key,
    domain_key,
    storage_key,
)
from commitguard.notifications.models import (
    DEFINITIONS,
    NotificationEvent,
    NotificationType,
    StoredNotificationEvent,
)
from commitguard.notifications.preferences import (
    OrganizationSettings,
    _normalize,
    disabled_deliveries,
    validate_email_recipients,
    validate_organization_document,
)
from commitguard.notifications.retry import MAX_ATTEMPTS, mask_destination, retry_delay
from commitguard.notifications.settings import NotificationMode, load_notification_settings
from commitguard.notifications.templates import render_email, render_webhook, resource_path
from commitguard.security.secrets import Secret

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
KEY = Secret("s" * 40)


def _event(**overrides: object) -> NotificationEvent:
    values: dict[str, object] = {
        "type": NotificationType.HIGH_VIOLATION,
        "account_id": 1001,
        "severity": Severity.HIGH,
        "installation_id": 42,
        "repository_id": 5001,
        "resource_type": "violation",
        "resource_id": "a" * 32,
        "dedup_key": "high_violation:42:5001:pull_request:7:ai_coauthor",
        "title": "Blocked: ai_coauthor in octo-org/<script>",
        "body": "ghs_" + "x" * 36 + " line\nbreak \x1b[2K hidden",
        "metadata": {"rule": "ai_coauthor"},
    }
    values.update(overrides)
    return NotificationEvent(**values)  # type: ignore[arg-type]


def _stored(event: NotificationEvent, occurrences: int = 1) -> StoredNotificationEvent:
    return StoredNotificationEvent(
        event=event,
        occurrences=occurrences,
        created_at=NOW,
        last_occurred_at=NOW,
        dispatched_at=NOW,
        request_id=None,
        delivery_id=None,
        job_id=None,
    )


# --------------------------------------------------------------------------- #
# Event model
# --------------------------------------------------------------------------- #
def test_event_text_is_sanitised_and_secrets_redacted() -> None:
    event = _event()
    assert "ghs_" not in event.body
    assert "\x1b" not in event.body
    assert "\\x1b" in event.body
    assert "\n" not in event.body
    with pytest.raises(ValueError, match="metadata key"):
        _event(metadata={"Bad Key": "x"})
    with pytest.raises(ValueError, match="more than"):
        _event(metadata={f"k{i}": i for i in range(40)})


def test_every_type_has_a_definition_with_a_real_permission() -> None:
    assert set(DEFINITIONS) == set(NotificationType)
    mandatory = {t for t, d in DEFINITIONS.items() if d.mandatory_in_app}
    assert mandatory == {
        NotificationType.CRITICAL_VIOLATION,
        NotificationType.POLICY_CHANGED,
        NotificationType.POLICY_ROLLED_BACK,
        NotificationType.INSTALLATION_DISCONNECTED,
    }


# --------------------------------------------------------------------------- #
# Deduplication
# --------------------------------------------------------------------------- #
def test_coalescing_window_and_idempotency_keys() -> None:
    key = domain_key(NotificationType.HIGH_VIOLATION, 42, 5001, "pull_request:7", "ai_coauthor")
    assert storage_key(NotificationType.HIGH_VIOLATION, key, NOW) == storage_key(
        NotificationType.HIGH_VIOLATION, key, NOW + timedelta(minutes=30)
    ) or storage_key(NotificationType.HIGH_VIOLATION, key, NOW + timedelta(minutes=30)).startswith(
        key
    )
    far = storage_key(NotificationType.HIGH_VIOLATION, key, NOW + timedelta(hours=2))
    assert far != storage_key(NotificationType.HIGH_VIOLATION, key, NOW)
    policy = domain_key(NotificationType.POLICY_CHANGED, 1001, 7)
    assert storage_key(NotificationType.POLICY_CHANGED, policy, NOW) == policy  # no window
    a = delivery_idempotency_key("e1", "email", "x@example.com")
    assert a == delivery_idempotency_key("e1", "email", "x@example.com")
    assert a != delivery_idempotency_key("e1", "webhook", "x@example.com")


def test_retries_are_bounded_with_backoff() -> None:
    delays = [retry_delay(n) for n in range(1, MAX_ATTEMPTS + 1)]
    assert delays[:-1] == [
        timedelta(minutes=1),
        timedelta(minutes=5),
        timedelta(minutes=30),
        timedelta(hours=2),
    ]
    assert delays[-1] is None
    assert mask_destination("email", "security@example.com") == "s***@example.com"
    assert mask_destination("webhook", "abc") == "abc"


# --------------------------------------------------------------------------- #
# Preferences
# --------------------------------------------------------------------------- #
def test_organization_document_validation() -> None:
    with pytest.raises(InputValidationError, match="cannot be turned off"):
        validate_organization_document({"policy_changed": {"in_app": False}})
    with pytest.raises(InputValidationError):
        validate_organization_document({"high_violation": {"sms": True}})
    with pytest.raises(InputValidationError):
        validate_organization_document({"high_violation": {"email": "yes"}})
    with pytest.raises(InputValidationError):
        validate_organization_document({"unknown": {}})
    types = validate_organization_document({"high_violation": {"email": True}})
    assert types[NotificationType.HIGH_VIOLATION].email is True
    before = OrganizationSettings(1, 1, _normalize({}), ("a@example.com",))
    assert disabled_deliveries(before, types, ["a@example.com"]) == []
    off = validate_organization_document({"policy_changed": {"email": False}})
    assert disabled_deliveries(before, off, []) == [
        "Policy changes: email",
        "e-mail recipients removed: 1",
    ]


@pytest.mark.parametrize(
    "value",
    [
        "a@example.com\r\nBcc: x@example.com",
        "Name <a@example.com>",
        "a@localhost",
        "a" * 300 + "@example.com",
        "",
    ],
)
def test_email_recipients_reject_header_injection_and_display_names(value: str) -> None:
    with pytest.raises(InputValidationError):
        validate_email_recipients([value])


# --------------------------------------------------------------------------- #
# Channels
# --------------------------------------------------------------------------- #
def test_email_is_plain_text_with_safe_headers() -> None:
    message = build_message(
        "commitguard@example.com",
        OutgoingEmail(
            to="sec@example.com", subject="Blocked\r\nBcc: evil@example.com", text="<b>x</b>"
        ),
        "k" * 64,
    )
    assert message["Subject"] == "Blocked Bcc: evil@example.com"
    assert message["Bcc"] is None
    assert message.get_content_type() == "text/plain"
    assert "<b>x</b>" in message.get_content()
    assert message["Message-ID"] == f"<{'k' * 64}@example.com>"


def test_webhook_signature_timestamp_and_per_endpoint_secrets() -> None:
    body = b'{"type":"policy_changed"}'
    secret = endpoint_secret(KEY, "a" * 32)
    assert secret != endpoint_secret(KEY, "b" * 32)
    assert endpoint_secret(KEY, "a" * 32) == secret
    signature = sign(secret, 1_800_000_000, body)
    ok = dict(timestamp="1800000000", signature=signature, body=body)
    assert verify_signature(secret, now=1_800_000_100, **ok)  # type: ignore[arg-type]
    assert not verify_signature(secret, now=1_800_000_400, **ok)  # type: ignore[arg-type] - replay window
    assert not verify_signature(endpoint_secret(KEY, "b" * 32), now=1_800_000_000, **ok)  # type: ignore[arg-type]
    assert not verify_signature(
        secret, timestamp="1800000001", signature=signature, body=body, now=1_800_000_000
    )
    assert not verify_signature(secret, timestamp=None, signature=signature, body=body, now=0)
    assert not verify_signature(secret, timestamp="1800000000", signature=None, body=body, now=0)


@pytest.mark.parametrize(
    ("url", "local_ok"),
    [
        ("http://hooks.example.com/x", False),
        ("https://127.0.0.1/x", False),
        ("https://10.0.0.5/x", False),
        ("https://169.254.169.254/latest/meta-data", False),
        ("https://[::1]/x", False),
        ("https://localhost/x", False),
        ("https://user:pass@hooks.example.com/", True),
        ("https://hooks.example.com/#frag", True),
        ("https://hooks.example.com:99999/", True),
        ("https://hooks.example.com/\nX-Injected: 1", True),
        ("file:///etc/passwd", True),
    ],
)
def test_webhook_urls_are_validated(url: str, local_ok: bool) -> None:
    with pytest.raises(WebhookUrlError):
        validate_webhook_url(url, allow_insecure_local=local_ok)


def test_webhook_url_validation_accepts_public_https_and_local_in_development() -> None:
    assert validate_webhook_url("https://hooks.example.com/a?b=c", allow_insecure_local=False)
    assert validate_webhook_url("http://localhost:9000/hook", allow_insecure_local=True)


def test_pinned_transport_refuses_private_addresses_after_resolution() -> None:
    transport = PinnedHttpsTransport(resolver=lambda host, port: ["10.1.2.3", "127.0.0.1"])
    with pytest.raises(DeliveryError) as error:
        transport.post(WebhookRequest("https://rebinding.example.com/", b"{}", {}), timeout=1)
    assert (error.value.code, error.value.permanent) == ("webhook_address_refused", True)


def test_webhook_status_classification() -> None:
    class Fixed:
        def __init__(self, status: int) -> None:
            self.status = status

        def post(self, request: WebhookRequest, *, timeout: float) -> int:
            return self.status

    def attempt(status: int) -> DeliveryError | None:
        provider = WebhookProvider(KEY, Fixed(status))
        try:
            provider.send(
                url="https://x.example.com",
                endpoint_id="a" * 32,
                event_type="t",
                body=b"{}",
                idempotency_key="k",
                timestamp=1,
            )
        except DeliveryError as exc:
            return exc
        return None

    assert attempt(204) is None
    assert attempt(302).permanent  # type: ignore[union-attr]
    assert attempt(404).permanent  # type: ignore[union-attr]
    assert not attempt(429).permanent  # type: ignore[union-attr]
    assert not attempt(503).permanent  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
def test_templates_encode_for_each_channel() -> None:
    stored = _stored(_event(), occurrences=3)
    email = render_email(
        stored,
        to="a@example.com",
        organization="octo-org",
        dashboard_origin="https://cg.example.com",
    )
    assert "Occurrences: 3" in email.text
    assert f"https://cg.example.com/violations/{'a' * 32}" in email.text
    assert "HIGH" in email.subject
    body = render_webhook(
        stored,
        organization="octo-org",
        repository="octo-org/project",
        dashboard_origin=None,
        now=NOW,
    )
    assert b"<script>" not in body  # ASCII-escaped JSON
    document = json.loads(body)
    assert document["title"].endswith("<script>")
    assert document["url"] is None
    assert resource_path("violation", "../../etc") is None
    assert resource_path("unknown", "abc") is None


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def test_settings_never_send_from_a_test_environment(tmp_path: Path) -> None:
    assert load_notification_settings({}).mode is NotificationMode.OFF
    forced = load_notification_settings(
        {"COMMITGUARD_ENV": "test", "COMMITGUARD_NOTIFICATIONS_MODE": "deliver"}
    )
    assert forced.mode is NotificationMode.TEST
    assert forced.email_available
    assert forced.webhook_available
    deliver = load_notification_settings({"COMMITGUARD_NOTIFICATIONS_MODE": "deliver"})
    assert not deliver.email_available
    assert not deliver.webhook_available
    secret_file = tmp_path / "smtp"
    secret_file.write_text("hunter2-hunter2\n")
    configured = load_notification_settings(
        {
            "COMMITGUARD_NOTIFICATIONS_MODE": "deliver",
            "COMMITGUARD_SMTP_HOST": "smtp.example.com",
            "COMMITGUARD_SMTP_FROM": "commitguard@example.com",
            "COMMITGUARD_SMTP_PASSWORD_FILE": str(secret_file),
            "COMMITGUARD_NOTIFICATION_SIGNING_KEY": "k" * 32,
        }
    )
    assert configured.email_available
    assert configured.webhook_available
    assert "hunter2" not in repr(configured)


@pytest.mark.parametrize(
    "env",
    [
        {"COMMITGUARD_NOTIFICATIONS_MODE": "loud"},
        {"COMMITGUARD_SMTP_HOST": "smtp.example.com", "COMMITGUARD_SMTP_FROM": "x\r\n@example.com"},
        {
            "COMMITGUARD_SMTP_HOST": "smtp.example.com",
            "COMMITGUARD_SMTP_FROM": "a@example.com",
            "COMMITGUARD_SMTP_SECURITY": "none",
        },
        {"COMMITGUARD_NOTIFICATION_SIGNING_KEY": "short"},
        {"COMMITGUARD_NOTIFICATION_RETENTION_DAYS": "0"},
        {
            "COMMITGUARD_SMTP_PASSWORD": "a",
            "COMMITGUARD_SMTP_PASSWORD_FILE": "/x",
            "COMMITGUARD_SMTP_HOST": "h",
            "COMMITGUARD_SMTP_FROM": "a@b.c",
        },
    ],
)
def test_invalid_settings_fail_closed_and_name_variables_not_values(env: dict[str, str]) -> None:
    with pytest.raises(AppConfigurationError) as error:
        load_notification_settings(env)
    for value in env.values():
        if len(value) > 6:
            assert value not in str(error.value)
