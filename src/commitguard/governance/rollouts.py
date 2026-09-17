"""Staged policy rollout: pilot repositories first, then wider, with safety thresholds.

::

    publish v15 with a rollout
        stage 1 (pilot)   10 repositories  ─ enrolled: they resolve v15
        stage 2           50 %             ─ the rest still resolve v14
        stage 3           100 %            ─ state becomes "active"

A rollout only decides **which version applies to which repository** while it
is in progress; the version itself is an ordinary immutable version. Resolution
is in :meth:`commitguard.governance.resolver.GovernanceResolver._version_for`:
an enrolled repository gets the new version, every other repository keeps the
previous one. Enrolling a stage invalidates exactly the repositories it adds.

States: ``pilot`` -> ``rollout`` -> ``active`` (every repository enrolled);
``paused`` at any point (manually or by a threshold), and ``rolled_back`` after
a rollback, which publishes a *new* version restoring the previous document
through the Phase 7 rollback path - history, audit and notifications are kept.

Safety thresholds (organization settings, or per rollout): once a stage has at
least ``min_scans`` completed scans of enrolled repositories, a share of scan
**errors** above ``max_error_rate`` or of **blocked** scans above
``max_block_rate`` pauses the rollout (``auto_pause``, on by default) and
notifies. Automatic *rollback* is off unless explicitly configured.

Progress is reported honestly: a rollout is complete only when every repository
in its scope is enrolled **and** its effective policy has been resolved with
the new version; repositories still stale, syncing or in error are reported as
such, never as done.
"""

