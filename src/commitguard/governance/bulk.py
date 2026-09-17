"""Bulk repository operations: authorized, audited, bounded, idempotent - and never inline.

::

    Admin ─ POST bulk operation ─► validate (permission, repositories visible, limits)
                                    │ one row + one item per repository   (201, "queued")
                                    ▼
    maintenance loop ─ claim (lease) ─► next batch of pending items
                                    │ each item in its own transaction
                                    ▼
                         completed / failed (with a reason) / skipped
                                    │
                                    ▼
    status: queued -> running -> completed | partial | failed | cancelled

Operations
==========

=====================  ===================================  ==========================
Type                   Parameters                            Permission
=====================  ===================================  ==========================
``add_to_group``       ``group_id``                          ``repositories:manage``
``remove_from_group``  ``group_id``                          ``repositories:manage``
``onboard``            ``mode`` (``monitor``/``enforce``)    ``repositories:manage``
``set_mode``           ``mode``, ``reason`` for monitor      ``repositories:manage``
``set_monitoring``     ``enabled``                           ``repositories:manage``
``schedule_scan``      -                                     ``scans:trigger``
=====================  ===================================  ==========================

"Apply a policy" to many repositories is done by adding them to a repository
group that has the policy: the group's policy then applies through normal
resolution, with its versioning, approval and rollback.

Safety:

* at most :data:`MAX_ITEMS` repositories per operation and
  :data:`MAX_OPEN_OPERATIONS` open operations per organization; the request
  only validates and stores the work;
* the request's ``idempotency_key`` (or a hash of the operation) is unique per
  organization: submitting the same operation twice returns the first one;
* every item operation is idempotent (adding a member twice is one member), so
  a crash mid-batch is retried safely after the lease expires;
* changing modes to ``enforce`` or ``monitor`` needs ``confirm`` at request time,
  like the single-repository action;
* failed items can be retried; an operation can be cancelled while items are
  pending. One audit event records the request, one the outcome.
"""

import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

