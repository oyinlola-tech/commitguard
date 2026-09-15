"""``commitguard dashboard``: operator tasks for the dashboard control plane.

* ``members list``   - roles in an organization;
* ``members grant``  - grant or change a role (bootstraps the first owner);
* ``members revoke`` - remove a role.

The commands work on the service's state database (``COMMITGUARD_APP_DATA_DIR``)
and need no network access. Users are named by numeric GitHub user ID (find it
with ``gh api users/<login> --jq .id``): logins can be renamed and re-registered.
Every change is recorded as an audit event with the actor ``commitguard-cli``.
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from commitguard.cli.output import fail, handled_errors, info
from commitguard.exceptions.base import CommitGuardError

if TYPE_CHECKING:  # imported lazily: the CLI must not load sqlite3 for unrelated commands
    from commitguard.controlplane.members import MembershipService
    from commitguard.github.storage import SqliteStateStore

dashboard_app = typer.Typer(
    help="Dashboard control plane: organization members and roles.", no_args_is_help=True
)
members_app = typer.Typer(help="Organization members and their roles.", no_args_is_help=True)
dashboard_app.add_typer(members_app, name="members")

CLI_ACTOR_LOGIN = "commitguard-cli"
_ROLES = ("viewer", "security_manager", "admin", "owner")


def _services() -> "tuple[SqliteStateStore, MembershipService]":
    from commitguard.controlplane.members import MembershipService
    from commitguard.github.settings import ENV_DATA_DIR
    from commitguard.github.storage import DATABASE_FILENAME, SqliteStateStore
    from commitguard.services.audit import AuditService

    raw = os.environ.get(ENV_DATA_DIR)
    if not raw or not Path(raw).is_absolute():
        raise CommitGuardError(
            f"{ENV_DATA_DIR} must be set to the service's absolute data directory"
        )
    database = Path(raw) / DATABASE_FILENAME
    if not database.is_file():
        raise CommitGuardError(f"no CommitGuard state database in {ENV_DATA_DIR}")
    store = SqliteStateStore(database)
    audit = AuditService([store])
    return store, MembershipService(store, audit)


def _organization(members: "MembershipService", value: str) -> tuple[int, str, str]:
    account = (
        members.account(int(value))
        if value.isascii() and value.isdigit()
        else members.account_by_login(value)
    )
    if account is None:
        raise CommitGuardError(
            f"no GitHub App installation is known for organization {value!r} "
            "(install the App, or let the service receive its installation event first)"
        )
    return account


OrganizationOption = Annotated[
    str, typer.Option("--organization", help="Organization login or numeric account ID.")
]
UserIdOption = Annotated[int, typer.Option("--user-id", min=1, help="Numeric GitHub user ID.")]


@members_app.command("list")
def list_command(organization: OrganizationOption) -> None:
    """List the members of an organization."""
    with handled_errors():
        store, members = _services()
        try:
            account = _organization(members, organization)
            rows = members.list_members(account[0], limit=1000)
        finally:
            store.close()
    info(f"{account[1]} ({account[2]}, account {account[0]})")
    if not rows:
        info("  no members")
    for member in rows:
        label = member.login or "(not signed in yet)"
        suffix = " [implicit]" if member.implicit else ""
        info(f"  {member.user_id:<12} {label:<40} {member.role}{suffix}")


@members_app.command("grant")
def grant_command(
    organization: OrganizationOption,
    user_id: UserIdOption,
    role: Annotated[str, typer.Option("--role", help="viewer, security_manager, admin or owner.")],
    login: Annotated[
        str | None, typer.Option("--login", help="GitHub login, for display until they sign in.")
    ] = None,
) -> None:
    """Grant a role (or change an existing one)."""
    from commitguard.audit.models import Actor, ActorType
    from commitguard.controlplane.access import Role

    if role not in _ROLES:
        fail(f"--role must be one of: {', '.join(_ROLES)}")
    with handled_errors():
        store, members = _services()
        try:
            account = _organization(members, organization)
            member = members.grant(
                account_id=account[0],
                user_id=user_id,
                role=Role(role),
                actor=Actor(type=ActorType.SYSTEM, login=CLI_ACTOR_LOGIN),
                login=login,
            )
        finally:
            store.close()
    info(f"Granted {member.role} in {account[1]} to user {member.user_id}.")


@members_app.command("revoke")
def revoke_command(organization: OrganizationOption, user_id: UserIdOption) -> None:
    """Remove a member's role."""
    from commitguard.audit.models import Actor, ActorType

    with handled_errors():
        store, members = _services()
        try:
            account = _organization(members, organization)
            members.remove(
                account_id=account[0],
                user_id=user_id,
                actor=Actor(type=ActorType.SYSTEM, login=CLI_ACTOR_LOGIN),
            )
        finally:
            store.close()
    info(f"Removed user {user_id} from {account[1]}.")
