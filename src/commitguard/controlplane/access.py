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
``viewer``          read repositories, scans, violations, policies, rules,
                    exceptions, the organization and its security posture;
                    receive in-app notifications about them
``security_manager`` acknowledge violations, request re-scans, read the audit log,
                    request policy exceptions
``admin``           change, publish, approve and roll back policy, manage
                    repository groups, onboarding, rollouts, scan schedules,
                    organization rules and settings, approve and revoke
                    exceptions, stop/resume monitoring a repository, refresh
                    enforcement status, sync installation repositories, list
                    members, manage organisation notification settings and webhooks
``owner``           grant, change and remove member roles; emergency policy
                    publication (bypassing approval, always audited)
==================  =========================================================

Each role includes every permission of the roles above it. The owner of a
personal (user-account) installation is always its ``owner``.

Authorization is by permission, never by role name: routes and services check
a :class:`Permission`, and the role table below is the only place that maps
roles to permissions. There is no separate ``member`` role: ``viewer`` is the
least-privileged member. Separation of duties (a policy change approved by
someone other than its author) is enforced by the policy workflow, not by roles.
"""

import json
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
    POLICIES_ROLLBACK = "policies:rollback"
    RULES_READ = "rules:read"
    AUDIT_READ = "audit:read"
    GITHUB_MANAGE = "github:manage"
    MEMBERS_READ = "members:read"
    MEMBERS_MANAGE = "members:manage"
    NOTIFICATIONS_READ = "notifications:read"
    NOTIFICATIONS_MANAGE = "notifications:manage"
    # Phase 8: organization governance.
    ORGANIZATION_READ = "organization:read"
    ORGANIZATION_MANAGE = "organization:manage"
    POLICIES_PUBLISH = "policies:publish"
    POLICIES_APPROVE = "policies:approve"
    POLICIES_EMERGENCY = "policies:emergency"
    RULES_MANAGE = "rules:manage"
    EXCEPTIONS_READ = "exceptions:read"
    EXCEPTIONS_CREATE = "exceptions:create"
    EXCEPTIONS_APPROVE = "exceptions:approve"
    EXCEPTIONS_REVOKE = "exceptions:revoke"
    SECURITY_READ = "security:read"
    SECURITY_MANAGE = "security:manage"


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
        Permission.NOTIFICATIONS_READ,
        Permission.ORGANIZATION_READ,
        Permission.EXCEPTIONS_READ,
        Permission.SECURITY_READ,
    }
)
_SECURITY_MANAGER = _VIEWER | {
    Permission.VIOLATIONS_MANAGE,
    Permission.SCANS_TRIGGER,
    Permission.AUDIT_READ,
    Permission.EXCEPTIONS_CREATE,
}
_ADMIN = _SECURITY_MANAGER | {
    Permission.POLICIES_WRITE,
    Permission.POLICIES_ROLLBACK,
    Permission.POLICIES_PUBLISH,
    Permission.POLICIES_APPROVE,
    Permission.NOTIFICATIONS_MANAGE,
    Permission.REPOSITORIES_MANAGE,
    Permission.GITHUB_MANAGE,
    Permission.MEMBERS_READ,
    Permission.ORGANIZATION_MANAGE,
    Permission.RULES_MANAGE,
    Permission.EXCEPTIONS_APPROVE,
    Permission.EXCEPTIONS_REVOKE,
    Permission.SECURITY_MANAGE,
}
_OWNER = _ADMIN | {Permission.MEMBERS_MANAGE, Permission.POLICIES_EMERGENCY}

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
    account_ids: tuple[int, ...]

    @property
    def empty(self) -> bool:
        return not self.installation_ids and not self.account_ids

    @property
    def installations_json(self) -> str:
        """The installation IDs as a JSON array, bound to ``json_each(?)`` in queries."""
        return json.dumps(list(self.installation_ids))

    @property
    def accounts_json(self) -> str:
        return json.dumps(list(self.account_ids))


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
        return AccessScope(
            session_hash=self.session_hash,
            installation_ids=installations,
            account_ids=tuple(sorted(accounts)),
        )

    def permissions_for(self, account_id: int) -> frozenset[Permission]:
        role = self.role_in(account_id)
        return role.permissions if role else frozenset()


def highest_role(roles: Iterable[Role]) -> Role | None:
    return max(roles, key=lambda role: role.rank, default=None)
