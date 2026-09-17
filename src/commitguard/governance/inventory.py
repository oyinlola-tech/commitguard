"""Repository onboarding: discovery, onboarding state and enforcement mode.

A repository's state has several independent dimensions, and they are never
merged into one label:

==============  ===================================================================
Dimension       Values
==============  ===================================================================
connection      ``connected`` (installation active, repository listed) /
                ``suspended`` / ``disconnected``
archived        GitHub's ``archived`` flag, from the API
onboarding      ``discovered`` (seen, not yet confirmed by an administrator) /
                ``onboarded`` / ``excluded`` (archived repositories, when the
                organization excludes them)
mode            ``enforce`` (block according to policy) / ``monitor`` (scan, record
                and alert, but report ``block`` as ``warn`` so checks do not fail)
enforcement     ``protected`` / ``at_risk`` / ``unprotected`` / ... (evidence from
                GitHub, :func:`commitguard.controlplane.queries.protection_for`)
policy          effective policy propagation: ``up_to_date`` / ``stale`` /
                ``syncing`` / ``error`` / ``pending``
==============  ===================================================================

So "connected but unprotected, onboarded, monitor" is a valid combination.

Organization governance applies to every repository of the organization
whatever its onboarding state: not onboarding a repository never removes a
mandatory requirement. A repository with no onboarding row yet (discovered
before the discovery listener ran) uses the organization's default mode.

Discovery: GitHub's installation webhooks and repository synchronisation report
repositories by immutable ID; :meth:`RepositoryInventory.discovered` records them
with the organization's defaults. Changing the mode needs ``repositories:manage``
and ``confirm``: switching to ``enforce`` can make GitHub checks fail and stop
merges; switching to ``monitor`` stops blocking and also needs a reason.
"""

import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from commitguard.audit.models import SYSTEM_ACTOR, Actor, AuditEvent, AuditEventType
from commitguard.controlplane.access import Permission, Principal
from commitguard.controlplane.errors import ConfirmationRequiredError, InputValidationError
from commitguard.github.identifiers import RepositoryRef
from commitguard.github.storage import SqliteStateStore
from commitguard.governance.cache import invalidate_repositories
from commitguard.governance.common import (
    MAX_REASON_CHARS,
    account_repositories,
    require,
    require_visible_repositories,
    text,
    ts,
)
from commitguard.governance.settings import load_settings
from commitguard.observability.logging import get_logger
from commitguard.policies.governance import RepositoryMode
from commitguard.services.audit import AuditService

log = get_logger(__name__)

ENFORCE_WARNING = (
    "Enforce mode makes CommitGuard checks fail on policy violations. Where branch "
    "protection requires the check, this prevents merges."
)
MONITOR_WARNING = (
    "Monitor mode stops CommitGuard from blocking: violations are recorded and alerted, "
    "but checks report them as warnings."
)


@dataclass(frozen=True, slots=True)
class RepositoryGovernance:
    repository_id: int
    onboarding: str
    mode: RepositoryMode
    recorded: bool  # False: no row yet, organization defaults shown


def governance_state(
    db: sqlite3.Connection | SqliteStateStore, account_id: int, repository_id: int
) -> RepositoryGovernance:
    sql = (
        "SELECT onboarding, mode FROM repository_governance WHERE account_id = ? "
        "AND repository_id = ?"
    )
    params = (int(account_id), int(repository_id))
    if isinstance(db, sqlite3.Connection):
        row = db.execute(sql, params).fetchone()
    else:
        rows = db.query(sql, params)
        row = rows[0] if rows else None
    if row is not None:
        return RepositoryGovernance(
            int(repository_id), str(row["onboarding"]), RepositoryMode(row["mode"]), True
        )
    settings = load_settings(db, account_id).settings
    return RepositoryGovernance(
        int(repository_id), "discovered", settings.default_onboarding_mode, False
    )


def governance_states(store: SqliteStateStore, account_id: int) -> dict[int, RepositoryGovernance]:
    return {
        int(row["repository_id"]): RepositoryGovernance(
            int(row["repository_id"]), str(row["onboarding"]), RepositoryMode(row["mode"]), True
        )
        for row in store.query(
            "SELECT repository_id, onboarding, mode FROM repository_governance "
            "WHERE account_id = ?",
            (int(account_id),),
        )
    }