from commitguard.audit.models import Actor, AuditEventType
from commitguard.controlplane.access import Permission, Principal
from commitguard.controlplane.errors import (
    ConfirmationRequiredError,
    ConflictError,
    ControlPlaneError,
    InputValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from commitguard.exceptions.base import CommitGuardError
from commitguard.github.storage import SqliteStateStore
from commitguard.governance.common import (
    MAX_REASON_CHARS,
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
from commitguard.governance.groups import RepositoryGroupService
from commitguard.governance.inventory import (
    ENFORCE_WARNING,
    MONITOR_WARNING,
    RepositoryInventory,
)
from commitguard.governance.schedules import DefaultBranchScanner
from commitguard.observability.logging import get_logger
from commitguard.policies.governance import RepositoryMode
from commitguard.security.hashing import fingerprint
from commitguard.services.audit import AuditService

log = get_logger(__name__)

MAX_ITEMS = 5000
MAX_OPEN_OPERATIONS = 10
ITEMS_PER_TICK = 200
LEASE = timedelta(minutes=5)
TYPES = (
    "add_to_group",
    "remove_from_group",
    "onboard",
    "set_mode",
    "set_monitoring",
    "schedule_scan",
)


class BulkItemView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repository_id: int
    full_name: str | None
    status: str
    attempts: int
    detail: str | None


class BulkOperationView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    organization_id: int
    type: str
    parameters: dict[str, Any]
    status: str
    total: int
    completed: int
    failed: int
    skipped: int
    pending: int
    requested_by: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_by: str | None
    items: tuple[BulkItemView, ...]
    can_manage: bool


class BulkOperationService:
    def __init__(
        self,
        store: SqliteStateStore,
        audit: AuditService,
        groups: RepositoryGroupService,
        inventory: RepositoryInventory,
        scanner: DefaultBranchScanner | None,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._audit = audit
        self._groups = groups
        self._inventory = inventory
        self._scanner = scanner
        self._now = now

    # -- requests --------------------------------------------------------- #
    def create(
        self,
        principal: Principal,
        account_id: int,
        *,
        operation_type: object,
        repository_ids: Sequence[int],
        parameters: object,
        idempotency_key: object,
        confirm: object,
    ) -> BulkOperationView:
        if operation_type not in TYPES:
            raise InputValidationError(
                f"type must be one of {', '.join(TYPES)}", field="type"
            )
        assert isinstance(operation_type, str)  # noqa: S101 - checked above
        permission = (
            Permission.SCANS_TRIGGER
            if operation_type == "schedule_scan"
            else Permission.REPOSITORIES_MANAGE
        )
        require(principal, permission, account_id)
        if len(repository_ids) > MAX_ITEMS:
            raise InputValidationError(
                f"a bulk operation covers at most {MAX_ITEMS} repositories",
                field="repository_ids",
            )
        ids = require_visible_repositories(self._store, principal, account_id, repository_ids)
        params = self._parameters(principal, account_id, operation_type, parameters, confirm)
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 128
        ):
            raise InputValidationError(
                "idempotency_key must be 8-128 characters", field="idempotency_key"
            )
        key = idempotency_key or fingerprint(
            [operation_type, json.dumps(params, sort_keys=True), *map(str, ids)]
        )
        now = self._now()
        operation_id = new_id()
        with self._store.transaction() as db:
            existing = db.execute(
                "SELECT operation_id FROM bulk_operations WHERE account_id = ? "
                "AND idempotency_key = ?",
                (account_id, key),
            ).fetchone()
            if existing is not None:
                existing_id = str(existing["operation_id"])
            else:
                existing_id = None
                open_count = db.execute(
                    "SELECT COUNT(*) AS n FROM bulk_operations WHERE account_id = ? "
                    "AND status IN ('queued', 'running')",
                    (account_id,),
                ).fetchone()["n"]
                if int(open_count) >= MAX_OPEN_OPERATIONS:
                    raise ConflictError(
                        "Too many bulk operations are in progress for this organization. "
                        "Wait for one to finish."
                    )
                db.execute(
                    "INSERT INTO bulk_operations (operation_id, account_id, type, parameters, "
                    "idempotency_key, requested_by_id, requested_by_login, created_at, status, "
                    "total, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                    (
                        operation_id,
                        account_id,
                        operation_type,
                        json.dumps(params, sort_keys=True),
                        key,
                        principal.user_id,
                        principal.login,
                        ts(now),
                        len(ids),
                        ts(now),
                    ),
                )
                db.executemany(
                    "INSERT INTO bulk_operation_items (operation_id, repository_id, status, "
                    "updated_at) VALUES (?, ?, 'pending', ?)",
                    [(operation_id, repository_id, ts(now)) for repository_id in ids],
                )
                stored = self._store.insert_audit_event(
                    db,
                    self._audit.build(
                        AuditEventType.BULK_OPERATION_REQUESTED,
                        actor=Actor.user(principal.user_id, principal.login),
                        account_id=account_id,
                        operation=operation_id,
                        operation_type=operation_type,
                        repositories=len(ids),
                        parameters=json.dumps(params, sort_keys=True)[:400],
                    ),
                )
        if existing_id is not None:
            return self.get(principal, existing_id)
        self._audit.log_stored(stored)
        return self.get(principal, operation_id)

    def _parameters(
        self,
        principal: Principal,
        account_id: int,
        operation_type: str,
        raw: object,
        confirm: object,
    ) -> dict[str, Any]:
        params = raw if isinstance(raw, dict) else {}
        if raw is not None and not isinstance(raw, dict):
            raise InputValidationError("parameters must be an object", field="parameters")
        if operation_type in ("add_to_group", "remove_from_group"):
            group_id = params.get("group_id")
            if not is_hex_id(group_id):
                raise InputValidationError("group_id is required", field="parameters.group_id")
            assert isinstance(group_id, str)  # noqa: S101 - checked above
            if self._groups.account_of(principal, group_id, Permission.REPOSITORIES_MANAGE) != (
                account_id
            ):
                raise NotFoundError()
            return {"group_id": group_id}
        if operation_type in ("onboard", "set_mode"):
            mode = RepositoryInventory.parse_mode(params.get("mode"))
            reason = text(params.get("reason"), "reason", limit=MAX_REASON_CHARS)
            if confirm is not True:
                raise ConfirmationRequiredError(
                    ENFORCE_WARNING if mode is RepositoryMode.ENFORCE else MONITOR_WARNING
                )
            if mode is RepositoryMode.MONITOR and reason is None:
                raise InputValidationError(
                    "A reason is required to stop blocking (monitor mode).",
                    field="parameters.reason",
                )
            return {"mode": mode.value, "reason": reason}
        if operation_type == "set_monitoring":
            enabled = params.get("enabled")
            if not isinstance(enabled, bool):
                raise InputValidationError(
                    "enabled must be true or false", field="parameters.enabled"
                )
            if not enabled and confirm is not True:
                raise ConfirmationRequiredError(
                    "Pausing monitoring stops CommitGuard checks for these repositories."
                )
            return {"enabled": enabled}
        return {}

    # -- reads ------------------------------------------------------------ #
    def _row(self, operation_id: str) -> sqlite3.Row:
        if not is_hex_id(operation_id):
            raise NotFoundError()
        rows = self._store.query(
            "SELECT * FROM bulk_operations WHERE operation_id = ?", (operation_id,)
        )
        if not rows:
            raise NotFoundError()
        return rows[0]

    def get(self, principal: Principal, operation_id: str) -> BulkOperationView:
        row = self._row(operation_id)
        account_id = int(row["account_id"])
        require(principal, Permission.REPOSITORIES_READ, account_id)
        return self._view(row, principal, with_items=True)

    def list_operations(self, principal: Principal, account_id: int) -> list[BulkOperationView]:
        require(principal, Permission.REPOSITORIES_READ, account_id)
        rows = self._store.query(
            "SELECT * FROM bulk_operations WHERE account_id = ? ORDER BY created_at DESC LIMIT 50",
            (account_id,),
        )
        return [self._view(row, principal, with_items=False) for row in rows]

    def _view(
        self, row: sqlite3.Row, principal: Principal, *, with_items: bool
    ) -> BulkOperationView:
        account_id = int(row["account_id"])
        operation_id = str(row["operation_id"])
        counts = {
            str(r["status"]): int(r["n"])
            for r in self._store.query(
                "SELECT status, COUNT(*) AS n FROM bulk_operation_items WHERE operation_id = ? "
                "GROUP BY status",
                (operation_id,),
            )
        }
        items: tuple[BulkItemView, ...] = ()
        if with_items:
            names = {
                repository_id: repository.full_name
                for repository_id, repository in account_repositories(
                    self._store, account_id
                ).items()
            }
            visible = visible_repository_ids(self._store, principal, account_id)
            items = tuple(
                BulkItemView(
                    repository_id=int(r["repository_id"]),
                    full_name=names.get(int(r["repository_id"]))
                    if int(r["repository_id"]) in visible
                    else None,
                    status=str(r["status"]),
                    attempts=int(r["attempts"]),
                    detail=r["detail"],
                )
                for r in self._store.query(
                    "SELECT * FROM bulk_operation_items WHERE operation_id = ? "
                    "ORDER BY CASE status WHEN 'failed' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, "
                    "repository_id LIMIT 500",
                    (operation_id,),
                )
            )
        permission = (
            Permission.SCANS_TRIGGER
            if row["type"] == "schedule_scan"
            else Permission.REPOSITORIES_MANAGE
        )
        return BulkOperationView(
            id=operation_id,
            organization_id=account_id,
            type=str(row["type"]),
            parameters=json.loads(str(row["parameters"])),
            status=str(row["status"]),
            total=int(row["total"]),
            completed=counts.get("completed", 0),
            failed=counts.get("failed", 0),
            skipped=counts.get("skipped", 0),
            pending=counts.get("pending", 0),
            requested_by=row["requested_by_login"],
            created_at=req_dt(row["created_at"]),
            started_at=dt(row["started_at"]),
            completed_at=dt(row["completed_at"]),
            cancelled_by=row["cancelled_by_login"],
            items=items,
            can_manage=principal.can(permission, account_id),
        )

    # -- control ---------------------------------------------------------- #
    def _manageable(self, principal: Principal, operation_id: str) -> sqlite3.Row:
        row = self._row(operation_id)
        account_id = int(row["account_id"])
        require(principal, Permission.REPOSITORIES_READ, account_id)
        permission = (
            Permission.SCANS_TRIGGER
            if row["type"] == "schedule_scan"
            else Permission.REPOSITORIES_MANAGE
        )
        if not principal.can(permission, account_id):
            raise PermissionDeniedError()
        return row

    def cancel(self, principal: Principal, operation_id: str) -> BulkOperationView:
        row = self._manageable(principal, operation_id)
        if str(row["status"]) not in ("queued", "running"):
            raise ConflictError(f"A {row['status']} operation cannot be cancelled.")
        now = self._now()
        with self._store.transaction() as db:
            db.execute(
                "UPDATE bulk_operation_items SET status = 'cancelled', updated_at = ? "
                "WHERE operation_id = ? AND status = 'pending'",
                (ts(now), operation_id),
            )
            db.execute(
                "UPDATE bulk_operations SET status = 'cancelled', completed_at = ?, "
                "cancelled_by_login = ?, lease_expires_at = NULL, updated_at = ? "
                "WHERE operation_id = ?",
                (ts(now), principal.login, ts(now), operation_id),
            )
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.BULK_OPERATION_CANCELLED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=int(row["account_id"]),
                    operation=operation_id,
                ),
            )
        self._audit.log_stored(stored)
        return self.get(principal, operation_id)

    def retry_failed(self, principal: Principal, operation_id: str) -> BulkOperationView:
        row = self._manageable(principal, operation_id)
        if str(row["status"]) not in ("partial", "failed"):
            raise ConflictError("Only an operation with failed items can be retried.")
        now = self._now()
        with self._store.transaction() as db:
            retried = db.execute(
                "UPDATE bulk_operation_items SET status = 'pending', updated_at = ? "
                "WHERE operation_id = ? AND status = 'failed'",
                (ts(now), operation_id),
            ).rowcount
            if retried:
                db.execute(
                    "UPDATE bulk_operations SET status = 'queued', completed_at = NULL, "
                    "updated_at = ? WHERE operation_id = ?",
                    (ts(now), operation_id),
                )
        return self.get(principal, operation_id)

    # -- processing ------------------------------------------------------- #
    def run_pending(self, budget: int = ITEMS_PER_TICK) -> int:
        """Process pending items of queued/running operations. Returns items processed."""
        now = self._now()
        processed = 0
        operations = self._store.query(
            "SELECT * FROM bulk_operations WHERE status = 'queued' OR (status = 'running' "
            "AND (lease_expires_at IS NULL OR lease_expires_at < ?)) ORDER BY created_at LIMIT 5",
            (ts(now),),
        )
        for operation in operations:
            if processed >= budget:
                break
            operation_id = str(operation["operation_id"])
            with self._store.transaction() as db:
                claimed = db.execute(
                    "UPDATE bulk_operations SET status = 'running', started_at = "
                    "COALESCE(started_at, ?), lease_expires_at = ?, updated_at = ? "
                    "WHERE operation_id = ? AND (status = 'queued' OR (status = 'running' AND "
                    "(lease_expires_at IS NULL OR lease_expires_at < ?)))",
                    (ts(now), ts(now + LEASE), ts(now), operation_id, ts(now)),
                ).rowcount
            if claimed != 1:
                continue
            items = self._store.query(
                "SELECT repository_id FROM bulk_operation_items WHERE operation_id = ? "
                "AND status = 'pending' ORDER BY repository_id LIMIT ?",
                (operation_id, budget - processed),
            )
            for item in items:
                self._process_item(operation, int(item["repository_id"]))
                processed += 1
            self._finish_if_done(operation)
        return processed

    def _process_item(self, operation: sqlite3.Row, repository_id: int) -> None:
        account_id = int(operation["account_id"])
        operation_id = str(operation["operation_id"])
        params = json.loads(str(operation["parameters"]))
        actor = Actor.user(
            int(operation["requested_by_id"] or 0),
            str(operation["requested_by_login"] or "bulk operation"),
        )
        status, detail = "completed", None
        now = self._now()
        try:
            kind = str(operation["type"])
            if kind == "schedule_scan":
                outcome = (
                    self._scanner.queue(
                        account_id,
                        repository_id,
                        key=("bulk", operation_id),
                        requested_by=actor.login,
                    )
                    if self._scanner is not None
                    else "skipped:no_scanner"
                )
                if outcome != "queued":
                    status, detail = "skipped", outcome.split(":", 1)[-1]
            else:
                with self._store.transaction() as db:
                    detail = self._apply_in(db, kind, account_id, repository_id, params, actor)
                    if detail is not None:
                        status = "skipped"
        except (ControlPlaneError, CommitGuardError, OSError) as exc:
            status = "failed"
            detail = str(exc)[:200] if isinstance(exc, ControlPlaneError) else type(exc).__name__
        with self._store.transaction() as db:
            db.execute(
                "UPDATE bulk_operation_items SET status = ?, attempts = attempts + 1, detail = ?, "
                "updated_at = ? WHERE operation_id = ? AND repository_id = ? AND status = "
                "'pending'",
                (status, detail, ts(now), operation_id, repository_id),
            )

    def _apply_in(
        self,
        db: sqlite3.Connection,
        kind: str,
        account_id: int,
        repository_id: int,
        params: dict[str, Any],
        actor: Actor,
    ) -> str | None:
        """Apply one item in a transaction. Returns a skip reason, or None when applied."""
        if repository_id not in account_repositories(db, account_id):
            return "not_found"
        if kind in ("add_to_group", "remove_from_group"):
            method = (
                self._groups.add_members_in
                if kind == "add_to_group"
                else self._groups.remove_members_in
            )
            event = method(db, account_id, str(params["group_id"]), [repository_id], actor=actor)
            return None if event is not None else "unchanged"
        if kind in ("onboard", "set_mode"):
            changed, _ = self._inventory.apply_in(
                db,
                account_id,
                [repository_id],
                mode=RepositoryMode(params["mode"]),
                actor=actor,
                onboard=kind == "onboard",
                reason=params.get("reason"),
            )
            return None if changed else "unchanged"
        if kind == "set_monitoring":
            installations = [
                int(r["installation_id"])
                for r in db.execute(
                    "SELECT k.installation_id FROM known_repositories k JOIN installations i ON "
                    "i.installation_id = k.installation_id WHERE i.account_id = ? "
                    "AND k.repository_id = ?",
                    (account_id, repository_id),
                ).fetchall()
            ]
            for installation_id in installations:
                db.execute(
                    "INSERT INTO repository_settings (installation_id, repository_id, "
                    "monitoring_enabled, updated_at, updated_by_login) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (installation_id, repository_id) DO UPDATE SET "
                    "monitoring_enabled = excluded.monitoring_enabled, "
                    "updated_at = excluded.updated_at, updated_by_login = "
                    "excluded.updated_by_login",
                    (
                        installation_id,
                        repository_id,
                        1 if params["enabled"] else 0,
                        ts(self._now()),
                        actor.login,
                    ),
                )
            self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.REPOSITORY_MONITORING_ENABLED
                    if params["enabled"]
                    else AuditEventType.REPOSITORY_MONITORING_DISABLED,
                    actor=actor,
                    account_id=account_id,
                    repository_id=repository_id,
                    source="bulk operation",
                ),
            )
            return None
        raise InputValidationError("unknown bulk operation")  # pragma: no cover

    def _finish_if_done(self, operation: sqlite3.Row) -> None:
        operation_id = str(operation["operation_id"])
        counts = {
            str(r["status"]): int(r["n"])
            for r in self._store.query(
                "SELECT status, COUNT(*) AS n FROM bulk_operation_items WHERE operation_id = ? "
                "GROUP BY status",
                (operation_id,),
            )
        }
        now = self._now()
        if counts.get("pending", 0):
            with self._store.transaction() as db:
                db.execute(
                    "UPDATE bulk_operations SET completed = ?, failed = ?, lease_expires_at = "
                    "NULL, "
                    "updated_at = ? WHERE operation_id = ?",
                    (
                        counts.get("completed", 0) + counts.get("skipped", 0),
                        counts.get("failed", 0),
                        ts(now),
                        operation_id,
                    ),
                )
            return
        failed = counts.get("failed", 0)
        done = counts.get("completed", 0) + counts.get("skipped", 0)
        status = "completed" if not failed else ("failed" if not done else "partial")
        with self._store.transaction() as db:
            changed = db.execute(
                "UPDATE bulk_operations SET status = ?, completed = ?, failed = ?, completed_at = "
                "?, "
                "lease_expires_at = NULL, updated_at = ? WHERE operation_id = ? "
                "AND status = 'running'",
                (status, done, failed, ts(now), ts(now), operation_id),
            ).rowcount
            if changed != 1:
                return
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.BULK_OPERATION_FINISHED,
                    actor=Actor.user(
                        int(operation["requested_by_id"] or 0),
                        str(operation["requested_by_login"] or "bulk operation"),
                    ),
                    account_id=int(operation["account_id"]),
                    operation=operation_id,
                    operation_type=str(operation["type"]),
                    result=status,
                    completed=counts.get("completed", 0),
                    skipped=counts.get("skipped", 0),
                    failed=failed,
                ),
            )
        self._audit.log_stored(stored)
