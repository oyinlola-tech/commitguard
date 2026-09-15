"""Notification delivery configuration from the environment.

=============================================  =============================================
Variable                                       Meaning
=============================================  =============================================
``COMMITGUARD_NOTIFICATIONS_MODE``             ``off`` (default): in-app notifications only;
                                               ``deliver``: e-mail and webhooks are sent;
                                               ``test``: they are recorded in memory, never
                                               sent (forced when ``COMMITGUARD_ENV=test``)
``COMMITGUARD_SMTP_HOST``                      SMTP relay host (enables e-mail)
``COMMITGUARD_SMTP_PORT``                      default 587
``COMMITGUARD_SMTP_SECURITY``                  ``starttls`` (default), ``tls``, or ``none``
                                               (only for localhost outside production)
``COMMITGUARD_SMTP_USERNAME``                  optional
``COMMITGUARD_SMTP_PASSWORD``                  optional, *or*
``COMMITGUARD_SMTP_PASSWORD_FILE``             a file containing it (preferred)
``COMMITGUARD_SMTP_FROM``                      sender address, e.g. ``commitguard@example.com``
``COMMITGUARD_NOTIFICATION_SIGNING_KEY``       key from which webhook signing secrets are
``COMMITGUARD_NOTIFICATION_SIGNING_KEY_FILE``  derived (enables webhooks; 32+ characters)
``COMMITGUARD_NOTIFICATION_RETENTION_DAYS``    notifications, deliveries (default 90)
=============================================  =============================================

Links in e-mail and webhook payloads use ``COMMITGUARD_DASHBOARD_URL`` when set.
"""

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path

from commitguard.github.errors import AppConfigurationError
from commitguard.security.secrets import Secret, register_secret
from commitguard.utils.filesystem import read_bytes_limited

ENV_MODE = "COMMITGUARD_NOTIFICATIONS_MODE"
ENV_SMTP_HOST = "COMMITGUARD_SMTP_HOST"
ENV_SMTP_PORT = "COMMITGUARD_SMTP_PORT"
ENV_SMTP_SECURITY = "COMMITGUARD_SMTP_SECURITY"
ENV_SMTP_USERNAME = "COMMITGUARD_SMTP_USERNAME"
ENV_SMTP_PASSWORD = "COMMITGUARD_SMTP_PASSWORD"  # noqa: S105 - variable name
ENV_SMTP_PASSWORD_FILE = "COMMITGUARD_SMTP_PASSWORD_FILE"  # noqa: S105 - variable name
ENV_SMTP_FROM = "COMMITGUARD_SMTP_FROM"
ENV_SIGNING_KEY = "COMMITGUARD_NOTIFICATION_SIGNING_KEY"
ENV_SIGNING_KEY_FILE = "COMMITGUARD_NOTIFICATION_SIGNING_KEY_FILE"
ENV_RETENTION_DAYS = "COMMITGUARD_NOTIFICATION_RETENTION_DAYS"
ENV_DASHBOARD_URL = "COMMITGUARD_DASHBOARD_URL"
ENV_ENVIRONMENT = "COMMITGUARD_ENV"

MIN_SIGNING_KEY_CHARS = 32
MAX_SECRET_BYTES = 4096
DEFAULT_RETENTION_DAYS = 90


class NotificationMode(StrEnum):
    OFF = "off"
    DELIVER = "deliver"
    TEST = "test"


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    host: str
    port: int
    security: str
    sender: str
    username: str | None = None
    password: Secret | None = None


@dataclass(frozen=True, slots=True)
class NotificationSettings:
    mode: NotificationMode = NotificationMode.OFF
    smtp: SmtpSettings | None = None
    signing_key: Secret | None = None
    retention: timedelta = timedelta(days=DEFAULT_RETENTION_DAYS)
    dashboard_origin: str | None = None
    production: bool = True

    @property
    def email_available(self) -> bool:
        return self.mode is NotificationMode.TEST or (
            self.mode is NotificationMode.DELIVER and self.smtp is not None
        )

    @property
    def webhook_available(self) -> bool:
        return self.mode is NotificationMode.TEST or (
            self.mode is NotificationMode.DELIVER and self.signing_key is not None
        )


