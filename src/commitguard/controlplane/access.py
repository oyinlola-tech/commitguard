"""Roles, permissions and the access scope of a signed-in user.

Tenant model
============

* A **tenant** (organisation) is a GitHub account - an organisation or a user -
  identified by its immutable numeric account ID. It owns one GitHub App
  installation (GitHub allows one per account), whose repositories, scans,
  violations and audit events belong to it.
* A **member** is a GitHub user with a CommitGuard role in that tenant. Roles
  are granted in CommitGuard (``commitguard dashboard members grant`` or by an
  owner in the dashboard); GitHub does not grant them.
* Access needs both: a role in CommitGuard *and* access in GitHub. At sign-in
  GitHub reports which installations and repositories the user can see
  (``GET /user/installations``, ``GET /user/installations/{id}/repositories``);
  CommitGuard stores that list with the session and never shows a repository
  the user could not open on GitHub, whatever their CommitGuard role.

Roles
=====

==================  =========================================================
Role                Adds
==================  =========================================================
``viewer``          read repositories, scans, violations, policies, rules
``security_manager`` acknowledge violations, request re-scans, read the audit log
``admin``           change organisation policy, stop/resume monitoring a
                    repository, refresh enforcement status, sync
                    installation repositories, list members
``owner``           grant, change and remove member roles
==================  =========================================================

Each role includes every permission of the roles above it. The owner of a
personal (user-account) installation is always its ``owner``.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Permission(StrEnum):
    REPOSITORIES_READ = "repositories:read"
    REPOSITORIES_MANAGE = "repositories:manage"
    SCANS_READ = "scans:read"
    SCANS_TRIGGER = "scans:trigger"
    VIOLATIONS_READ = "violations:read"
    VIOLATIONS_MANAGE = "violations:manage"
    POLICIES_READ = "policies:read"
    POLICIES_WRITE = "policies:write"
    RULES_READ = "rules:read"
    AUDIT_READ = "audit:read"
    GITHUB_MANAGE = "github:manage"
    MEMBERS_READ = "members:read"
    MEMBERS_MANAGE = "members:manage"


class Role(StrEnum):
    VIEWER = "viewer"
    SECURITY_MANAGER = "security_manager"
    ADMIN = "admin"
    OWNER = "owner"

    @property
    def rank(self) -> int:
        return list(Role).index(self)

    @property
    def permissions(self) -> frozenset[Permission]:
        return ROLE_PERMISSIONS[self]


_VIEWER = frozenset(
    {
        Permission.REPOSITORIES_READ,
        Permission.SCANS_READ,
        Permission.VIOLATIONS_READ,
        Permission.POLICIES_READ,
        Permission.RULES_READ,
    }
)
_SECURITY_MANAGER = _VIEWER | {
    Permission.VIOLATIONS_MANAGE,
    Permission.SCANS_TRIGGER,
    Permission.AUDIT_READ,
}
_ADMIN = _SECURITY_MANAGER | {
    Permission.POLICIES_WRITE,
    Permission.REPOSITORIES_MANAGE,
    Permission.GITHUB_MANAGE,
    Permission.MEMBERS_READ,
}
_OWNER = _ADMIN | {Permission.MEMBERS_MANAGE}

ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = {
    Role.VIEWER: _VIEWER,
    Role.SECURITY_MANAGER: frozenset(_SECURITY_MANAGER),
    Role.ADMIN: frozenset(_ADMIN),
    Role.OWNER: frozenset(_OWNER),
}


@dataclass(frozen=True, slots=True)
class AccessScope:
    """What one session may see for one permission.

    ``installation_ids`` are installations whose account grants the permission
    to the user *and* that GitHub reported as accessible at sign-in.
    ``session_hash`` keys the per-session list of repositories GitHub reported;
    repository-level rows are only visible when listed there.
    """

    session_hash: str
    installation_ids: tuple[int, ...]

    @property
    def empty(self) -> bool:
        return not self.installation_ids

    def placeholders(self) -> str:
        """``?, ?, ?`` for the installation IDs (the IDs are bound as parameters)."""
        return ", ".join("?" for _ in self.installation_ids)


@dataclass(frozen=True, slots=True)
class Membership:
    account_id: int
    account_login: str
    account_type: str
    role: Role
    implicit: bool = False  # owner of a personal installation


@dataclass(frozen=True, slots=True)
class Principal:
    """A signed-in user, as established by the session for one request."""

    user_id: int
    login: str
    session_hash: str
    session_public_id: str
    authenticated_at: datetime
    expires_at: datetime
    memberships: Mapping[int, Membership] = field(default_factory=dict)
    # installation_id -> account_id, for installations GitHub reported at sign-in
    installations: Mapping[int, int] = field(default_factory=dict)

    def role_in(self, account_id: int) -> Role | None:
        membership = self.memberships.get(account_id)
        return membership.role if membership else None

    def can(self, permission: Permission, account_id: int) -> bool:
        role = self.role_in(account_id)
        return role is not None and permission in role.permissions

    def accounts_with(self, permission: Permission) -> tuple[int, ...]:
        return tuple(
            sorted(a for a, m in self.memberships.items() if permission in m.role.permissions)
        )

    def scope(self, permission: Permission, *, account_id: int | None = None) -> AccessScope:
        accounts = set(self.accounts_with(permission))
        if account_id is not None:
            accounts &= {account_id}
        installations = tuple(
            sorted(i for i, account in self.installations.items() if account in accounts)
        )
        return AccessScope(session_hash=self.session_hash, installation_ids=installations)

    def permissions_for(self, account_id: int) -> frozenset[Permission]:
        role = self.role_in(account_id)
        return role.permissions if role else frozenset()


def highest_role(roles: Iterable[Role]) -> Role | None:
    return max(roles, key=lambda role: role.rank, default=None)
