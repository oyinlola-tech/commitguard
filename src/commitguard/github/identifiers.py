"""Validated GitHub identifiers (accounts, repositories).

Repository and account names come from webhook payloads and API responses and
are untrusted. They are only ever used as data: in URL path segments (after
validation and percent-encoding), in Check Run text (after escaping) and in
logs. Numeric IDs, not names, are used for authorization, storage keys and
file-system paths, so a rename or a crafted name cannot confuse tenants.
"""

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from commitguard.exceptions.base import UnsafeInputError

# GitHub logins: alphanumerics and single hyphens, max 39 chars. Bot accounts
# (e.g. "dependabot[bot]") only appear as senders, never as repository owners.
_LOGIN_RE = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}\Z")
_REPO_NAME_RE = re.compile(r"\A[A-Za-z0-9._-]{1,100}\Z")
MAX_GITHUB_ID = 2**53


def validate_login(value: str) -> str:
    if not isinstance(value, str) or not _LOGIN_RE.match(value):
        raise UnsafeInputError("invalid GitHub account login")
    return value


def validate_repository_name(value: str) -> str:
    if not isinstance(value, str) or not _REPO_NAME_RE.match(value) or value in (".", ".."):
        raise UnsafeInputError("invalid GitHub repository name")
    if value.lower().endswith(".git"):
        raise UnsafeInputError("invalid GitHub repository name")
    return value


def split_full_name(full_name: str) -> tuple[str, str]:
    if not isinstance(full_name, str) or full_name.count("/") != 1:
        raise UnsafeInputError("invalid GitHub repository full name")
    owner, name = full_name.split("/")
    return validate_login(owner), validate_repository_name(name)


class AccountType(StrEnum):
    USER = "User"
    ORGANIZATION = "Organization"
    ENTERPRISE = "Enterprise"


class GitHubAccount(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int = Field(gt=0, lt=MAX_GITHUB_ID)
    login: str
    type: AccountType

    @field_validator("login")
    @classmethod
    def _login(cls, value: str) -> str:
        return validate_login(value)


class RepositoryRef(BaseModel):
    """A repository identified by its immutable numeric ID plus its current name."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int = Field(gt=0, lt=MAX_GITHUB_ID)
    owner: str
    name: str

    @field_validator("owner")
    @classmethod
    def _owner(cls, value: str) -> str:
        return validate_login(value)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return validate_repository_name(value)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @classmethod
    def from_full_name(cls, repository_id: int, full_name: str) -> "RepositoryRef":
        owner, name = split_full_name(full_name)
        return cls(id=repository_id, owner=owner, name=name)
