import pytest

from commitguard.exceptions.base import UnsafeInputError
from commitguard.security.hashing import fingerprint, sha256_hex
from commitguard.security.validation import (
    is_git_sha,
    validate_git_config_key,
    validate_identifier,
    validate_revision,
)


@pytest.mark.parametrize("rev", ["HEAD", "main", "origin/main..HEAD", "a" * 40, "v1.0^{commit}"])
def test_valid_revisions(rev: str) -> None:
    assert validate_revision(rev) == rev


@pytest.mark.parametrize(
    "rev",
    ["", "--output=/tmp/pwned", "-p", "HEAD\n--all", "HEAD\x00", "x" * 1000],
)
def test_unsafe_revisions_are_rejected(rev: str) -> None:
    with pytest.raises(UnsafeInputError):
        validate_revision(rev)


def test_git_sha_detection() -> None:
    assert is_git_sha("0" * 40)
    assert is_git_sha("f" * 64)
    assert not is_git_sha("F" * 40)
    assert not is_git_sha("0" * 39)
    assert not is_git_sha("0" * 40 + "\n")


@pytest.mark.parametrize("key", ["core.hooksPath", "user.email", "remote.origin.url"])
def test_valid_git_config_keys(key: str) -> None:
    assert validate_git_config_key(key) == key


@pytest.mark.parametrize("key", ["", "--global", "core", "core.hooks path", "a.b\n"])
def test_invalid_git_config_keys(key: str) -> None:
    with pytest.raises(UnsafeInputError):
        validate_git_config_key(key)


def test_identifier_validation() -> None:
    assert validate_identifier("ai_coauthor") == "ai_coauthor"
    with pytest.raises(UnsafeInputError):
        validate_identifier("ai-coauthor")


def test_hashing() -> None:
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert fingerprint(["ab", "c"]) != fingerprint(["a", "bc"])
    assert fingerprint(["a"]) == fingerprint(["a"])