def _secret(env: Mapping[str, str], value_var: str, file_var: str) -> Secret | None:
    value, path = env.get(value_var), env.get(file_var)
    if value and path:
        raise AppConfigurationError(f"set only one of {value_var} and {file_var}")
    if path:
        try:
            value = read_bytes_limited(Path(path), max_bytes=MAX_SECRET_BYTES).decode().strip()
        except (OSError, UnicodeDecodeError, ValueError):
            raise AppConfigurationError(f"{file_var} could not be read") from None
    if not value:
        return None
    secret = Secret(value)
    register_secret(secret)
    return secret


def load_notification_settings(env: Mapping[str, str] | None = None) -> NotificationSettings:
    source = os.environ if env is None else env
    environment = source.get(ENV_ENVIRONMENT, "production")
    raw_mode = source.get(ENV_MODE, NotificationMode.OFF.value)
    try:
        mode = NotificationMode(raw_mode)
    except ValueError:
        raise AppConfigurationError(f"{ENV_MODE} must be off, deliver or test") from None
    if environment == "test" and mode is NotificationMode.DELIVER:
        mode = NotificationMode.TEST  # a test environment never sends real notifications
    production = environment == "production"

    smtp = None
    host = source.get(ENV_SMTP_HOST, "").strip()
    if host:
        sender = source.get(ENV_SMTP_FROM, "").strip()
        if "@" not in sender or any(c in sender for c in "\r\n<>"):
            raise AppConfigurationError(f"{ENV_SMTP_FROM} must be a plain sender address")
        port_raw = source.get(ENV_SMTP_PORT, "587")
        if not port_raw.isdigit() or not 0 < int(port_raw) < 65536:
            raise AppConfigurationError(f"{ENV_SMTP_PORT} must be a TCP port")
        security = source.get(ENV_SMTP_SECURITY, "starttls")
        if security not in ("starttls", "tls", "none"):
            raise AppConfigurationError(f"{ENV_SMTP_SECURITY} must be starttls, tls or none")
        if security == "none" and (production or host not in ("localhost", "127.0.0.1")):
            raise AppConfigurationError(
                f"{ENV_SMTP_SECURITY}=none is only allowed for localhost outside production"
            )
        smtp = SmtpSettings(
            host=host,
            port=int(port_raw),
            security=security,
            sender=sender,
            username=source.get(ENV_SMTP_USERNAME) or None,
            password=_secret(source, ENV_SMTP_PASSWORD, ENV_SMTP_PASSWORD_FILE),
        )

    signing_key = _secret(source, ENV_SIGNING_KEY, ENV_SIGNING_KEY_FILE)
    if signing_key is not None and len(signing_key) < MIN_SIGNING_KEY_CHARS:
        raise AppConfigurationError(
            f"{ENV_SIGNING_KEY} must be at least {MIN_SIGNING_KEY_CHARS} characters"
        )
    if mode is NotificationMode.TEST and signing_key is None:
        signing_key = Secret(secrets.token_hex(32))  # ephemeral: test deliveries only

    retention_raw = source.get(ENV_RETENTION_DAYS, str(DEFAULT_RETENTION_DAYS))
    if not retention_raw.isdigit() or not 1 <= int(retention_raw) <= 3650:
        raise AppConfigurationError(f"{ENV_RETENTION_DAYS} must be between 1 and 3650")
    origin = source.get(ENV_DASHBOARD_URL, "").strip().rstrip("/") or None
    return NotificationSettings(
        mode=mode,
        smtp=smtp,
        signing_key=signing_key,
        retention=timedelta(days=int(retention_raw)),
        dashboard_origin=origin,
        production=production,
    )
