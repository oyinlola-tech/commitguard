"""GitHub App configuration from the environment.

=====================================================  ==========================================
Variable                                               Meaning
=====================================================  ==========================================
``COMMITGUARD_GITHUB_APP_ID``                          numeric App ID (JWT issuer)
``COMMITGUARD_GITHUB_PRIVATE_KEY``                     PEM private key text, *or*
``COMMITGUARD_GITHUB_PRIVATE_KEY_FILE``                path to the PEM file (preferred)
``COMMITGUARD_GITHUB_WEBHOOK_SECRET``                  webhook secret, *or*
``COMMITGUARD_GITHUB_WEBHOOK_SECRET_FILE``             path to a file containing it
``COMMITGUARD_APP_DATA_DIR``                           state database and metadata mirrors
``COMMITGUARD_APP_MANDATORY_POLICY_FILE``              optional mandatory (organisation) policy
``COMMITGUARD_APP_WORKERS``                            scan worker threads (default 2)
``COMMITGUARD_APP_RETENTION_DAYS``                     retention for deliveries/scans/audit (30)
``COMMITGUARD_APP_MAX_COMMITS``                        per-scan commit limit (10000)
=====================================================  ==========================================

A client ID/secret is not needed: the App never acts on behalf of a user.

Errors name the variable, never its value. Secret values are wrapped in
:class:`~commitguard.security.secrets.Secret` and registered for redaction the
moment they are read.
"""

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from commitguard.exceptions.base import UnsafeInputError
from commitguard.github.errors import AppConfigurationError
from commitguard.security.secrets import Secret, register_secret
from commitguard.services.ci import DEFAULT_CI_MAX_COMMITS
from commitguard.utils.filesystem import read_bytes_limited

ENV_APP_ID = "COMMITGUARD_GITHUB_APP_ID"
ENV_PRIVATE_KEY = "COMMITGUARD_GITHUB_PRIVATE_KEY"
ENV_PRIVATE_KEY_FILE = "COMMITGUARD_GITHUB_PRIVATE_KEY_FILE"
ENV_WEBHOOK_SECRET = "COMMITGUARD_GITHUB_WEBHOOK_SECRET"  # noqa: S105 - variable name
ENV_WEBHOOK_SECRET_FILE = "COMMITGUARD_GITHUB_WEBHOOK_SECRET_FILE"  # noqa: S105 - variable name
ENV_DATA_DIR = "COMMITGUARD_APP_DATA_DIR"
ENV_MANDATORY_POLICY_FILE = "COMMITGUARD_APP_MANDATORY_POLICY_FILE"
ENV_WORKERS = "COMMITGUARD_APP_WORKERS"
ENV_RETENTION_DAYS = "COMMITGUARD_APP_RETENTION_DAYS"
ENV_MAX_COMMITS = "COMMITGUARD_APP_MAX_COMMITS"

MAX_PRIVATE_KEY_BYTES = 32 * 1024
MAX_WEBHOOK_SECRET_BYTES = 4096
MIN_WEBHOOK_SECRET_LENGTH = 16
DEFAULT_WORKERS = 2
DEFAULT_RETENTION_DAYS = 30


@dataclass(frozen=True, slots=True)
class AppSettings:
    app_id: int
    private_key: Secret
    webhook_secret: Secret
    data_dir: Path
    mandatory_policy_file: Path | None = None
    workers: int = DEFAULT_WORKERS
    retention: timedelta = timedelta(days=DEFAULT_RETENTION_DAYS)
    max_commits: int = DEFAULT_CI_MAX_COMMITS

    def __repr__(self) -> str:  # secrets are already masked; keep it short anyway
        return f"AppSettings(app_id={self.app_id}, data_dir={self.data_dir!s})"


def _int(environ: Mapping[str, str], name: str, default: int, *, low: int, high: int) -> int:
    raw = environ.get(name)
    if raw is None or raw == "":
        return default
    if not raw.isascii() or not raw.isdigit() or not low <= int(raw) <= high:
        raise AppConfigurationError(f"{name} must be an integer between {low} and {high}")
    return int(raw)


