"""Fixtures for GitHub App unit tests: throwaway RSA keys and secrets."""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from commitguard.security.secrets import Secret


@pytest.fixture(scope="session")
def rsa_private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def private_key_pem(rsa_private_key: rsa.RSAPrivateKey) -> Secret:
    pem = rsa_private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return Secret(pem.decode("ascii"))


@pytest.fixture
def webhook_secret() -> Secret:
    return Secret("unit-test-webhook-secret-7f3a9c1e5b")
