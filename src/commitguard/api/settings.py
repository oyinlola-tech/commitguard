"""Dashboard configuration from the environment.

======================================================  ======================================
Variable                                                Meaning
======================================================  ======================================
``COMMITGUARD_DASHBOARD_URL``                           public origin of the dashboard, e.g.
                                                        ``https://commitguard.example.com``;
                                                        enables the dashboard and its API
``COMMITGUARD_GITHUB_CLIENT_ID``                        the GitHub App's client ID
``COMMITGUARD_GITHUB_CLIENT_SECRET``                    the GitHub App's client secret, *or*
``COMMITGUARD_GITHUB_CLIENT_SECRET_FILE``               a file containing it (preferred)
``COMMITGUARD_DASHBOARD_STATIC_DIR``                    optional: built dashboard (``web/dist``)
                                                        served by the same process
``COMMITGUARD_DASHBOARD_ALLOWED_ORIGINS``               optional: comma-separated extra origins
                                                        allowed to call the API with credentials
``COMMITGUARD_ENV``                                     ``production`` (default),
                                                        ``development`` or ``test``
======================================================  ======================================

Environments differ only where it matters:

* ``production`` requires an ``https://`` dashboard URL and sends
  ``Strict-Transport-Security``;
* ``development`` and ``test`` also accept ``http://localhost`` and
  ``http://127.0.0.1`` URLs (browsers still honour ``Secure`` cookies there)
  and omit HSTS so a local HTTP setup is not pinned to HTTPS.

Every other security control - cookie flags, CSRF, CORS allow-list, CSP,
authorization - is identical in all environments. Test suites generate their
own keys and secrets; production credentials are never needed to run them.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from commitguard.github.errors import AppConfigurationError
from commitguard.security.secrets import Secret, register_secret
from commitguard.utils.filesystem import read_bytes_limited

ENV_DASHBOARD_URL = "COMMITGUARD_DASHBOARD_URL"
ENV_CLIENT_ID = "COMMITGUARD_GITHUB_CLIENT_ID"
ENV_CLIENT_SECRET = "COMMITGUARD_GITHUB_CLIENT_SECRET"  # noqa: S105 - variable name
ENV_CLIENT_SECRET_FILE = "COMMITGUARD_GITHUB_CLIENT_SECRET_FILE"  # noqa: S105 - variable name
ENV_STATIC_DIR = "COMMITGUARD_DASHBOARD_STATIC_DIR"
ENV_ALLOWED_ORIGINS = "COMMITGUARD_DASHBOARD_ALLOWED_ORIGINS"
ENV_ENVIRONMENT = "COMMITGUARD_ENV"

CALLBACK_PATH = "/api/v1/auth/callback"
MAX_CLIENT_SECRET_BYTES = 4096


class Environment(StrEnum):
    PRODUCTION = "production"
    DEVELOPMENT = "development"
    TEST = "test"


@dataclass(frozen=True, slots=True)
class DashboardSettings:
    origin: str  # scheme://host[:port], no path
    client_id: str
    client_secret: Secret
    environment: Environment = Environment.PRODUCTION
    static_dir: Path | None = None
    allowed_origins: tuple[str, ...] = ()

    @property
    def redirect_uri(self) -> str:
        return self.origin + CALLBACK_PATH

    @property
    def https(self) -> bool:
        return self.origin.startswith("https://")

    @property
    def cors_origins(self) -> frozenset[str]:
        return frozenset(self.allowed_origins)

    @property
    def trusted_origins(self) -> frozenset[str]:
        return frozenset((self.origin, *self.allowed_origins))


def normalize_origin(value: str, environment: Environment, variable: str) -> str:
    parts = urlsplit(value.strip())
    local = parts.hostname in ("localhost", "127.0.0.1")
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise AppConfigurationError(f"{variable} must be an http(s) origin")
    if parts.scheme == "http" and not (local and environment is not Environment.PRODUCTION):
        raise AppConfigurationError(
            f"{variable} must use https (http is only accepted for localhost outside production)"
        )
    if parts.username or parts.password or parts.query or parts.fragment:
        raise AppConfigurationError(f"{variable} must not contain credentials, query or fragment")
    if parts.path not in ("", "/"):
        raise AppConfigurationError(f"{variable} must be an origin without a path")
    if value.strip() == "*":
        raise AppConfigurationError(f"{variable} must name explicit origins")
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname.lower()}{port}"


def _client_secret(env: Mapping[str, str]) -> Secret:
    value = env.get(ENV_CLIENT_SECRET)
    path = env.get(ENV_CLIENT_SECRET_FILE)
    if value and path:
        raise AppConfigurationError(
            f"set only one of {ENV_CLIENT_SECRET} and {ENV_CLIENT_SECRET_FILE}"
        )
    if path:
        try:
            raw = read_bytes_limited(Path(path), max_bytes=MAX_CLIENT_SECRET_BYTES)
            value = raw.decode("utf-8").strip()
        except (OSError, UnicodeDecodeError, ValueError):
            raise AppConfigurationError(f"{ENV_CLIENT_SECRET_FILE} could not be read") from None
    if not value or len(value) < 16:
        raise AppConfigurationError(
            f"{ENV_CLIENT_SECRET} (or {ENV_CLIENT_SECRET_FILE}) is not set or too short"
        )
    secret = Secret(value)
    register_secret(secret)
    return secret


def dashboard_enabled(env: Mapping[str, str] | None = None) -> bool:
    return bool((os.environ if env is None else env).get(ENV_DASHBOARD_URL))


def load_dashboard_settings(env: Mapping[str, str] | None = None) -> DashboardSettings:
    source = os.environ if env is None else env
    raw_environment = source.get(ENV_ENVIRONMENT, Environment.PRODUCTION.value)
    try:
        environment = Environment(raw_environment)
    except ValueError:
        raise AppConfigurationError(
            f"{ENV_ENVIRONMENT} must be production, development or test"
        ) from None
    url = source.get(ENV_DASHBOARD_URL)
    if not url:
        raise AppConfigurationError(f"{ENV_DASHBOARD_URL} is not set")
    origin = normalize_origin(url, environment, ENV_DASHBOARD_URL)
    client_id = source.get(ENV_CLIENT_ID, "")
    if not (0 < len(client_id) <= 64) or not client_id.replace(".", "").isalnum():
        raise AppConfigurationError(f"{ENV_CLIENT_ID} is not set or invalid")
    static_raw = source.get(ENV_STATIC_DIR)
    static_dir = None
    if static_raw:
        static_dir = Path(static_raw)
        if not static_dir.is_absolute() or not (static_dir / "index.html").is_file():
            raise AppConfigurationError(
                f"{ENV_STATIC_DIR} must be an absolute path to a built dashboard (index.html)"
            )
    allowed = tuple(
        normalize_origin(item, environment, ENV_ALLOWED_ORIGINS)
        for item in source.get(ENV_ALLOWED_ORIGINS, "").split(",")
        if item.strip()
    )
    return DashboardSettings(
        origin=origin,
        client_id=client_id,
        client_secret=_client_secret(source),
        environment=environment,
        static_dir=static_dir,
        allowed_origins=allowed,
    )
