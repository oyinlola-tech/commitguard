"""Organisation members and their roles.

Roles are granted by an owner in the dashboard or by the operator with
``commitguard dashboard members grant``. A grant names a GitHub user by its
immutable numeric ID, never by login: logins can be renamed and re-registered.

Rules enforced here (not in the browser):

* an organisation always keeps at least one explicit owner once it has one,
  so the last owner cannot be demoted or removed;
* owners cannot change their own role (another owner must do it);
* the owner of a personal installation is implicit and cannot be edited.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from commitguard.audit.models import Actor, AuditEventType
from commitguard.controlplane.access import Role
from commitguard.controlplane.errors import (
    ConflictError,
    InputValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from commitguard.controlplane.views import MemberView
from commitguard.github.identifiers import MAX_GITHUB_ID, AccountType, validate_login
from commitguard.github.storage import SqliteStateStore
from commitguard.services.audit import AuditService


def _dt(value: float) -> datetime:
    return datetime.fromtimestamp(value, UTC)


class MembershipService:
    def __init__(
        self,
        store: SqliteStateStore,
        audit: AuditService,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._audit = audit
        self._now = now

    def account(self, account_id: int) -> tuple[int, str, str] | None:
        rows = self._store.query(
            "SELECT account_id, account_login, account_type FROM installations "
            "WHERE account_id = ? ORDER BY updated_at DESC LIMIT 1",
            (int(account_id),),
        )
        if not rows:
            return None
        return int(rows[0]["account_id"]), rows[0]["account_login"], rows[0]["account_type"]

    def account_by_login(self, login: str) -> tuple[int, str, str] | None:
        validate_login(login)
        rows = self._store.query(
            "SELECT account_id, account_login, account_type FROM installations "
            "WHERE account_login = ? COLLATE NOCASE ORDER BY updated_at DESC LIMIT 1",
            (login,),
        )
        if not rows:
            return None
        return int(rows[0]["account_id"]), rows[0]["account_login"], rows[0]["account_type"]

    def list_members(
        self, account_id: int, *, offset: int = 0, limit: int = 100
    ) -> list[MemberView]:
        members: list[MemberView] = []
        account = self.account(account_id)
        if account and account[2] == AccountType.USER.value and offset == 0:
            members.append(
                MemberView(
                    user_id=account_id,
                    login=account[1],
                    role=Role.OWNER.value,
                    granted_by="GitHub account owner",
                    created_at=_dt(0),
                    updated_at=_dt(0),
                    implicit=True,
                )
            )
        rows = self._store.query(
            "SELECT m.user_id, u.login, m.role, m.granted_by, m.created_at, m.updated_at "
            "FROM memberships m LEFT JOIN users u ON u.user_id = m.user_id "
            "WHERE m.account_id = ? ORDER BY m.created_at, m.user_id LIMIT ? OFFSET ?",
            (int(account_id), int(limit), int(offset)),
        )
        members.extend(
            MemberView(
                user_id=int(r["user_id"]),
                login=r["login"],
                role=r["role"],
                granted_by=r["granted_by"],
                created_at=_dt(r["created_at"]),
                updated_at=_dt(r["updated_at"]),
                implicit=False,
            )
            for r in rows
        )
        return members

    def grant(
        self,
        *,
        account_id: int,
        user_id: int,
        role: Role,
        actor: Actor,
        login: str | None = None,
    ) -> MemberView:
        if not 0 < user_id < MAX_GITHUB_ID:
            raise InputValidationError("invalid GitHub user ID", field="user_id")
        if login is not None:
            try:
                validate_login(login)
            except ValueError:
                raise InputValidationError("invalid GitHub login", field="login") from None
        account = self.account(account_id)
        if account is None:
            raise NotFoundError("No GitHub App installation is known for this account.")
        if account[2] == AccountType.USER.value and user_id == account_id:
            raise ConflictError("The owner of a personal account is always its owner.")
        if actor.id is not None and actor.id == user_id:
            raise PermissionDeniedError("You cannot change your own role.")
        now = self._now().timestamp()
        granted_by = actor.login or actor.type.value
        with self._store.transaction() as db:
            existing = db.execute(
                "SELECT role FROM memberships WHERE account_id = ? AND user_id = ?",
                (int(account_id), int(user_id)),
            ).fetchone()
            if (
                existing is not None
                and existing["role"] == Role.OWNER.value
                and role is not Role.OWNER
            ):
                self._require_other_owner(db, account_id, user_id)
            if login is not None:
                db.execute(
                    "INSERT INTO users (user_id, login, created_at) VALUES (?, ?, ?) "
                    "ON CONFLICT (user_id) DO NOTHING",
                    (int(user_id), login, now),
                )
            db.execute(
                "INSERT INTO memberships (account_id, user_id, role, granted_by, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (account_id, user_id) DO UPDATE "
                "SET role = excluded.role, granted_by = excluded.granted_by, "
                "updated_at = excluded.updated_at",
                (int(account_id), int(user_id), role.value, granted_by, now, now),
            )
            event = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.MEMBER_ROLE_CHANGED
                    if existing is not None
                    else AuditEventType.MEMBER_ROLE_GRANTED,
                    actor=actor,
                    account_id=account_id,
                    member=int(user_id),
                    old_role=existing["role"] if existing is not None else None,
                    new_role=role.value,
                ),
            )
        self._audit.log_stored(event)
        members = [m for m in self.list_members(account_id, limit=10_000) if m.user_id == user_id]
        return members[-1]

    def remove(self, *, account_id: int, user_id: int, actor: Actor) -> None:
        if actor.id is not None and actor.id == user_id:
            raise PermissionDeniedError("You cannot remove your own membership.")
        with self._store.transaction() as db:
            existing = db.execute(
                "SELECT role FROM memberships WHERE account_id = ? AND user_id = ?",
                (int(account_id), int(user_id)),
            ).fetchone()
            if existing is None:
                raise NotFoundError()
            if existing["role"] == Role.OWNER.value:
                self._require_other_owner(db, account_id, user_id)
            db.execute(
                "DELETE FROM memberships WHERE account_id = ? AND user_id = ?",
                (int(account_id), int(user_id)),
            )
            event = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.MEMBER_REMOVED,
                    actor=actor,
                    account_id=account_id,
                    member=int(user_id),
                    old_role=existing["role"],
                ),
            )
        self._audit.log_stored(event)

    @staticmethod
    def _require_other_owner(db, account_id: int, user_id: int) -> None:  # type: ignore[no-untyped-def]
        row = db.execute(
            "SELECT COUNT(*) AS owners FROM memberships WHERE account_id = ? AND role = 'owner' "
            "AND user_id != ?",
            (int(account_id), int(user_id)),
        ).fetchone()
        if int(row["owners"]) == 0:
            raise ConflictError("An organization must keep at least one owner.")
