"""Repository groups: named sets of repositories that policies and exceptions can target.

A repository may belong to any number of groups (``Production`` and
``Backend``). Where several groups set a *default* for the same rule the most
restrictive default applies; *mandatory* requirements always combine to the
strongest (see :mod:`commitguard.policies.governance`).

Groups are archived, never deleted: policy versions, exceptions and audit
events keep referring to them. Membership changes need
``repositories:manage``, name only repositories of the same organization that
the caller can see, are audited, and invalidate the effective policy of exactly
the repositories added or removed.
"""

import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from commitguard.audit.models import Actor, AuditEvent, AuditEventType
from commitguard.controlplane.access import Permission, Principal
from commitguard.controlplane.errors import (
    ConfirmationRequiredError,
    ConflictError,
    InputValidationError,
    NotFoundError,
)
from commitguard.github.storage import SqliteStateStore
from commitguard.governance.cache import group_member_ids, invalidate_repositories
from commitguard.governance.common import (
    MAX_DESCRIPTION_CHARS,
    MAX_NAME_CHARS,
    account_repositories,
    dt,
    is_hex_id,
    new_id,
    req_dt,
    require,
    require_visible_repositories,
    text,
    ts,
    visible_repository_ids,
)
from commitguard.services.audit import AuditService

MAX_GROUPS_PER_ORGANIZATION = 500


class RepositoryGroupView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    organization_id: int
    name: str
    description: str | None
    repository_count: int
    policy_version: int
    active_exceptions: int
    created_at: datetime
    created_by: str | None
    updated_at: datetime
    archived_at: datetime | None


class GroupMemberView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repository_id: int
    full_name: str
    added_at: datetime
    added_by: str | None