def read_app_id(environ: Mapping[str, str]) -> int:
    raw = environ.get(ENV_APP_ID, "").strip()
    if not raw:
        raise AppConfigurationError(f"{ENV_APP_ID} is not set")
    if not raw.isascii() or not raw.isdigit() or not 0 < int(raw) < 2**53:
        raise AppConfigurationError(f"{ENV_APP_ID} must be a positive integer")
    return int(raw)


def _read_secret(
    environ: Mapping[str, str], value_var: str, file_var: str, *, max_bytes: int
) -> tuple[str, Path | None]:
    value = environ.get(value_var)
    file_name = environ.get(file_var)
    if value and file_name:
        raise AppConfigurationError(f"set only one of {value_var} and {file_var}")
    if file_name:
        path = Path(file_name)
        try:
            data = read_bytes_limited(path, max_bytes=max_bytes)
            return data.decode("utf-8"), path
        except FileNotFoundError:
            raise AppConfigurationError(
                f"{file_var} points to a file that does not exist"
            ) from None
        except (OSError, UnsafeInputError, UnicodeDecodeError):
            raise AppConfigurationError(f"{file_var} could not be read as a text file") from None
    if value:
        if len(value.encode("utf-8")) > max_bytes:
            raise AppConfigurationError(f"{value_var} is too large")
        return value, None
    raise AppConfigurationError(f"{value_var} (or {file_var}) is not set")


def read_private_key(environ: Mapping[str, str]) -> Secret:
    """The PEM text of the App private key (not yet parsed; see ``auth.AppCredentials``)."""
    text, _ = _read_secret(
        environ, ENV_PRIVATE_KEY, ENV_PRIVATE_KEY_FILE, max_bytes=MAX_PRIVATE_KEY_BYTES
    )
    if "\n" not in text and "\\n" in text:
        text = text.replace("\\n", "\n")  # single-line environment variable with escaped newlines
    secret = Secret(text.strip() + "\n")
    register_secret(secret)
    return secret


def read_webhook_secret(environ: Mapping[str, str]) -> Secret:
    text, from_file = _read_secret(
        environ, ENV_WEBHOOK_SECRET, ENV_WEBHOOK_SECRET_FILE, max_bytes=MAX_WEBHOOK_SECRET_BYTES
    )
    if from_file is not None:
        text = text.rstrip("\r\n")
    if len(text) < MIN_WEBHOOK_SECRET_LENGTH:
        raise AppConfigurationError(
            f"the webhook secret must be at least {MIN_WEBHOOK_SECRET_LENGTH} characters "
            "(use a long random value)"
        )
    secret = Secret(text)
    register_secret(secret)
    return secret


def private_key_file_too_open(environ: Mapping[str, str]) -> bool:
    """True if the key file is readable by group or others (POSIX only)."""
    name = environ.get(ENV_PRIVATE_KEY_FILE)
    if not name or os.name != "posix":
        return False
    try:
        mode = Path(name).stat().st_mode
    except OSError:
        return False
    return bool(mode & (stat.S_IRWXG | stat.S_IRWXO))


def load_settings(environ: Mapping[str, str] | None = None) -> AppSettings:
    env = os.environ if environ is None else environ
    data_dir_raw = env.get(ENV_DATA_DIR, "")
    if not data_dir_raw:
        raise AppConfigurationError(f"{ENV_DATA_DIR} is not set")
    data_dir = Path(data_dir_raw)
    if not data_dir.is_absolute():
        raise AppConfigurationError(f"{ENV_DATA_DIR} must be an absolute path")
    policy_raw = env.get(ENV_MANDATORY_POLICY_FILE, "")
    policy_file = Path(policy_raw) if policy_raw else None
    retention_days = _int(env, ENV_RETENTION_DAYS, DEFAULT_RETENTION_DAYS, low=1, high=3650)
    return AppSettings(
        app_id=read_app_id(env),
        private_key=read_private_key(env),
        webhook_secret=read_webhook_secret(env),
        data_dir=data_dir,
        mandatory_policy_file=policy_file,
        workers=_int(env, ENV_WORKERS, DEFAULT_WORKERS, low=1, high=64),
        retention=timedelta(days=retention_days),
        max_commits=_int(env, ENV_MAX_COMMITS, DEFAULT_CI_MAX_COMMITS, low=1, high=1_000_000),
    )
