"""Organization security settings: versioned, audited, and weakening needs confirmation.

======================================  ===========  ==========================================
Setting                                 Default      Effect
======================================  ===========  ==========================================
``security_baseline``                   none         mandatory rule requirements applied to every
                                                     repository of the organization (a policy
                                                     layer above the organization policy)
``require_policy_approval``             off          policy changes are drafted, approved, then
                                                     published; direct saves are refused
``require_separate_approver``           on           the author of a policy draft cannot approve it
``exception_approval_min_severity``     ``high``     exceptions for rules at or above this severity
                                                     (and every organization- or group-wide
                                                     exception) need approval by someone else
``exception_max_days``                  90           longest allowed exception
``allow_permanent_exceptions``          off          permanent exceptions need this *and*
                                                     ``exceptions:approve``
``exception_warning_days``              7, 3, 1      expiry warnings (days before expiry)
``default_onboarding_mode``             ``enforce``  mode of newly discovered repositories
``auto_onboard_new_repositories``       on           newly discovered repositories are onboarded
                                                     (else they wait in ``discovered``)
``archived_repositories``               ``keep``     ``keep``: stay visible, no scheduled scans;
                                                     ``exclude``: excluded from onboarding
``rollout_auto_pause``                  on           pause a staged rollout that exceeds its
                                                     thresholds
``rollout_max_error_rate``              0.2          share of scan errors among enrolled scans
``rollout_max_block_rate``              0.5          share of blocked scans among enrolled scans
``rollout_min_scans``                   5            scans needed before thresholds apply
``rollout_auto_rollback``               off          roll back (not only pause) on a breach
``aggregate_violation_alerts``          off          e-mail/webhook violation alerts as one
                                                     organization digest per rule and hour
``timezone``                            ``UTC``      scan schedules and report dates
======================================  ===========  ==========================================

The default onboarding mode is ``enforce`` so that installing the GitHub App
keeps its Phase 5 meaning (the check fails on violations). Organizations that
want to observe first set ``monitor``.

A change that relaxes a control - turning approval off, allowing permanent
exceptions, lowering the baseline, onboarding in monitor mode, turning off
rollout safety - is a *weakening* change: it needs ``confirm``, a reason and a
sign-in from the last 15 minutes, and produces a critical notification.
"""

import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from commitguard.audit.models import Actor, AuditEventType
from commitguard.controlplane.access import Permission, Principal
from commitguard.controlplane.errors import (
    ConfirmationRequiredError,
    ConflictError,
    InputValidationError,
    ReauthenticationRequiredError,
)
from commitguard.controlplane.policies import REAUTHENTICATION_WINDOW
from commitguard.core.decision import Action
from commitguard.core.result import Severity
from commitguard.github.storage import SqliteStateStore
from commitguard.governance.common import MAX_REASON_CHARS, req_dt, require, text, ts
from commitguard.notifications.deduplication import domain_key
from commitguard.notifications.models import NotificationEvent, NotificationType
from commitguard.notifications.outbox import emit
from commitguard.policies.defaults import DEFAULT_POLICIES
from commitguard.policies.governance import RepositoryMode
from commitguard.services.audit import AuditService

type SettingsHook = Callable[[sqlite3.Connection, int, "OrganizationSettings"], None]


class OrganizationSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    security_baseline: dict[str, Action] = {}
    require_policy_approval: bool = False
    require_separate_approver: bool = True
    exception_approval_min_severity: Severity = Severity.HIGH
    exception_max_days: int = Field(default=90, ge=1, le=365)
    allow_permanent_exceptions: bool = False
    exception_warning_days: tuple[int, ...] = (7, 3, 1)
    default_onboarding_mode: RepositoryMode = RepositoryMode.ENFORCE
    auto_onboard_new_repositories: bool = True
    archived_repositories: Literal["keep", "exclude"] = "keep"
    rollout_auto_pause: bool = True
    rollout_max_error_rate: float = Field(default=0.2, ge=0.0, le=1.0)
    rollout_max_block_rate: float = Field(default=0.5, ge=0.0, le=1.0)
    rollout_min_scans: int = Field(default=5, ge=1, le=10_000)
    rollout_auto_rollback: bool = False
    aggregate_violation_alerts: bool = False
    timezone: str = "UTC"

    @field_validator("security_baseline")
    @classmethod
    def _baseline(cls, value: dict[str, Action]) -> dict[str, Action]:
        for rule, action in value.items():
            if rule not in DEFAULT_POLICIES:
                raise ValueError(f"unknown rule {rule!r} in the security baseline")
            if action is Action.ALLOW:
                raise ValueError("a baseline requirement must be warn or block")
        return dict(sorted(value.items()))

    @field_validator("exception_warning_days")
    @classmethod
    def _warnings(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if len(value) > 5 or any(not 1 <= day <= 90 for day in value):
            raise ValueError("at most 5 warning days, each between 1 and 90")
        return tuple(sorted(set(value), reverse=True))

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        if len(value) > 64:
            raise ValueError("unknown time zone")
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("unknown time zone") from None
        return value


DEFAULT_SETTINGS = OrganizationSettings()


def weakening_changes(old: OrganizationSettings, new: OrganizationSettings) -> list[str]:
    """Human-readable descriptions of every relaxed control."""
    changes = []
    for rule, action in old.security_baseline.items():
        after = new.security_baseline.get(rule)
        if after is None or after.rank < action.rank:
            changes.append(f"security baseline {rule}: {action.value} -> {after or 'removed'}")
    if old.require_policy_approval and not new.require_policy_approval:
        changes.append("policy approval no longer required")
    if old.require_separate_approver and not new.require_separate_approver:
        changes.append("authors may approve their own policy changes")
    if new.exception_approval_min_severity.rank > old.exception_approval_min_severity.rank:
        changes.append(
            "fewer exceptions need approval "
            f"({old.exception_approval_min_severity.value} -> "
            f"{new.exception_approval_min_severity.value})"
        )
    if new.exception_max_days > old.exception_max_days:
        changes.append(
            f"longer exceptions allowed ({old.exception_max_days} -> {new.exception_max_days} days)"
        )
    if new.allow_permanent_exceptions and not old.allow_permanent_exceptions:
        changes.append("permanent exceptions allowed")
    if (
        old.default_onboarding_mode is RepositoryMode.ENFORCE
        and new.default_onboarding_mode is RepositoryMode.MONITOR
    ):
        changes.append("new repositories onboard in monitor mode (not blocking)")
    if old.rollout_auto_pause and not new.rollout_auto_pause:
        changes.append("staged rollouts no longer pause automatically")
    if new.rollout_max_error_rate > old.rollout_max_error_rate:
        changes.append("higher rollout error threshold")
    if new.rollout_max_block_rate > old.rollout_max_block_rate:
        changes.append("higher rollout block threshold")
    return changes


@dataclass(frozen=True, slots=True)
class StoredSettings:
    account_id: int
    version: int  # 0: defaults, never saved
    settings: OrganizationSettings
    updated_at: datetime | None
    updated_by: str | None


class SettingsView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_id: int
    version: int
    settings: OrganizationSettings
    updated_at: datetime | None
    updated_by: str | None
    can_manage: bool


def load_settings(db: sqlite3.Connection | SqliteStateStore, account_id: int) -> StoredSettings:
    sql = "SELECT * FROM organization_settings WHERE account_id = ?"
    if isinstance(db, sqlite3.Connection):
        row = db.execute(sql, (int(account_id),)).fetchone()
    else:
        rows = db.query(sql, (int(account_id),))
        row = rows[0] if rows else None
    if row is None:
        return StoredSettings(int(account_id), 0, DEFAULT_SETTINGS, None, None)
    try:
        settings = OrganizationSettings.model_validate_json(str(row["document"]))
    except ValidationError:
        # A stored document that no longer validates must not silently relax
        # anything: fall back to defaults, which are the documented baseline.
        settings = DEFAULT_SETTINGS
    return StoredSettings(
        int(account_id),
        int(row["version"]),
        settings,
        req_dt(row["updated_at"]),
        row["updated_by_login"],
    )


class OrganizationSettingsService:
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
        self._hooks: list[SettingsHook] = []

    def add_hook(self, hook: SettingsHook) -> None:
        """Run ``hook`` inside the saving transaction (e.g. effective policy invalidation)."""
        self._hooks.append(hook)

    def get(self, account_id: int) -> StoredSettings:
        return load_settings(self._store, account_id)

    def view(self, principal: Principal, account_id: int) -> SettingsView:
        require(principal, Permission.ORGANIZATION_READ, account_id)
        stored = self.get(account_id)
        return SettingsView(
            organization_id=account_id,
            version=stored.version,
            settings=stored.settings,
            updated_at=stored.updated_at,
            updated_by=stored.updated_by,
            can_manage=principal.can(Permission.ORGANIZATION_MANAGE, account_id),
        )

    def update(
        self,
        principal: Principal,
        account_id: int,
        *,
        expected_version: object,
        changes: object,
        reason: object,
        confirm: object,
    ) -> SettingsView:
        require(principal, Permission.ORGANIZATION_MANAGE, account_id)
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 0
        ):
            raise InputValidationError(
                "expected_version must be a non-negative integer", field="expected_version"
            )
        if not isinstance(changes, Mapping) or not changes:
            raise InputValidationError("settings must be a non-empty object", field="settings")
        if not isinstance(confirm, bool):
            raise InputValidationError("confirm must be true or false", field="confirm")
        reason_text = text(reason, "reason", limit=MAX_REASON_CHARS)
        now = self._now()
        current = self.get(account_id)
        if current.version != expected_version:
            raise ConflictError(
                f"The settings were changed by someone else (now version {current.version}). "
                "Reload before saving."
            )
        merged: dict[str, Any] = {**current.settings.model_dump(mode="json"), **dict(changes)}
        try:
            updated = OrganizationSettings.model_validate(merged)
        except ValidationError as exc:
            error = exc.errors()[0]
            location = ".".join(str(part) for part in error.get("loc", ()))
            raise InputValidationError(
                f"Invalid setting {location or 'value'}: {error.get('msg', 'invalid value')}",
                field=f"settings.{location}" if location else "settings",
            ) from None
        changed = sorted(
            key
            for key in type(updated).model_fields
            if getattr(updated, key) != getattr(current.settings, key)
        )
        if not changed:
            raise InputValidationError("The settings are identical to the current version.")
        weakening = weakening_changes(current.settings, updated)
        if weakening:
            if not confirm:
                raise ConfirmationRequiredError(
                    "This change relaxes security controls and must be confirmed: "
                    + "; ".join(weakening),
                    changes=tuple(weakening),
                )
            if now - principal.authenticated_at > REAUTHENTICATION_WINDOW:
                raise ReauthenticationRequiredError()
            if reason_text is None:
                raise InputValidationError(
                    "A reason is required when relaxing security controls.", field="reason"
                )
        actor = Actor.user(principal.user_id, principal.login)
        document = updated.model_dump_json()
        with self._store.transaction() as db:
            row = db.execute(
                "SELECT version FROM organization_settings WHERE account_id = ?", (account_id,)
            ).fetchone()
            latest = int(row["version"]) if row else 0
            if latest != expected_version:
                raise ConflictError(
                    f"The settings were changed by someone else (now version {latest}). "
                    "Reload before saving."
                )
            db.execute(
                "INSERT INTO organization_settings (account_id, version, document, updated_at, "
                "updated_by_id, updated_by_login) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (account_id) DO UPDATE SET version = excluded.version, "
                "document = excluded.document, updated_at = excluded.updated_at, "
                "updated_by_id = excluded.updated_by_id, "
                "updated_by_login = excluded.updated_by_login",
                (account_id, latest + 1, document, ts(now), actor.id, actor.login),
            )
            stored = self._store.insert_audit_event(
                db,
                self._audit.build(
                    AuditEventType.ORGANIZATION_SETTINGS_CHANGED,
                    actor=actor,
                    account_id=account_id,
                    old_version=latest,
                    new_version=latest + 1,
                    changed=",".join(changed),
                    weakening="; ".join(weakening) or None,
                    baseline=json.dumps({k: v.value for k, v in updated.security_baseline.items()}),
                    reason=reason_text,
                ),
            )
            emit(
                db,
                NotificationEvent(
                    type=NotificationType.ORGANIZATION_SETTINGS_CHANGED,
                    account_id=account_id,
                    severity=Severity.CRITICAL if weakening else Severity.MEDIUM,
                    resource_type="organization",
                    resource_id=str(account_id),
                    dedup_key=domain_key(
                        NotificationType.ORGANIZATION_SETTINGS_CHANGED, account_id, latest + 1
                    ),
                    title=f"Organization security settings changed (v{latest + 1})",
                    body=(
                        f"{actor.login} changed: {', '.join(changed)}."
                        + (f" Relaxed controls: {'; '.join(weakening)}." if weakening else "")
                        + (f" Reason: {reason_text}" if reason_text else "")
                    ),
                    metadata={"new_version": latest + 1, "weakening": bool(weakening)},
                ),
                now,
            )
            for hook in self._hooks:
                hook(db, account_id, updated)
        self._audit.log_stored(stored)
        return self.view(principal, account_id)
