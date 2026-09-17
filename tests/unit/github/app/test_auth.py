"""GitHub App authentication: private key handling, JWTs and installation tokens."""

import base64
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from commitguard.github.auth import AppCredentials, InstallationTokenProvider
from commitguard.github.client import InstallationTokenGrant
from commitguard.github.errors import (
    AuthenticationError,
    AuthorizationError,
    GitHubNotFoundError,
    GitHubUnauthorizedError,
    GitHubValidationError,
    InsufficientPermissionsError,
)
from commitguard.github.permissions import REQUIRED_PERMISSIONS
from commitguard.github.settings import (
    AppConfigurationError,
    load_settings,
    private_key_file_too_open,
    read_app_id,
    read_private_key,
    read_webhook_secret,
)
from commitguard.security.secrets import Secret


def _b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_jwt_is_rs256_signed_with_bounded_lifetime(
    private_key_pem: Secret, rsa_private_key: rsa.RSAPrivateKey
) -> None:
    now = 1_800_000_000.0
    credentials = AppCredentials(123, private_key_pem, clock=lambda: now)
    token = credentials.create_jwt().reveal()
    header, payload, signature = token.split(".")
    assert json.loads(_b64decode(header)) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(_b64decode(payload))
    assert claims == {"iat": int(now) - 60, "exp": int(now) + 540, "iss": "123"}
    assert claims["exp"] - claims["iat"] <= 600  # GitHub's maximum
    rsa_private_key.public_key().verify(
        _b64decode(signature), f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )


def test_jwt_is_reused_until_near_expiry(private_key_pem: Secret) -> None:
    clock = [1_800_000_000.0]
    credentials = AppCredentials(123, private_key_pem, clock=lambda: clock[0])
    first = credentials.create_jwt()
    clock[0] += 300
    assert credentials.create_jwt() is first
    clock[0] += 200  # within a minute of expiry
    assert credentials.create_jwt() is not first


@pytest.mark.parametrize(
    "pem",
    [
        "",
        "not a key",
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n"
        "-----END RSA PRIVATE KEY-----\n",
        "-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----\n",
    ],
    ids=["empty", "garbage", "truncated", "public"],
)
def test_malformed_private_key_fails_closed_without_leaking(pem: str) -> None:
    with pytest.raises(AuthenticationError) as info:
        AppCredentials(123, Secret(pem))
    message = str(info.value)
    assert "malformed" in message
    assert "MIIB" not in message
    assert "AAAA" not in message
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


def test_encrypted_non_rsa_and_short_keys_are_rejected(rsa_private_key: rsa.RSAPrivateKey) -> None:
    encrypted = rsa_private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"passphrase"),
    )
    with pytest.raises(AuthenticationError, match="malformed or encrypted"):
        AppCredentials(1, Secret(encrypted.decode()))
    ec_key = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    with pytest.raises(AuthenticationError, match="RSA"):
        AppCredentials(1, Secret(ec_key.decode()))
    weak = rsa.generate_private_key(65537, 1024)  # noqa: S505 - must be rejected
    short = weak.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    with pytest.raises(AuthenticationError, match="2048"):
        AppCredentials(1, Secret(short.decode()))


def test_credentials_repr_and_pickle_do_not_expose_key(private_key_pem: Secret) -> None:
    credentials = AppCredentials(99, private_key_pem)
    assert repr(credentials) == "AppCredentials(app_id=99)"
    with pytest.raises(TypeError):
        __import__("pickle").dumps(credentials)


