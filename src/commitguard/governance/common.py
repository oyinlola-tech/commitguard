"""Shared helpers for the organization governance services.

Tenant scoping
==============

Every governance row carries the ``account_id`` of the organization (the GitHub
account that owns the installation) and, for repository-level rows, GitHub's
immutable repository ID. Services never accept a repository or group from a
request without resolving it *within* that account:

* :func:`account_repositories` lists the repositories the account's
  installations have seen, one row per repository ID (a repository seen through
  an older installation and again after a reinstallation is one repository);
* :func:`visible_repository_ids` narrows that to the repositories GitHub
  reported to the signed-in session - the same rule as every other dashboard
  view, so an organization role never reveals a repository its holder could not
  open on GitHub.

A resource of another account is answered exactly like a missing one.
"""

import json
import re
import sqlite3
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from commitguard.controlplane.access import Permission, Principal
from commitguard.controlplane.errors import (
    InputValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from commitguard.controlplane.results import clean_text
from commitguard.github.storage import SqliteStateStore

MAX_NAME_CHARS = 100
MAX_DESCRIPTION_CHARS = 500
MAX_REASON_CHARS = 500
#: Upper bound on repositories named in one request (bulk operations use jobs).
MAX_REPOSITORIES_PER_REQUEST = 5000
_HEX_ID = re.compile(r"\A[0-9a-f]{32}\Z")


def new_id() -> str:
    return uuid.uuid4().hex


def ts(value: datetime) -> float:
    return value.timestamp()


def dt(value: float | None) -> datetime | None:
    return None if value is None else datetime.fromtimestamp(float(value), UTC)


def req_dt(value: float) -> datetime:
    return datetime.fromtimestamp(float(value), UTC)


def is_hex_id(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX_ID.match(value))


def require(principal: Principal, permission: Permission, account_id: int) -> None:
    """404 for a non-member (no disclosure), 403 for a member without the permission."""
    if account_id not in principal.memberships:
        raise NotFoundError()
    if not principal.can(permission, account_id):
        raise PermissionDeniedError()


def text(value: object, field: str, *, limit: int, required: bool = False) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise InputValidationError(f"{field} is required", field=field)
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise InputValidationError(
            f"{field} must be text of at most {limit} characters", field=field
        )
    return clean_text(value.strip(), limit)


def repository_ids(raw: object, field: str = "repository_ids") -> list[int]:
    if not isinstance(raw, list) or not raw:
        raise InputValidationError(
            f"{field} must be a non-empty list of repository IDs", field=field
        )
    if len(raw) > MAX_REPOSITORIES_PER_REQUEST:
        raise InputValidationError(
            f"{field} may name at most {MAX_REPOSITORIES_PER_REQUEST} repositories", field=field
        )
    ids = []
    for item in raw:
        if not isinstance(item, int) or isinstance(item, bool) or not 1 <= item < 10**16:
            raise InputValidationError(f"{field} must contain repository IDs", field=field)
        ids.append(item)
    return sorted(set(ids))


@dataclass(frozen=True, slots=True)
class AccountRepository:
    repository_id: int
    installation_id: int
    owner: str
    name: str
    default_branch: str | None
    private: bool | None
    archived: bool
    listed: bool
    installation_state: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def connected(self) -> bool:
        return self.installation_state == "active" and self.listed


_ACCOUNT_REPOSITORIES = (
    "SELECT repository_id, installation_id, owner, name, default_branch, private, archived, "
    "listed, installation_state FROM (SELECT k.repository_id, k.installation_id, k.owner, k.name, "
    "k.default_branch, k.private, k.archived, i.state AS installation_state, EXISTS (SELECT 1 FROM "
    "installation_repositories ir WHERE ir.installation_id = k.installation_id AND "
    "ir.repository_id = k.repository_id) AS listed, ROW_NUMBER() OVER (PARTITION BY "
    "k.repository_id ORDER BY (i.state = 'active') DESC, k.last_seen_at DESC) AS rank "
    "FROM known_repositories k JOIN installations i ON i.installation_id = k.installation_id "
    "WHERE i.account_id = ? AND i.state != 'deleted') WHERE rank = 1"
)


def _repository(row: sqlite3.Row) -> AccountRepository:
    return AccountRepository(
        repository_id=int(row["repository_id"]),
        installation_id=int(row["installation_id"]),
        owner=str(row["owner"]),
        name=str(row["name"]),
        default_branch=row["default_branch"],
        private=None if row["private"] is None else bool(row["private"]),
        archived=bool(row["archived"]),
        listed=bool(row["listed"]),
        installation_state=str(row["installation_state"]),
    )


def account_repositories(
    store_or_db: SqliteStateStore | sqlite3.Connection, account_id: int
) -> dict[int, AccountRepository]:
    """Every repository of the account (including disconnected ones), by repository ID."""
    params = (int(account_id),)
    if isinstance(store_or_db, sqlite3.Connection):
        rows = store_or_db.execute(_ACCOUNT_REPOSITORIES, params).fetchall()
    else:
        rows = store_or_db.query(_ACCOUNT_REPOSITORIES, params)
    return {int(row["repository_id"]): _repository(row) for row in rows}


def account_repository(
    store_or_db: SqliteStateStore | sqlite3.Connection, account_id: int, repository_id: int
) -> AccountRepository | None:
    # _ACCOUNT_REPOSITORIES is a module constant; nothing from the caller enters the SQL.
    sql = f"SELECT * FROM ({_ACCOUNT_REPOSITORIES}) WHERE repository_id = ?"  # noqa: S608  # nosec B608
    params = (int(account_id), int(repository_id))
    if isinstance(store_or_db, sqlite3.Connection):
        row = store_or_db.execute(sql, params).fetchone()
    else:
        rows = store_or_db.query(sql, params)
        row = rows[0] if rows else None
    return _repository(row) if row is not None else None


def visible_repository_ids(
    store: SqliteStateStore, principal: Principal, account_id: int
) -> set[int]:
    """Repository IDs of the account that GitHub reported to this session."""
    installations = sorted(i for i, a in principal.installations.items() if a == account_id)
    if not installations:
        return set()
    rows = store.query(
        "SELECT DISTINCT repository_id FROM session_repositories WHERE session_hash = ? "
        "AND installation_id IN (SELECT value FROM json_each(?))",
        (principal.session_hash, json.dumps(installations)),
    )
    return {int(row["repository_id"]) for row in rows}


def require_visible_repositories(
    store: SqliteStateStore, principal: Principal, account_id: int, ids: Iterable[int]
) -> list[int]:
    """All of ``ids`` must be the account's and visible to the caller, else 404."""
    wanted = sorted(set(ids))
    visible = visible_repository_ids(store, principal, account_id)
    known = account_repositories(store, account_id)
    missing = [i for i in wanted if i not in visible or i not in known]
    if missing:
        raise NotFoundError(
            f"{len(missing)} of the selected repositories were not found in this organization."
        )
    return wanted


def json_list(value: str | None) -> list[object]:
    if not value:
        return []
    parsed = json.loads(value)
    return parsed if isinstance(parsed, list) else []


def chunks[T](items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