class RepositoryGroupDetail(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    group: RepositoryGroupView
    repositories: tuple[GroupMemberView, ...]
    hidden_repositories: int  # members GitHub did not report to this session
    can_manage: bool


_GROUP_SELECT = (
    "SELECT g.*, (SELECT COUNT(*) FROM repository_group_members m WHERE m.group_id = g.group_id) "
    "AS repository_count, COALESCE((SELECT MAX(version) FROM scoped_policy_versions p WHERE "
    "p.account_id = g.account_id AND p.target_type = 'group' AND p.target_id = g.group_id), 0) "
    "AS policy_version, (SELECT COUNT(*) FROM policy_exceptions e WHERE e.account_id = "
    "g.account_id AND e.scope_type = 'group' AND e.scope_id = g.group_id AND e.status = 'active') "
    "AS active_exceptions FROM repository_groups g"
)


def _name_key(name: str) -> str:
    return " ".join(name.casefold().split())


class RepositoryGroupService:
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

    @staticmethod
    def _view(r: sqlite3.Row) -> RepositoryGroupView:
        return RepositoryGroupView(
            id=r["group_id"],
            organization_id=int(r["account_id"]),
            name=r["name"],
            description=r["description"],
            repository_count=int(r["repository_count"]),
            policy_version=int(r["policy_version"]),
            active_exceptions=int(r["active_exceptions"]),
            created_at=req_dt(r["created_at"]),
            created_by=r["created_by"],
            updated_at=req_dt(r["updated_at"]),
            archived_at=dt(r["archived_at"]),
        )

    def list_groups(
        self, principal: Principal, account_id: int, *, include_archived: bool = False
    ) -> list[RepositoryGroupView]:
        require(principal, Permission.REPOSITORIES_READ, account_id)
        sql = f"{_GROUP_SELECT} WHERE g.account_id = ?"
        if not include_archived:
            sql += " AND g.archived_at IS NULL"
        rows = self._store.query(sql + " ORDER BY g.name_key LIMIT 1000", (account_id,))
        return [self._view(row) for row in rows]

    def _group_row(self, group_id: str) -> sqlite3.Row:
        if not is_hex_id(group_id):
            raise NotFoundError()
        rows = self._store.query(f"{_GROUP_SELECT} WHERE g.group_id = ?", (group_id,))
        if not rows:
            raise NotFoundError()
        return rows[0]

    def account_of(self, principal: Principal, group_id: str, permission: Permission) -> int:
        """The group's organization after checking the caller may ``permission`` there."""
        row = self._group_row(group_id)
        account_id = int(row["account_id"])
        require(principal, permission, account_id)
        return account_id

    def get(self, principal: Principal, group_id: str) -> RepositoryGroupDetail:
        row = self._group_row(group_id)
        account_id = int(row["account_id"])
        require(principal, Permission.REPOSITORIES_READ, account_id)
        visible = visible_repository_ids(self._store, principal, account_id)
        known = account_repositories(self._store, account_id)
        members = self._store.query(
            "SELECT repository_id, added_at, added_by FROM repository_group_members "
            "WHERE group_id = ? ORDER BY added_at",
            (group_id,),
        )
        shown = []
        hidden = 0
        for member in members:
            repository_id = int(member["repository_id"])
            repository = known.get(repository_id)
            if repository is None or repository_id not in visible:
                hidden += 1
                continue
            shown.append(
                GroupMemberView(
                    repository_id=repository_id,
                    full_name=repository.full_name,
                    added_at=req_dt(member["added_at"]),
                    added_by=member["added_by"],
                )
            )
        shown.sort(key=lambda m: m.full_name.casefold())
        return RepositoryGroupDetail(
            group=self._view(row),
            repositories=tuple(shown),
            hidden_repositories=hidden,
            can_manage=principal.can(Permission.REPOSITORIES_MANAGE, account_id),
        )

    def create(
        self, principal: Principal, account_id: int, *, name: object, description: object
    ) -> RepositoryGroupView:
        require(principal, Permission.REPOSITORIES_MANAGE, account_id)
        clean_name = text(name, "name", limit=MAX_NAME_CHARS, required=True)
        assert clean_name is not None  # noqa: S101 - required=True
        clean_description = text(description, "description", limit=MAX_DESCRIPTION_CHARS)
        now = self._now()
        group_id = new_id()
        actor = Actor.user(principal.user_id, principal.login)
        with self._store.transaction() as db:
            count = db.execute(
                "SELECT COUNT(*) AS n FROM repository_groups WHERE account_id = ? "
                "AND archived_at IS NULL",
                (account_id,),
            ).fetchone()["n"]
            if int(count) >= MAX_GROUPS_PER_ORGANIZATION:
                raise ConflictError(
                    f"An organization can have at most {MAX_GROUPS_PER_ORGANIZATION} groups."
                )
            if db.execute(
                "SELECT 1 FROM repository_groups WHERE account_id = ? AND name_key = ? "
                "AND archived_at IS NULL",
                (account_id, _name_key(clean_name)),
            ).fetchone():
                raise ConflictError("A group with this name already exists.")
            db.execute(
                "INSERT INTO repository_groups (group_id, account_id, name, name_key, "
                "description, created_at, created_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    group_id,
                    account_id,
                    clean_name,
                    _name_key(clean_name),
                    clean_description,
                    ts(now),
                    principal.login,
                    ts(now),
                ),
            )
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.REPOSITORY_GROUP_CREATED,
                    actor=actor,
                    account_id=account_id,
                    group=group_id,
                    name=clean_name,
                ),
            )
        self._audit.log_stored(stored)
        return self._view(self._group_row(group_id))

    def update(
        self, principal: Principal, group_id: str, *, name: object, description: object
    ) -> RepositoryGroupView:
        row = self._group_row(group_id)
        account_id = int(row["account_id"])
        require(principal, Permission.REPOSITORIES_MANAGE, account_id)
        if row["archived_at"] is not None:
            raise ConflictError("An archived group cannot be changed.")
        new_name = text(name, "name", limit=MAX_NAME_CHARS) if name is not None else row["name"]
        new_description = (
            text(description, "description", limit=MAX_DESCRIPTION_CHARS)
            if description is not None
            else row["description"]
        )
        if new_name == row["name"] and new_description == row["description"]:
            raise InputValidationError("Nothing to change.")
        now = self._now()
        with self._store.transaction() as db:
            if new_name != row["name"] and db.execute(
                "SELECT 1 FROM repository_groups WHERE account_id = ? AND name_key = ? "
                "AND archived_at IS NULL AND group_id != ?",
                (account_id, _name_key(str(new_name)), group_id),
            ).fetchone():
                raise ConflictError("A group with this name already exists.")
            db.execute(
                "UPDATE repository_groups SET name = ?, name_key = ?, description = ?, "
                "updated_at = ? WHERE group_id = ?",
                (new_name, _name_key(str(new_name)), new_description, ts(now), group_id),
            )
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.REPOSITORY_GROUP_UPDATED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=account_id,
                    group=group_id,
                    name=str(new_name),
                    previous_name=str(row["name"]),
                ),
            )
        self._audit.log_stored(stored)
        return self._view(self._group_row(group_id))

    def archive(self, principal: Principal, group_id: str, *, confirm: object) -> None:
        row = self._group_row(group_id)
        account_id = int(row["account_id"])
        require(principal, Permission.REPOSITORIES_MANAGE, account_id)
        if row["archived_at"] is not None:
            raise ConflictError("The group is already archived.")
        affects_policy = int(row["policy_version"]) > 0 or int(row["active_exceptions"]) > 0
        if affects_policy and confirm is not True:
            raise ConfirmationRequiredError(
                "This group has a policy or active exceptions; archiving it changes the "
                "effective policy of its repositories and must be confirmed."
            )
        now = self._now()
        with self._store.transaction() as db:
            members = group_member_ids(db, account_id, group_id)
            db.execute(
                "UPDATE repository_groups SET archived_at = ?, archived_by = ?, updated_at = ? "
                "WHERE group_id = ? AND archived_at IS NULL",
                (ts(now), principal.login, ts(now), group_id),
            )
            # Exceptions scoped to an archived group end with it (history is kept).
            db.execute(
                "UPDATE policy_exceptions SET status = CASE status WHEN 'requested' THEN "
                "'cancelled' ELSE 'revoked' END, revoked_at = ?, revoked_by_login = ?, "
                "revoke_reason = 'repository group archived', updated_at = ? WHERE account_id = ? "
                "AND scope_type = 'group' AND scope_id = ? AND status IN ('requested', 'active')",
                (ts(now), principal.login, ts(now), account_id, group_id),
            )
            invalidate_repositories(db, account_id, members, now)
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.REPOSITORY_GROUP_ARCHIVED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=account_id,
                    group=group_id,
                    name=str(row["name"]),
                    repositories=len(members),
                ),
            )
        self._audit.log_stored(stored)

    def add_members(
        self, principal: Principal, group_id: str, repository_ids: Sequence[int]
    ) -> RepositoryGroupDetail:
        row = self._group_row(group_id)
        account_id = int(row["account_id"])
        require(principal, Permission.REPOSITORIES_MANAGE, account_id)
        ids = require_visible_repositories(self._store, principal, account_id, repository_ids)
        with self._store.transaction() as db:
            added = self.add_members_in(
                db, account_id, group_id, ids, actor=Actor.user(principal.user_id, principal.login)
            )
        if added is not None:
            self._audit.log_stored(added)
        return self.get(principal, group_id)

    def add_members_in(
        self,
        db: sqlite3.Connection,
        account_id: int,
        group_id: str,
        repository_ids: Sequence[int],
        *,
        actor: Actor,
    ) -> AuditEvent | None:
        """Add members inside a caller's transaction (also used by bulk operations).

        Returns the stored audit event, or None when every repository was already a member.
        """
        group = db.execute(
            "SELECT archived_at FROM repository_groups WHERE group_id = ? AND account_id = ?",
            (group_id, account_id),
        ).fetchone()
        if group is None:
            raise NotFoundError()
        if group["archived_at"] is not None:
            raise ConflictError("An archived group cannot be changed.")
        now = self._now()
        known = account_repositories(db, account_id)
        added = []
        for repository_id in repository_ids:
            if repository_id not in known:
                raise NotFoundError("A selected repository was not found in this organization.")
            cursor = db.execute(
                "INSERT OR IGNORE INTO repository_group_members (group_id, account_id, "
                "repository_id, added_at, added_by) VALUES (?, ?, ?, ?, ?)",
                (group_id, account_id, repository_id, ts(now), actor.login),
            )
            if cursor.rowcount:
                added.append(repository_id)
        if not added:
            return None
        db.execute(
            "UPDATE repository_groups SET updated_at = ? WHERE group_id = ?", (ts(now), group_id)
        )
        invalidate_repositories(db, account_id, added, now)
        return self._store.insert_audit_event(
            db,
            self._audit.build(
                AuditEventType.REPOSITORY_GROUP_MEMBERS_ADDED,
                actor=actor,
                account_id=account_id,
                group=group_id,
                repositories=len(added),
                repository_ids=",".join(str(i) for i in added[:50]),
            ),
        )

    def remove_members(
        self, principal: Principal, group_id: str, repository_ids: Sequence[int]
    ) -> RepositoryGroupDetail:
        row = self._group_row(group_id)
        account_id = int(row["account_id"])
        require(principal, Permission.REPOSITORIES_MANAGE, account_id)
        ids = require_visible_repositories(self._store, principal, account_id, repository_ids)
        with self._store.transaction() as db:
            stored = self.remove_members_in(
                db, account_id, group_id, ids, actor=Actor.user(principal.user_id, principal.login)
            )
        if stored is not None:
            self._audit.log_stored(stored)
        return self.get(principal, group_id)

    def remove_members_in(
        self,
        db: sqlite3.Connection,
        account_id: int,
        group_id: str,
        repository_ids: Sequence[int],
        *,
        actor: Actor,
    ) -> AuditEvent | None:
        now = self._now()
        removed = []
        for repository_id in repository_ids:
            cursor = db.execute(
                "DELETE FROM repository_group_members WHERE group_id = ? AND account_id = ? "
                "AND repository_id = ?",
                (group_id, account_id, repository_id),
            )
            if cursor.rowcount:
                removed.append(repository_id)
        if not removed:
            return None
        invalidate_repositories(db, account_id, removed, now)
        return self._store.insert_audit_event(
            db,
            self._audit.build(
                AuditEventType.REPOSITORY_GROUP_MEMBERS_REMOVED,
                actor=actor,
                account_id=account_id,
                group=group_id,
                repositories=len(removed),
                repository_ids=",".join(str(i) for i in removed[:50]),
            ),
        )