import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from commitguard.audit.models import SYSTEM_ACTOR, Actor, AuditEventType
from commitguard.controlplane.access import Permission, Principal
from commitguard.controlplane.errors import (
    ConflictError,
    InputValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from commitguard.controlplane.policies import (
    OrganizationPolicyService,
    PolicyTarget,
    PolicyTargetType,
    PublishedPolicy,
    target_label,
)
from commitguard.controlplane.views import PolicyTargetView
from commitguard.core.result import Severity
from commitguard.github.storage import SqliteStateStore
from commitguard.governance.cache import group_member_ids, invalidate_repositories
from commitguard.governance.common import (
    MAX_REASON_CHARS,
    account_repositories,
    dt,
    is_hex_id,
    new_id,
    req_dt,
    require,
    text,
    ts,
)
from commitguard.governance.settings import load_settings
from commitguard.notifications.deduplication import domain_key
from commitguard.notifications.models import NotificationEvent, NotificationType
from commitguard.notifications.outbox import emit
from commitguard.observability.logging import get_logger
from commitguard.security.hashing import sha256_hex
from commitguard.services.audit import AuditService

log = get_logger(__name__)

MAX_STAGES = 10
IN_PROGRESS = ("pilot", "rollout", "paused")


class RolloutStageView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int
    name: str
    kind: str  # repositories | percent
    percent: int | None
    repositories: int  # planned size (explicit list) or enrolled so far
    enrolled: int
    state: str  # done | current | planned


class RolloutView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    organization_id: int
    target: PolicyTargetView
    from_version: int
    to_version: int
    state: str
    stages: tuple[RolloutStageView, ...]
    current_stage: int
    scope_repositories: int
    enrolled: int
    propagated: int  # enrolled and effective policy resolved with the new version
    scanned: int
    passed: int
    blocked: int
    errors: int
    complete: bool
    thresholds: dict[str, float]
    auto_pause: bool
    auto_rollback: bool
    paused_reason: str | None
    created_by: str | None
    created_at: datetime
    stage_started_at: datetime
    completed_at: datetime | None
    rolled_back_at: datetime | None
    rollback_version: int | None
    can_manage: bool


def parse_stages(raw: object) -> list[dict[str, Any]]:
    """``[{"repositories": [...]} | {"percent": 25}, ...]`` -> validated stages."""
    if not isinstance(raw, list) or not raw:
        raise InputValidationError("stages must be a non-empty list", field="stages")
    if len(raw) > MAX_STAGES:
        raise InputValidationError(f"at most {MAX_STAGES} stages", field="stages")
    stages: list[dict[str, Any]] = []
    last_percent = 0
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise InputValidationError("each stage is an object", field=f"stages.{index}")
        name = item.get("name")
        label = name if isinstance(name, str) and name.strip() else f"Stage {index + 1}"
        if "repositories" in item:
            ids = item["repositories"]
            if not isinstance(ids, list) or not ids or len(ids) > 5000:
                raise InputValidationError(
                    "stage repositories must be a list of repository IDs",
                    field=f"stages.{index}.repositories",
                )
            for value in ids:
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    raise InputValidationError(
                        "stage repositories must be repository IDs",
                        field=f"stages.{index}.repositories",
                    )
            stages.append(
                {"name": label[:100], "kind": "repositories", "repositories": sorted(set(ids))}
            )
        elif "percent" in item:
            percent = item["percent"]
            if not isinstance(percent, int) or isinstance(percent, bool) or not 1 <= percent <= 100:
                raise InputValidationError(
                    "stage percent must be between 1 and 100", field=f"stages.{index}.percent"
                )
            if percent < last_percent:
                raise InputValidationError(
                    "stage percentages must increase", field=f"stages.{index}.percent"
                )
            last_percent = percent
            stages.append({"name": label[:100], "kind": "percent", "percent": percent})
        else:
            raise InputValidationError(
                "a stage names repositories or a percent", field=f"stages.{index}"
            )
    if stages[-1].get("kind") == "percent" and stages[-1].get("percent") != 100:
        stages.append({"name": "All repositories", "kind": "percent", "percent": 100})
    return stages


def _order_key(rollout_id: str, repository_id: int) -> str:
    """Deterministic, stable order for percentage stages."""
    return sha256_hex(f"{rollout_id}:{repository_id}".encode())


class PolicyRolloutService:
    def __init__(
        self,
        store: SqliteStateStore,
        audit: AuditService,
        policies: OrganizationPolicyService,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._audit = audit
        self._policies = policies
        self._now = now

    # -- scope ------------------------------------------------------------ #
    def _scope_repositories(
        self, db: sqlite3.Connection, account_id: int, target: PolicyTarget
    ) -> list[int]:
        repositories = sorted(account_repositories(db, account_id))
        if target.type is PolicyTargetType.GROUP:
            members = set(group_member_ids(db, account_id, target.id))
            return [r for r in repositories if r in members]
        return repositories

    # -- creation --------------------------------------------------------- #
    def creator(
        self,
        principal: Principal,
        *,
        stages: list[dict[str, Any]],
        thresholds: dict[str, float] | None,
        auto_pause: bool | None,
        auto_rollback: bool | None,
    ) -> Callable[[sqlite3.Connection, PublishedPolicy], None]:
        """A hook that starts a rollout inside the publishing transaction."""

        def start(db: sqlite3.Connection, published: PublishedPolicy) -> None:
            self.start_in(
                db,
                published,
                actor=Actor.user(principal.user_id, principal.login),
                stages=stages,
                thresholds=thresholds,
                auto_pause=auto_pause,
                auto_rollback=auto_rollback,
            )

        return start

    def start_in(
        self,
        db: sqlite3.Connection,
        published: PublishedPolicy,
        *,
        actor: Actor,
        stages: list[dict[str, Any]],
        thresholds: dict[str, float] | None,
        auto_pause: bool | None,
        auto_rollback: bool | None,
    ) -> str:
        account_id = published.account_id
        target = published.target
        if target.type is PolicyTargetType.REPOSITORY:
            raise InputValidationError(
                "A repository policy applies to one repository; it has no staged rollout.",
                field="rollout",
            )
        settings = load_settings(db, account_id).settings
        limits = {
            "max_error_rate": settings.rollout_max_error_rate,
            "max_block_rate": settings.rollout_max_block_rate,
            "min_scans": float(settings.rollout_min_scans),
            **(thresholds or {}),
        }
        rollout_id = new_id()
        now = published.now
        db.execute(
            "INSERT INTO policy_rollouts (rollout_id, account_id, target_type, target_id, "
            "from_version, to_version, state, stages, current_stage, thresholds, auto_pause, "
            "auto_rollback, created_at, created_by_id, created_by_login, updated_at, "
            "stage_started_at) VALUES (?, ?, ?, ?, ?, ?, 'pilot', ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rollout_id,
                account_id,
                target.type.value,
                target.id,
                published.previous_version,
                published.version,
                json.dumps(stages),
                json.dumps(limits),
                1 if (settings.rollout_auto_pause if auto_pause is None else auto_pause) else 0,
                1
                if (settings.rollout_auto_rollback if auto_rollback is None else auto_rollback)
                else 0,
                ts(now),
                actor.id,
                actor.login,
                ts(now),
                ts(now),
            ),
        )
        enrolled = self._enroll_stage(db, rollout_id, account_id, target, stages, 0, now)
        self._store.insert_audit_event(
            db,
            self._audit.build(
                AuditEventType.POLICY_ROLLOUT_STARTED,
                actor=actor,
                account_id=account_id,
                rollout=rollout_id,
                target_type=target.type.value,
                target_id=target.id or None,
                from_version=published.previous_version,
                to_version=published.version,
                stages=len(stages),
                pilot_repositories=len(enrolled),
            ),
        )
        return rollout_id

    def _enroll_stage(
        self,
        db: sqlite3.Connection,
        rollout_id: str,
        account_id: int,
        target: PolicyTarget,
        stages: Sequence[dict[str, Any]],
        index: int,
        now: datetime,
    ) -> list[int]:
        scope = self._scope_repositories(db, account_id, target)
        already = {
            int(r["repository_id"])
            for r in db.execute(
                "SELECT repository_id FROM policy_rollout_repositories WHERE rollout_id = ?",
                (rollout_id,),
            ).fetchall()
        }
        stage = stages[index]
        if stage["kind"] == "repositories":
            wanted = [r for r in stage["repositories"] if r in set(scope) and r not in already]
        else:
            share = int(stage["percent"])
            ordered = sorted(scope, key=lambda r: _order_key(rollout_id, r))
            target_count = max(1, (len(ordered) * share + 99) // 100)
            wanted = [r for r in ordered[:target_count] if r not in already]
        for repository_id in wanted:
            db.execute(
                "INSERT OR IGNORE INTO policy_rollout_repositories (rollout_id, repository_id, "
                "stage, enrolled_at) VALUES (?, ?, ?, ?)",
                (rollout_id, repository_id, index, ts(now)),
            )
        invalidate_repositories(db, account_id, wanted, now)
        return wanted

    # -- reads ------------------------------------------------------------ #
    def _row(self, rollout_id: str) -> sqlite3.Row:
        if not is_hex_id(rollout_id):
            raise NotFoundError()
        rows = self._store.query(
            "SELECT * FROM policy_rollouts WHERE rollout_id = ?", (rollout_id,)
        )
        if not rows:
            raise NotFoundError()
        return rows[0]

    def list_rollouts(
        self, principal: Principal, account_id: int, *, active_only: bool = False
    ) -> list[RolloutView]:
        require(principal, Permission.POLICIES_READ, account_id)
        sql = "SELECT * FROM policy_rollouts WHERE account_id = ?"
        if active_only:
            sql += " AND state IN ('pilot', 'rollout', 'paused')"
        rows = self._store.query(sql + " ORDER BY created_at DESC LIMIT 100", (account_id,))
        return [self._view(row, principal) for row in rows]

    def get(self, principal: Principal, rollout_id: str) -> RolloutView:
        row = self._row(rollout_id)
        require(principal, Permission.POLICIES_READ, int(row["account_id"]))
        return self._view(row, principal)

    def _view(self, row: sqlite3.Row, principal: Principal) -> RolloutView:
        account_id = int(row["account_id"])
        rollout_id = str(row["rollout_id"])
        target = PolicyTarget(PolicyTargetType(row["target_type"]), str(row["target_id"]))
        stages = json.loads(str(row["stages"]))
        with self._store.transaction() as db:
            label = target_label(db, account_id, target) or "(removed)"
            scope = self._scope_repositories(db, account_id, target)
        enrolled_rows = self._store.query(
            "SELECT repository_id, stage FROM policy_rollout_repositories WHERE rollout_id = ?",
            (rollout_id,),
        )
        enrolled = [int(r["repository_id"]) for r in enrolled_rows]
        by_stage: dict[int, int] = {}
        for r in enrolled_rows:
            by_stage[int(r["stage"])] = by_stage.get(int(r["stage"]), 0) + 1
        state = str(row["state"])
        current_stage = int(row["current_stage"])
        stage_views = []
        for index, stage in enumerate(stages):
            stage_views.append(
                RolloutStageView(
                    index=index,
                    name=str(stage.get("name", f"Stage {index + 1}")),
                    kind=str(stage["kind"]),
                    percent=stage.get("percent"),
                    repositories=len(stage.get("repositories", []))
                    if stage["kind"] == "repositories"
                    else 0,
                    enrolled=by_stage.get(index, 0),
                    state="done"
                    if index < current_stage or state == "active"
                    else ("current" if index == current_stage else "planned"),
                )
            )
        stats = self._stage_statistics(rollout_id, int(row["to_version"]))
        propagated = self._propagated(account_id, enrolled, int(row["to_version"]))
        complete = state == "active" and len(enrolled) >= len(scope) and propagated >= len(scope)
        return RolloutView(
            id=rollout_id,
            organization_id=account_id,
            target=PolicyTargetView(type=target.type.value, id=target.id, label=label),
            from_version=int(row["from_version"]),
            to_version=int(row["to_version"]),
            state=state,
            stages=tuple(stage_views),
            current_stage=current_stage,
            scope_repositories=len(scope),
            enrolled=len(enrolled),
            propagated=propagated,
            scanned=stats["scanned"],
            passed=stats["passed"],
            blocked=stats["blocked"],
            errors=stats["errors"],
            complete=complete,
            thresholds=json.loads(str(row["thresholds"])),
            auto_pause=bool(row["auto_pause"]),
            auto_rollback=bool(row["auto_rollback"]),
            paused_reason=row["paused_reason"],
            created_by=row["created_by_login"],
            created_at=req_dt(row["created_at"]),
            stage_started_at=req_dt(row["stage_started_at"]),
            completed_at=dt(row["completed_at"]),
            rolled_back_at=dt(row["rolled_back_at"]),
            rollback_version=row["rollback_version"],
            can_manage=principal.can(Permission.POLICIES_PUBLISH, account_id),
        )

    def _propagated(self, account_id: int, enrolled: Sequence[int], version: int) -> int:
        if not enrolled:
            return 0
        rows = self._store.query(
            "SELECT document FROM repository_effective_policies WHERE account_id = ? "
            "AND state = 'up_to_date' AND repository_id IN (SELECT value FROM json_each(?))",
            (account_id, json.dumps(list(enrolled))),
        )
        count = 0
        for row in rows:
            try:
                document = json.loads(str(row["document"]))
            except (ValueError, TypeError):
                continue
            versions = document.get("versions", {})
            if versions.get("organization_policy") == version or version in (
                versions.get("groups") or {}
            ).values():
                count += 1
        return count

    def _stage_statistics(self, rollout_id: str, version: int) -> dict[str, int]:
        rows = self._store.query(
            "SELECT j.state, COUNT(*) AS n FROM scan_jobs j JOIN policy_rollout_repositories e "
            "ON e.repository_id = j.repository_id WHERE e.rollout_id = ? "
            "AND j.completed_at >= e.enrolled_at AND j.organization_policy_version = ? "
            "AND j.state IN ('passed', 'failed', 'error') GROUP BY j.state",
            (rollout_id, version),
        )
        counts = {str(r["state"]): int(r["n"]) for r in rows}
        return {
            "scanned": sum(counts.values()),
            "passed": counts.get("passed", 0),
            "blocked": counts.get("failed", 0),
            "errors": counts.get("error", 0),
        }

    # -- operations ------------------------------------------------------- #
    def _manageable(self, principal: Principal, rollout_id: str) -> sqlite3.Row:
        row = self._row(rollout_id)
        account_id = int(row["account_id"])
        require(principal, Permission.POLICIES_READ, account_id)
        if not principal.can(Permission.POLICIES_PUBLISH, account_id):
            raise PermissionDeniedError()
        return row

    def advance(self, principal: Principal, rollout_id: str) -> RolloutView:
        row = self._manageable(principal, rollout_id)
        state = str(row["state"])
        if state not in ("pilot", "rollout"):
            raise ConflictError(f"A {state} rollout cannot be expanded.")
        account_id = int(row["account_id"])
        target = PolicyTarget(PolicyTargetType(row["target_type"]), str(row["target_id"]))
        stages = json.loads(str(row["stages"]))
        index = int(row["current_stage"]) + 1
        now = self._now()
        actor = Actor.user(principal.user_id, principal.login)
        with self._store.transaction() as db:
            if index < len(stages):
                enrolled = self._enroll_stage(
                    db, rollout_id, account_id, target, stages, index, now
                )
                remaining = set(self._scope_repositories(db, account_id, target)) - {
                    int(r["repository_id"])
                    for r in db.execute(
                        "SELECT repository_id FROM policy_rollout_repositories "
                        "WHERE rollout_id = ?",
                        (rollout_id,),
                    ).fetchall()
                }
                finished = index == len(stages) - 1 and not remaining
            else:
                enrolled = []
                finished = True
            if finished:
                enrolled += self._enroll_remaining(db, rollout_id, account_id, target, index, now)
            db.execute(
                "UPDATE policy_rollouts SET current_stage = ?, state = ?, stage_started_at = ?, "
                "updated_at = ?, completed_at = ? WHERE rollout_id = ?",
                (
                    min(index, len(stages) - 1),
                    "active" if finished else "rollout",
                    ts(now),
                    ts(now),
                    ts(now) if finished else None,
                    rollout_id,
                ),
            )
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.POLICY_ROLLOUT_COMPLETED
                    if finished
                    else AuditEventType.POLICY_ROLLOUT_ADVANCED,
                    actor=actor,
                    account_id=account_id,
                    rollout=rollout_id,
                    stage=index,
                    repositories=len(enrolled),
                    to_version=int(row["to_version"]),
                ),
            )
        self._audit.log_stored(stored)
        return self.get(principal, rollout_id)

    def _enroll_remaining(
        self,
        db: sqlite3.Connection,
        rollout_id: str,
        account_id: int,
        target: PolicyTarget,
        stage: int,
        now: datetime,
    ) -> list[int]:
        scope = self._scope_repositories(db, account_id, target)
        already = {
            int(r["repository_id"])
            for r in db.execute(
                "SELECT repository_id FROM policy_rollout_repositories WHERE rollout_id = ?",
                (rollout_id,),
            ).fetchall()
        }
        remaining = [r for r in scope if r not in already]
        for repository_id in remaining:
            db.execute(
                "INSERT OR IGNORE INTO policy_rollout_repositories (rollout_id, repository_id, "
                "stage, enrolled_at) VALUES (?, ?, ?, ?)",
                (rollout_id, repository_id, stage, ts(now)),
            )
        invalidate_repositories(db, account_id, remaining, now)
        return remaining

    def pause(self, principal: Principal, rollout_id: str, reason: object) -> RolloutView:
        row = self._manageable(principal, rollout_id)
        if str(row["state"]) not in ("pilot", "rollout"):
            raise ConflictError(f"A {row['state']} rollout cannot be paused.")
        note = text(reason, "reason", limit=MAX_REASON_CHARS, required=True)
        self._pause_row(
            row, reason=note or "paused", actor=Actor.user(principal.user_id, principal.login)
        )
        return self.get(principal, rollout_id)

    def _pause_row(self, row: sqlite3.Row, *, reason: str, actor: Actor) -> None:
        now = self._now()
        rollout_id = str(row["rollout_id"])
        account_id = int(row["account_id"])
        with self._store.transaction() as db:
            changed = db.execute(
                "UPDATE policy_rollouts SET state = 'paused', paused_at = ?, paused_reason = ?, "
                "paused_from = state, updated_at = ? WHERE rollout_id = ? "
                "AND state IN ('pilot', 'rollout')",
                (ts(now), reason, ts(now), rollout_id),
            ).rowcount
            if changed != 1:
                raise ConflictError("The rollout was changed by someone else.")
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.POLICY_ROLLOUT_PAUSED,
                    actor=actor,
                    account_id=account_id,
                    rollout=rollout_id,
                    to_version=int(row["to_version"]),
                    reason=reason,
                ),
            )
            emit(
                db,
                NotificationEvent(
                    type=NotificationType.POLICY_ROLLOUT_FAILED,
                    account_id=account_id,
                    severity=Severity.HIGH,
                    resource_type="rollout",
                    resource_id=rollout_id,
                    dedup_key=domain_key(
                        NotificationType.POLICY_ROLLOUT_FAILED, account_id, rollout_id
                    ),
                    title=f"Policy rollout paused: v{row['to_version']}",
                    body=(
                        f"The staged rollout of policy v{row['to_version']} was paused: {reason}. "
                        "Enrolled repositories keep the new version; the rest keep "
                        f"v{row['from_version']}. Resume or roll back from the dashboard."
                    ),
                    metadata={"rollout": rollout_id, "reason": reason},
                ),
                now,
            )
        self._audit.log_stored(stored)

    def resume(self, principal: Principal, rollout_id: str) -> RolloutView:
        row = self._manageable(principal, rollout_id)
        if str(row["state"]) != "paused":
            raise ConflictError(f"The rollout is {row['state']}, not paused.")
        now = self._now()
        with self._store.transaction() as db:
            db.execute(
                "UPDATE policy_rollouts SET state = COALESCE(paused_from, 'rollout'), "
                "paused_at = NULL, paused_reason = NULL, paused_from = NULL, updated_at = ? "
                "WHERE rollout_id = ? AND state = 'paused'",
                (ts(now), rollout_id),
            )
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.POLICY_ROLLOUT_RESUMED,
                    actor=Actor.user(principal.user_id, principal.login),
                    account_id=int(row["account_id"]),
                    rollout=rollout_id,
                ),
            )
        self._audit.log_stored(stored)
        return self.get(principal, rollout_id)

    def rollback(
        self, principal: Principal, rollout_id: str, *, reason: object, confirm: object
    ) -> RolloutView:
        row = self._row(rollout_id)
        account_id = int(row["account_id"])
        require(principal, Permission.POLICIES_ROLLBACK, account_id)
        if str(row["state"]) not in IN_PROGRESS:
            raise ConflictError(f"A {row['state']} rollout cannot be rolled back.")
        self._rollback_row(
            row,
            actor=Actor.user(principal.user_id, principal.login),
            authenticated_at=principal.authenticated_at,
            reason=text(reason, "reason", limit=MAX_REASON_CHARS, required=True) or "",
            confirm=confirm is True,
        )
        return self.get(principal, rollout_id)

    def _rollback_row(
        self,
        row: sqlite3.Row,
        *,
        actor: Actor,
        authenticated_at: datetime,
        reason: str,
        confirm: bool,
    ) -> None:
        account_id = int(row["account_id"])
        rollout_id = str(row["rollout_id"])
        target = PolicyTarget(PolicyTargetType(row["target_type"]), str(row["target_id"]))
        from_version = int(row["from_version"])
        if from_version < 1:
            raise ConflictError(
                "This was the first policy version; there is nothing to roll back to. "
                "Publish a new version instead."
            )
        current = self._policies.current(account_id, target)

        def finish(db: sqlite3.Connection, published: PublishedPolicy) -> None:
            db.execute(
                "UPDATE policy_rollouts SET state = 'rolled_back', rolled_back_at = ?, "
                "rollback_version = ?, updated_at = ? WHERE rollout_id = ?",
                (ts(published.now), published.version, ts(published.now), rollout_id),
            )
            # Every repository of the scope resolves the restored version again.
            invalidate_repositories(
                db, account_id, self._scope_repositories(db, account_id, target), published.now
            )
            self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.POLICY_ROLLOUT_ROLLED_BACK,
                    actor=actor,
                    account_id=account_id,
                    rollout=rollout_id,
                    to_version=int(row["to_version"]),
                    restored_version=from_version,
                    new_version=published.version,
                    reason=reason,
                ),
            )

        self._policies.rollback(
            account_id=account_id,
            actor=actor,
            authenticated_at=authenticated_at,
            target_version=from_version,
            expected_current_version=current.version,
            reason=reason,
            confirm=confirm,
            target=target,
            hooks=[finish],
        )

    # -- automatic safety -------------------------------------------------- #
    def evaluate(self) -> int:
        """Pause (or roll back) rollouts whose thresholds are exceeded. Returns actions taken."""
        rows = self._store.query(
            "SELECT * FROM policy_rollouts WHERE state IN ('pilot', 'rollout') AND auto_pause = 1"
        )
        acted = 0
        for row in rows:
            thresholds = json.loads(str(row["thresholds"]))
            stats = self._stage_statistics(str(row["rollout_id"]), int(row["to_version"]))
            scanned = stats["scanned"]
            if scanned < int(thresholds.get("min_scans", 5)):
                continue
            error_rate = stats["errors"] / scanned
            block_rate = stats["blocked"] / scanned
            breach = None
            if error_rate > float(thresholds.get("max_error_rate", 0.2)):
                breach = f"{stats['errors']} of {scanned} scans could not be completed"
            elif block_rate > float(thresholds.get("max_block_rate", 0.5)):
                breach = f"{stats['blocked']} of {scanned} scans were blocked"
            if breach is None:
                continue
            try:
                self._pause_row(
                    row, reason=f"safety threshold exceeded: {breach}", actor=SYSTEM_ACTOR
                )
                acted += 1
                if row["auto_rollback"]:
                    self._rollback_row(
                        self._row(str(row["rollout_id"])),
                        actor=SYSTEM_ACTOR,
                        authenticated_at=self._now(),
                        reason=f"automatic rollback: {breach}",
                        confirm=True,
                    )
            except ConflictError:
                continue
        return acted