@dataclass
class ScriptedClient:
    outcomes: list[Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create_installation_token(
        self,
        jwt: Secret,
        installation_id: int,
        *,
        repository_ids: Sequence[int] | None,
        permissions: Mapping[str, str],
    ) -> InstallationTokenGrant:
        self.calls.append(
            {"installation": installation_id, "repos": repository_ids, "perms": dict(permissions)}
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, InstallationTokenGrant)
        return outcome


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def grant(
    token: str = "ghs_" + "a" * 36,
    *,
    expires: timedelta = timedelta(hours=1),
    permissions: Mapping[str, str] = REQUIRED_PERMISSIONS,
    repos: tuple[int, ...] = (5001,),
) -> InstallationTokenGrant:
    return InstallationTokenGrant(Secret(token), NOW + expires, dict(permissions), repos)


def provider(
    private_key_pem: Secret, client: ScriptedClient, now: list[datetime]
) -> InstallationTokenProvider:
    return InstallationTokenProvider(
        AppCredentials(123, private_key_pem),
        client,
        now=lambda: now[0],  # type: ignore[arg-type]
    )


def test_installation_tokens_are_scoped_cached_and_refreshed(private_key_pem: Secret) -> None:
    now = [NOW]
    client = ScriptedClient(
        [grant("ghs_" + "a" * 36), grant("ghs_" + "b" * 36, expires=timedelta(hours=2))]
    )
    tokens = provider(private_key_pem, client, now)
    first = tokens.token(42, 5001)
    assert client.calls == [
        {"installation": 42, "repos": [5001], "perms": dict(REQUIRED_PERMISSIONS)}
    ]
    assert tokens.token(42, 5001) is first  # cached
    now[0] = NOW + timedelta(minutes=56)  # inside the expiry margin
    second = tokens.token(42, 5001)
    assert second.token.reveal().startswith("ghs_b")
    assert len(client.calls) == 2


def test_invalidate_drops_cached_tokens(private_key_pem: Secret) -> None:
    client = ScriptedClient([grant(), grant("ghs_" + "c" * 36)])
    tokens = provider(private_key_pem, client, [NOW])
    tokens.token(42, 5001)
    tokens.invalidate(42)
    assert tokens.token(42, 5001).token.reveal().startswith("ghs_c")


@pytest.mark.parametrize(
    ("error", "expected", "text"),
    [
        (GitHubUnauthorizedError("op", status=401), AuthenticationError, "App credentials"),
        (GitHubNotFoundError("op", status=404), AuthorizationError, "uninstalled"),
        (GitHubValidationError("op", status=422), AuthorizationError, "cannot access"),
    ],
)
def test_token_errors_are_normalised(
    private_key_pem: Secret, error: Exception, expected: type[Exception], text: str
) -> None:
    tokens = provider(private_key_pem, ScriptedClient([error]), [NOW])
    with pytest.raises(expected, match=text):
        tokens.token(42, 5001)


def test_reduced_permissions_are_detected(private_key_pem: Secret) -> None:
    reduced = {"contents": "read", "metadata": "read", "pull_requests": "read", "checks": "read"}
    tokens = provider(private_key_pem, ScriptedClient([grant(permissions=reduced)]), [NOW])
    with pytest.raises(InsufficientPermissionsError) as info:
        tokens.token(42, 5001)
    assert info.value.missing == {"checks": "write"}


def test_token_for_other_repository_or_expired_is_rejected(private_key_pem: Secret) -> None:
    tokens = provider(private_key_pem, ScriptedClient([grant(repos=(9999,))]), [NOW])
    with pytest.raises(AuthorizationError, match="not scoped"):
        tokens.token(42, 5001)
    tokens = provider(
        private_key_pem, ScriptedClient([grant(expires=timedelta(seconds=-1))]), [NOW]
    )
    with pytest.raises(AuthenticationError, match="expired"):
        tokens.token(42, 5001)


# --------------------------------------------------------------------------- #
# Environment configuration
# --------------------------------------------------------------------------- #
def test_settings_from_environment(private_key_pem: Secret, tmp_path: Path) -> None:
    key_file = tmp_path / "app.pem"
    key_file.write_text(private_key_pem.reveal())
    key_file.chmod(0o600)
    env = {
        "COMMITGUARD_GITHUB_APP_ID": "123",
        "COMMITGUARD_GITHUB_PRIVATE_KEY_FILE": str(key_file),
        "COMMITGUARD_GITHUB_WEBHOOK_SECRET": "0123456789abcdef0123",
        "COMMITGUARD_APP_DATA_DIR": str(tmp_path / "data"),
        "COMMITGUARD_APP_RETENTION_DAYS": "7",
    }
    settings = load_settings(env)
    assert settings.app_id == 123
    assert settings.retention == timedelta(days=7)
    assert "BEGIN" not in repr(settings)
    assert "0123456789abcdef" not in repr(settings)
    assert not private_key_file_too_open(env)
    key_file.chmod(0o644)
    # Group/other permission bits exist only on POSIX; elsewhere the check cannot apply.
    assert private_key_file_too_open(env) is (os.name == "posix")


def test_escaped_newlines_in_private_key_variable(private_key_pem: Secret) -> None:
    single_line = private_key_pem.reveal().replace("\n", "\\n")
    key = read_private_key({"COMMITGUARD_GITHUB_PRIVATE_KEY": single_line})
    AppCredentials(1, key)


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "COMMITGUARD_GITHUB_APP_ID is not set"),
        ({"COMMITGUARD_GITHUB_APP_ID": "12a"}, "positive integer"),
        ({"COMMITGUARD_GITHUB_APP_ID": "-5"}, "positive integer"),
    ],
)
def test_app_id_errors(env: dict[str, str], message: str) -> None:
    with pytest.raises(AppConfigurationError, match=message):
        read_app_id(env)


def test_secret_configuration_errors_name_variables_not_values(tmp_path: Path) -> None:
    with pytest.raises(AppConfigurationError, match="set only one"):
        read_private_key(
            {"COMMITGUARD_GITHUB_PRIVATE_KEY": "x", "COMMITGUARD_GITHUB_PRIVATE_KEY_FILE": "/y"}
        )
    with pytest.raises(AppConfigurationError, match="does not exist"):
        read_private_key({"COMMITGUARD_GITHUB_PRIVATE_KEY_FILE": str(tmp_path / "missing.pem")})
    with pytest.raises(AppConfigurationError) as info:
        read_webhook_secret({"COMMITGUARD_GITHUB_WEBHOOK_SECRET": "short-secret"})
    assert "short-secret" not in str(info.value)
    with pytest.raises(AppConfigurationError, match="is not set"):
        read_webhook_secret({})