class RepositoryInventory:
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

    # -- discovery (GitHub events and synchronisation) -------------------- #
    def discovered(
        self, account_id: int, installation_id: int, repositories: Sequence[RepositoryRef]
    ) -> int:
        """Record newly seen repositories with the organization's onboarding defaults."""
        now = self._now()
        stored: list[AuditEvent] = []
        with self._store.transaction() as db:
            settings = load_settings(db, account_id).settings
            known = account_repositories(db, account_id)
            new_ids: list[int] = []
            onboarded: list[int] = []
            excluded: list[int] = []
            for repository in repositories:
                onboarding = (
                    "onboarded" if settings.auto_onboard_new_repositories else "discovered"
                )
                info = known.get(repository.id)
                exclude_archived = settings.archived_repositories == "exclude"
                if info is not None and info.archived and exclude_archived:
                    onboarding = "excluded"
                cursor = db.execute(
                    "INSERT OR IGNORE INTO repository_governance (account_id, repository_id, "
                    "onboarding, mode, discovered_at, onboarded_at, onboarded_by, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        account_id,
                        repository.id,
                        onboarding,
                        settings.default_onboarding_mode.value,
                        ts(now),
                        ts(now) if onboarding == "onboarded" else None,
                        "organization default" if onboarding == "onboarded" else None,
                        ts(now),
                    ),
                )
                if cursor.rowcount:
                    new_ids.append(repository.id)
                    if onboarding == "onboarded":
                        onboarded.append(repository.id)
                    elif onboarding == "excluded":
                        excluded.append(repository.id)
            if new_ids:
                stored.append(
                    self._store.insert_audit_event(
                        db,
                        self._audit.build(
                            AuditEventType.REPOSITORY_DISCOVERED,
                            actor=SYSTEM_ACTOR,
                            account_id=account_id,
                            installation_id=installation_id,
                            repositories=len(new_ids),
                            repository_ids=",".join(str(i) for i in new_ids[:50]),
                            mode=settings.default_onboarding_mode.value,
                        ),
                    )
                )
            if onboarded:
                stored.append(
                    self._store.insert_audit_event(
                        db,
                        self._audit.build(
                            AuditEventType.REPOSITORY_ONBOARDED,
                            actor=SYSTEM_ACTOR,
                            account_id=account_id,
                            installation_id=installation_id,
                            repositories=len(onboarded),
                            mode=settings.default_onboarding_mode.value,
                            source="organization default",
                        ),
                    )
                )
            if excluded:
                stored.append(
                    self._store.insert_audit_event(
                        db,
                        self._audit.build(
                            AuditEventType.REPOSITORY_EXCLUDED,
                            actor=SYSTEM_ACTOR,
                            account_id=account_id,
                            repositories=len(excluded),
                            reason="archived repository",
                        ),
                    )
                )
        for event in stored:
            self._audit.log_stored(event)
        return len(new_ids)

    # -- administrator actions -------------------------------------------- #
    def onboard(
        self,
        principal: Principal,
        account_id: int,
        repository_ids: Sequence[int],
        *,
        mode: object,
        confirm: object,
        reason: object = None,
    ) -> list[int]:
        """Onboard repositories in ``mode``. Returns the repositories that changed."""
        require(principal, Permission.REPOSITORIES_MANAGE, account_id)
        target = self.parse_mode(mode)
        ids = require_visible_repositories(self._store, principal, account_id, repository_ids)
        reason_text = text(reason, "reason", limit=MAX_REASON_CHARS)
        self._confirm_mode(account_id, ids, target, confirm, reason_text)
        actor = Actor.user(principal.user_id, principal.login)
        with self._store.transaction() as db:
            changed, events = self.apply_in(
                db, account_id, ids, mode=target, actor=actor, onboard=True, reason=reason_text
            )
        for event in events:
            self._audit.log_stored(event)
        return changed

    @staticmethod
    def parse_mode(mode: object) -> RepositoryMode:
        if not isinstance(mode, str) or mode not in {m.value for m in RepositoryMode}:
            raise InputValidationError("mode must be monitor or enforce", field="mode")
        return RepositoryMode(mode)

    def _confirm_mode(
        self,
        account_id: int,
        ids: Sequence[int],
        target: RepositoryMode,
        confirm: object,
        reason: str | None,
    ) -> None:
        current = {i: governance_state(self._store, account_id, i).mode for i in ids}
        changing = [i for i, m in current.items() if m is not target]
        if not changing:
            return
        if confirm is not True:
            raise ConfirmationRequiredError(
                ENFORCE_WARNING if target is RepositoryMode.ENFORCE else MONITOR_WARNING
            )
        if target is RepositoryMode.MONITOR and reason is None:
            raise InputValidationError(
                "A reason is required to stop blocking (monitor mode).", field="reason"
            )

    def set_mode(
        self,
        principal: Principal,
        account_id: int,
        repository_ids: Sequence[int],
        *,
        mode: object,
        confirm: object,
        reason: object = None,
    ) -> list[int]:
        require(principal, Permission.REPOSITORIES_MANAGE, account_id)
        target = self.parse_mode(mode)
        ids = require_visible_repositories(self._store, principal, account_id, repository_ids)
        reason_text = text(reason, "reason", limit=MAX_REASON_CHARS)
        self._confirm_mode(account_id, ids, target, confirm, reason_text)
        actor = Actor.user(principal.user_id, principal.login)
        with self._store.transaction() as db:
            changed, events = self.apply_in(
                db, account_id, ids, mode=target, actor=actor, onboard=False, reason=reason_text
            )
        for event in events:
            self._audit.log_stored(event)
        return changed

    def apply_in(
        self,
        db: sqlite3.Connection,
        account_id: int,
        repository_ids: Sequence[int],
        *,
        mode: RepositoryMode | None,
        actor: Actor,
        onboard: bool,
        reason: str | None = None,
    ) -> tuple[list[int], list[AuditEvent]]:
        """Onboard and/or set the mode inside a caller's transaction (bulk operations too)."""
        now = self._now()
        known = account_repositories(db, account_id)
        changed_mode: list[int] = []
        newly_onboarded: list[int] = []
        for repository_id in repository_ids:
            if repository_id not in known:
                continue
            current = governance_state(db, account_id, repository_id)
            target_mode = mode or current.mode
            onboarding = "onboarded" if onboard else current.onboarding
            if (
                current.recorded
                and current.mode is target_mode
                and current.onboarding == onboarding
            ):
                continue
            db.execute(
                "INSERT INTO repository_governance (account_id, repository_id, onboarding, mode, "
                "discovered_at, onboarded_at, onboarded_by, mode_changed_at, mode_changed_by, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (account_id, repository_id) DO UPDATE SET "
                "onboarding = excluded.onboarding, mode = excluded.mode, "
                "onboarded_at = COALESCE(excluded.onboarded_at, "
                "repository_governance.onboarded_at), "
                "onboarded_by = COALESCE(excluded.onboarded_by, "
                "repository_governance.onboarded_by), "
                "mode_changed_at = COALESCE(excluded.mode_changed_at, "
                "repository_governance.mode_changed_at), mode_changed_by = "
                "COALESCE(excluded.mode_changed_by, repository_governance.mode_changed_by), "
                "updated_at = excluded.updated_at",
                (
                    account_id,
                    repository_id,
                    onboarding,
                    target_mode.value,
                    ts(now),
                    ts(now) if onboard and current.onboarding != "onboarded" else None,
                    actor.login if onboard and current.onboarding != "onboarded" else None,
                    ts(now) if current.mode is not target_mode else None,
                    actor.login if current.mode is not target_mode else None,
                    ts(now),
                ),
            )
            if current.mode is not target_mode:
                changed_mode.append(repository_id)
            if onboard and current.onboarding != "onboarded":
                newly_onboarded.append(repository_id)
        events: list[AuditEvent] = []
        if changed_mode:
            invalidate_repositories(db, account_id, changed_mode, now)
            events.append(
                self._store.insert_audit_event(
                    db,
                    self._audit.build(
                        AuditEventType.REPOSITORY_MODE_CHANGED,
                        actor=actor,
                        account_id=account_id,
                        mode=(mode or RepositoryMode.ENFORCE).value,
                        repositories=len(changed_mode),
                        repository_ids=json.dumps(changed_mode[:50]),
                        reason=reason,
                    ),
                )
            )
        if newly_onboarded:
            events.append(
                self._store.insert_audit_event(
                    db,
                    self._audit.build(
                        AuditEventType.REPOSITORY_ONBOARDED,
                        actor=actor,
                        account_id=account_id,
                        repositories=len(newly_onboarded),
                        repository_ids=json.dumps(newly_onboarded[:50]),
                        mode=mode.value if mode else None,
                    ),
                )
            )
        return sorted(set(changed_mode) | set(newly_onboarded)), events
