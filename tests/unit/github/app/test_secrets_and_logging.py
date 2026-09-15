"""Secrets never appear in repr, logs or redacted text; logs carry correlation IDs."""

import io
import json
import logging
import pickle

import pytest

from commitguard.observability.logging import (
    JsonFormatter,
    configure_json_logging,
    correlation,
    get_logger,
)
from commitguard.observability.metrics import InMemoryMetrics
from commitguard.security.secrets import REDACTED, Secret, SecretRedactor, redact, register_secret


def test_secret_never_shows_its_value() -> None:
    secret = Secret("super-secret-value-123")
    for rendered in (
        repr(secret),
        str(secret),
        f"{secret}",
        f"{secret!r}",
        " ".join([str(secret)]),
    ):
        assert "super-secret" not in rendered
    assert secret.reveal() == "super-secret-value-123"
    with pytest.raises(TypeError):
        pickle.dumps(secret)
    assert Secret("a") == Secret("a")
    assert Secret("a") != Secret("b")


def test_redactor_removes_registered_values_and_credential_shapes() -> None:
    redactor = SecretRedactor()
    redactor.register("my-webhook-secret-value")
    text = (
        "secret my-webhook-secret-value token ghs_16C7e42F292c6912E7710c838347Ae178B4a "
        "pat github_pat_11ABCDEFG0123456789_abcdefghijklmnop "
        "jwt eyJhbGciOiJSUzI1NiJ9.eyJpc3MiOiIxMjMifQ.c2lnbmF0dXJlLXZhbHVl "
        "Authorization: Bearer abcdefghijklmnop header "
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    )
    cleaned = redactor.redact(text)
    for leaked in (
        "my-webhook-secret-value",
        "ghs_16C7e42F",
        "github_pat_11",
        "eyJhbGciOi",
        "abcdefghijklmnop",
        "MIIEow",
    ):
        assert leaked not in cleaned
    assert cleaned.count(REDACTED) >= 5


def test_redaction_keeps_ordinary_text() -> None:
    text = "token provider refreshed; authorization checks passed for octo-org/project"
    assert redact(text) == text


def test_json_logs_redact_secrets_and_include_correlation() -> None:
    stream = io.StringIO()
    configure_json_logging(stream)
    register_secret("registered-secret-in-log-9876")
    log = get_logger("commitguard.test")
    with correlation(delivery_id="d-1", job_id="j-1", repository="octo/project"):
        log.info(
            "scan_completed",
            result="block",
            installation_token="ghs_should_never_be_logged_123456789",  # noqa: S106 - fake
            note="contains registered-secret-in-log-9876 inside",
            headers={"Authorization": "Bearer xyz", "Accept": "json"},
        )
        log.warning("failure", exc=RuntimeError("boom ghs_abcdefghijklmnopqrstuvwxyz0123"))
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    first, second = lines
    assert first["event"] == "scan_completed"
    assert (first["delivery_id"], first["job_id"], first["repository"]) == (
        "d-1",
        "j-1",
        "octo/project",
    )
    assert first["installation_token"] == REDACTED
    assert first["headers"]["Authorization"] == REDACTED
    assert first["headers"]["Accept"] == "json"
    assert "registered-secret-in-log" not in first["note"]
    assert second["error_type"] == "RuntimeError"
    assert "ghs_abcdef" not in second["error"]
    raw = stream.getvalue()
    assert "ghs_should_never" not in raw
    assert "xyz" not in raw
    logging.getLogger("commitguard").handlers.clear()


def test_log_fields_are_truncated_and_escaped() -> None:
    record = logging.LogRecord("commitguard.x", logging.INFO, __file__, 1, "evt", None, None)
    record.fields = {"subject": "A" * 5000 + "\n\x1b[2Kinjected"}  # type: ignore[attr-defined]
    record.correlation = {}  # type: ignore[attr-defined]
    document = json.loads(JsonFormatter().format(record))
    assert len(document["subject"]) < 1100
    assert "\n" not in JsonFormatter().format(record)


def test_unknown_correlation_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown correlation"), correlation(token="x"):  # noqa: S106  # type: ignore[arg-type]
        pass


def test_metrics_counters() -> None:
    metrics = InMemoryMetrics()
    metrics.increment("scans_completed", result="block")
    metrics.increment("scans_completed", result="allow")
    metrics.increment("scans_completed", 2, result="allow")
    assert metrics.value("scans_completed") == 4
    assert metrics.value("scans_completed", result="allow") == 3
    assert metrics.snapshot() == {"scans_completed": 4}
    with pytest.raises(ValueError, match="unknown metric"):
        metrics.increment("private_key_bytes")
