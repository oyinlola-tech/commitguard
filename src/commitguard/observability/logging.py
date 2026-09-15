"""Structured JSON logs with correlation IDs and secret redaction.

Every record is one JSON object::

    {"ts": "...", "level": "info", "logger": "commitguard.github.worker",
     "event": "scan_completed", "delivery_id": "...", "job_id": "...",
     "repository": "owner/name", "result": "block", "violations": 1}

* correlation fields (``delivery_id``, ``job_id``, ``scan_id``,
  ``installation_id``, ``repository``) are taken from context variables, so a
  webhook, its scan, the GitHub API requests and the Check Run update share IDs;
* field names that look like credentials are replaced with ``[REDACTED]``;
* every string passes through :func:`commitguard.security.secrets.redact` and
  is truncated, so untrusted commit data cannot flood the log;
* exceptions are logged by type and redacted message only - never tracebacks
  with local variables.
"""

import json
import logging
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TextIO

from commitguard.security.secrets import REDACTED, redact

MAX_FIELD_CHARS = 1000
MAX_FIELDS = 40
CORRELATION_FIELDS = (
    "request_id",
    "delivery_id",
    "job_id",
    "scan_id",
    "notification_event_id",
    "installation_id",
    "repository",
)
_SENSITIVE_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "private_key",
    "privatekey",
    "authorization",
    "credential",
    "jwt",
    "signature",
    "cookie",
)

_correlation: ContextVar[Mapping[str, str | int] | None] = ContextVar(
    "commitguard_correlation", default=None
)


@contextmanager
def correlation(**fields: str | int | None) -> Iterator[None]:
    """Attach correlation fields to every log record emitted inside the block."""
    unknown = set(fields) - set(CORRELATION_FIELDS)
    if unknown:
        raise ValueError(f"unknown correlation field(s): {', '.join(sorted(unknown))}")
    merged = dict(_correlation.get() or {})
    merged.update({k: v for k, v in fields.items() if v is not None})
    token = _correlation.set(merged)
    try:
        yield
    finally:
        _correlation.reset(token)


def current_correlation() -> dict[str, str | int]:
    return dict(_correlation.get() or {})


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _clean(value: Any, depth: int = 0) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        text = redact(value)
        return text if len(text) <= MAX_FIELD_CHARS else text[:MAX_FIELD_CHARS] + "...[truncated]"
    if depth < 3 and isinstance(value, Mapping):
        return {
            str(k): (REDACTED if _is_sensitive(str(k)) else _clean(v, depth + 1))
            for k, v in list(value.items())[:MAX_FIELDS]
        }
    if depth < 3 and isinstance(value, list | tuple | set | frozenset):
        return [_clean(v, depth + 1) for v in list(value)[:MAX_FIELDS]]
    return _clean(str(value), depth)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        document: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": _clean(record.getMessage()),
        }
        document.update(_clean(getattr(record, "correlation", None) or {}))
        fields = getattr(record, "fields", None) or {}
        for key, value in list(fields.items())[:MAX_FIELDS]:
            if key in document:
                key = f"field_{key}"
            document[key] = REDACTED if _is_sensitive(key) else _clean(value)
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            document["error_type"] = type(exc).__name__
            document["error"] = _clean(str(exc))
        return json.dumps(document, ensure_ascii=True, sort_keys=False, default=str)


class StructuredLogger:
    """Thin wrapper: ``log.info("scan_completed", result="block", violations=1)``."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _log(
        self, level: int, event: str, fields: Mapping[str, Any], exc: BaseException | None
    ) -> None:
        if not self._logger.isEnabledFor(level):
            return
        extra = {"fields": dict(fields), "correlation": current_correlation()}
        exc_info = (type(exc), exc, None) if exc is not None else None
        self._logger.log(level, event, extra=extra, exc_info=exc_info)

    def debug(self, event: str, **fields: Any) -> None:
        self._log(logging.DEBUG, event, fields, None)

    def info(self, event: str, **fields: Any) -> None:
        self._log(logging.INFO, event, fields, None)

    def warning(self, event: str, *, exc: BaseException | None = None, **fields: Any) -> None:
        self._log(logging.WARNING, event, fields, exc)

    def error(self, event: str, *, exc: BaseException | None = None, **fields: Any) -> None:
        self._log(logging.ERROR, event, fields, exc)


def get_logger(name: str) -> StructuredLogger:
    return StructuredLogger(logging.getLogger(name))


def configure_json_logging(stream: TextIO | None = None, level: int = logging.INFO) -> None:
    """Send ``commitguard.*`` logs to ``stream`` (default stderr) as JSON lines."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("commitguard")
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False
