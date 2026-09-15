"""GitHub App authentication.

::

    App ID + private key --(RS256 JWT, 9 min)--> POST /app/installations/{id}/access_tokens
                                                  (repository_ids=[one repo], least-privilege
                                                   permissions)
                          <-- installation token (expires after ~1 hour)

* The private key is parsed once at start-up; a malformed, encrypted, non-RSA
  or short key fails closed with a message that never contains key material.
* JWTs and installation tokens are :class:`~commitguard.security.secrets.Secret`
  values registered for redaction; they are kept in memory only, never
  persisted, and dropped when they are near expiry, when GitHub rejects them,
  or when the installation or repository is removed.
* Every installation token is down-scoped to the single repository being
  scanned and to :data:`~commitguard.github.permissions.REQUIRED_PERMISSIONS`;
  GitHub refuses to mint it if the installation cannot access that repository,
  which makes the token request itself an authorization check.
"""

import base64
import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from commitguard.github.client import GitHubClient, InstallationTokenGrant
from commitguard.github.errors import (
    AuthenticationError,
    AuthorizationError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubUnauthorizedError,
    GitHubValidationError,
    InsufficientPermissionsError,
)
from commitguard.github.permissions import REQUIRED_PERMISSIONS, missing_permissions
from commitguard.security.secrets import Secret, default_redactor, register_secret

MIN_RSA_KEY_BITS = 2048
JWT_BACKDATE_SECONDS = 60  # tolerate clock drift between this host and GitHub
JWT_LIFETIME_SECONDS = 540  # GitHub allows at most 10 minutes
JWT_REUSE_MARGIN_SECONDS = 60
TOKEN_EXPIRY_MARGIN = timedelta(minutes=5)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class AppCredentials:
    """The App ID and parsed private key. Creates short-lived JWTs."""

    def __init__(
        self, app_id: int, private_key_pem: Secret, *, clock: Callable[[], float] = time.time
    ) -> None:
        if not isinstance(app_id, int) or app_id <= 0:
            raise AuthenticationError("GitHub App ID must be a positive integer")
        try:
            key = serialization.load_pem_private_key(
                private_key_pem.reveal().encode("utf-8"), password=None
            )
        except Exception:  # noqa: BLE001 - never chain: parser errors could echo input
            raise AuthenticationError(
                "GitHub App private key is malformed or encrypted "
                "(expected an unencrypted PEM RSA private key)"
            ) from None
        if not isinstance(key, rsa.RSAPrivateKey):
            raise AuthenticationError("GitHub App private key must be an RSA key")
        if key.key_size < MIN_RSA_KEY_BITS:
            raise AuthenticationError(
                f"GitHub App private key must be at least {MIN_RSA_KEY_BITS} bits"
            )
        self.app_id = app_id
        self._key = key
        self._clock = clock
        self._lock = threading.Lock()
        self._jwt: Secret | None = None
        self._jwt_expires = 0.0

    def __repr__(self) -> str:
        return f"AppCredentials(app_id={self.app_id})"

    def __reduce__(self) -> NoReturn:
        raise TypeError("AppCredentials cannot be pickled")

    def create_jwt(self) -> Secret:
        """A JWT for App-level API calls, reused until shortly before it expires."""
        now = self._clock()
        with self._lock:
            if self._jwt is not None and now < self._jwt_expires - JWT_REUSE_MARGIN_SECONDS:
                return self._jwt
            issued = int(now) - JWT_BACKDATE_SECONDS
            expires = int(now) + JWT_LIFETIME_SECONDS
            header = _b64url(
                json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode()
            )
            claims = {"iat": issued, "exp": expires, "iss": str(self.app_id)}
            payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
            signing_input = f"{header}.{payload}".encode("ascii")
            signature = self._key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
            token = Secret(f"{header}.{payload}.{_b64url(signature)}")
            register_secret(token)
            if self._jwt is not None:
                default_redactor().forget(self._jwt)
            self._jwt, self._jwt_expires = token, float(expires)
            return token


@dataclass(frozen=True, slots=True)
class InstallationToken:
    installation_id: int
    repository_id: int | None
    token: Secret
    expires_at: datetime
    permissions: Mapping[str, str]

    def usable_at(self, now: datetime) -> bool:
        return now < self.expires_at - TOKEN_EXPIRY_MARGIN


class InstallationTokenProvider:
    """Mints and caches down-scoped installation tokens (memory only)."""

    def __init__(
        self,
        credentials: AppCredentials,
        client: GitHubClient,
        *,
        permissions: Mapping[str, str] = REQUIRED_PERMISSIONS,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._credentials = credentials
        self._client = client
        self._permissions = dict(permissions)
        self._now = now
        self._lock = threading.Lock()
        self._cache: dict[tuple[int, int | None], InstallationToken] = {}

    def token(self, installation_id: int, repository_id: int | None) -> InstallationToken:
        """A token for one repository (or, with ``None``, the whole installation)."""
        key = (int(installation_id), None if repository_id is None else int(repository_id))
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and cached.usable_at(self._now()):
                return cached
        grant = self._mint(*key)
        token = InstallationToken(
            installation_id=key[0],
            repository_id=key[1],
            token=grant.token,
            expires_at=grant.expires_at,
            permissions=dict(grant.permissions),
        )
        with self._lock:
            previous = self._cache.get(key)
            self._cache[key] = token
        if previous is not None:
            default_redactor().forget(previous.token)
        return token

    def _mint(self, installation_id: int, repository_id: int | None) -> InstallationTokenGrant:
        jwt = self._credentials.create_jwt()
        try:
            grant = self._client.create_installation_token(
                jwt,
                installation_id,
                repository_ids=None if repository_id is None else [repository_id],
                permissions=self._permissions,
            )
        except GitHubUnauthorizedError:
            raise AuthenticationError(
                "GitHub rejected the App credentials (check the App ID and private key)"
            ) from None
        except GitHubNotFoundError:
            raise AuthorizationError(
                "GitHub App installation not found (the App may have been uninstalled)"
            ) from None
        except GitHubValidationError:
            raise AuthorizationError(
                "the installation cannot access this repository with the required permissions"
            ) from None
        except GitHubForbiddenError:
            raise AuthorizationError(
                "GitHub refused an installation token (installation suspended?)"
            ) from None
        if grant.expires_at.tzinfo is None or grant.expires_at <= self._now():
            raise AuthenticationError("GitHub returned an already expired installation token")
        if repository_id is not None and grant.repository_ids not in ((), (repository_id,)):
            raise AuthorizationError("installation token is not scoped to the requested repository")
        missing = missing_permissions(grant.permissions, self._permissions)
        if missing:
            raise InsufficientPermissionsError(missing)
        return grant

    def invalidate(self, installation_id: int, repository_id: int | None = None) -> None:
        """Forget cached tokens for an installation (or one of its repositories)."""
        with self._lock:
            keys = [
                k
                for k in self._cache
                if k[0] == installation_id and (repository_id is None or k[1] == repository_id)
            ]
            dropped = [self._cache.pop(k) for k in keys]
        for token in dropped:
            default_redactor().forget(token.token)
