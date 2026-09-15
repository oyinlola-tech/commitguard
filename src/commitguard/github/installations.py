"""Installation lifecycle and repository authorization.

Two sources of truth, used for different decisions:

* **Stored installation state** (from ``installation`` /
  ``installation_repositories`` webhooks) gives fast *negative* answers: a
  deleted or suspended installation is rejected before anything else happens,
  and its mirrors and cached tokens are discarded.
* **GitHub itself** gives the authoritative *positive* answer: before any
  repository access the worker mints an installation token down-scoped to that
  single repository (GitHub refuses if the installation does not cover it) and
  looks the repository up by its immutable ID with that token. A missing or
  missed webhook therefore cannot grant access, and a payload naming an
  unrelated installation, owner or repository gets nothing.

Connection state transitions are detected against the stored state, inside the
same transaction that applies them:

======================================  =============================================
Transition                              Notification
======================================  =============================================
active -> suspended / deleted           ``installation_disconnected`` (enforcement
                                        at risk)
suspended -> active (unsuspend)         ``installation_reconnected``
new installation for an account whose   ``installation_reconnected``
previous installation was removed
suspended -> deleted, deleted ->        none: already disconnected
deleted, repeated events
======================================  =============================================

Webhooks can arrive out of order. An installation stored as ``deleted`` is
not revived by a late ``suspend``, ``unsuspend`` or ``new_permissions_accepted``
event; only ``created`` makes it active again.
"""

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from commitguard.audit.models import GITHUB_ACTOR, Actor, AuditEventType
from commitguard.core.result import Severity
from commitguard.github.auth import InstallationToken, InstallationTokenProvider
from commitguard.github.client import GitHubClient, InstallationInfo
from commitguard.github.errors import (
    AuthenticationError,
    AuthorizationError,
    GitHubAPIError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubUnauthorizedError,
)
from commitguard.github.events import (
    InstallationAction,
    InstallationEvent,
    InstallationRepositoriesEvent,
    RepositoriesAction,
)
from commitguard.github.identifiers import AccountType, RepositoryRef
from commitguard.github.repositories import MirrorManager
from commitguard.github.storage import InstallationRecord, InstallationState, SqliteStateStore
from commitguard.notifications.deduplication import domain_key
from commitguard.notifications.models import NotificationEvent, NotificationType
from commitguard.notifications.outbox import emit
from commitguard.observability.logging import get_logger
from commitguard.services.audit import AuditService

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RepositorySync:
    installation_id: int
    repositories: tuple[RepositoryRef, ...]
    added: tuple[RepositoryRef, ...]
    removed: tuple[RepositoryRef, ...]
    synced_at: datetime


@dataclass(frozen=True, slots=True)
class AuthorizedRepository:
    installation_id: int
    repository: RepositoryRef  # canonical owner/name from the API
    token: InstallationToken
    default_branch: str | None


class InstallationService:
    def __init__(
        self,
        store: SqliteStateStore,
        tokens: InstallationTokenProvider,
        client: GitHubClient,
        mirrors: MirrorManager,
        audit: AuditService,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._tokens = tokens
        self._client = client
        self._mirrors = mirrors
        self._audit = audit
        self._now = now
        self._discovery_listeners: list[Callable[[int, int, Sequence[RepositoryRef]], None]] = []

    def add_discovery_listener(
        self, listener: Callable[[int, int, Sequence[RepositoryRef]], None]
    ) -> None:
        """Call ``listener(account_id, installation_id, repositories)`` for listed repositories.

        Organization governance uses it to record newly discovered repositories
        and apply the organization's onboarding defaults. A listener failure is
        logged and never fails the GitHub event.
        """
        self._discovery_listeners.append(listener)

    def _discovered(
        self, account_id: int, installation_id: int, repositories: Sequence[RepositoryRef]
    ) -> None:
        if not repositories:
            return
        for listener in self._discovery_listeners:
            try:
                listener(account_id, installation_id, repositories)
            except Exception as exc:  # noqa: BLE001 - discovery must not fail the event
                log.error("repository_discovery_listener_failed", error_type=type(exc).__name__)

    # -- webhooks -------------------------------------------------------- #
    def handle_installation(self, event: InstallationEvent) -> None:
        now = self._now()
        existing = self._store.get_installation(event.installation_id)
        record = InstallationRecord(
            installation_id=event.installation_id,
            account_id=event.account.id,
            account_login=event.account.login,
            account_type=event.account.type,
            repository_selection=event.repository_selection,
            state=InstallationState.ACTIVE,
            permissions=event.permissions,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        installation_id = event.installation_id
        deleted = existing is not None and existing.state is InstallationState.DELETED
        if deleted and event.action is not InstallationAction.CREATED:
            log.info("installation_event_after_removal_ignored", action=event.action.value)
            return
        before = existing.state if existing else None
        if event.action is InstallationAction.CREATED:
            self._store.replace_repositories(event.installation_id, event.repositories, now)
            self._apply(
                record,
                before,
                AuditEventType.INSTALLATION_CREATED,
                actor=GITHUB_ACTOR,
                installation_id=installation_id,
                account_id=event.account.id,
                account_type=event.account.type.value,
                repository_selection=event.repository_selection,
                repositories=len(event.repositories),
            )
            self._discovered(event.account.id, installation_id, event.repositories)
        elif event.action is InstallationAction.DELETED:
            repositories = len(self._store.list_repositories(installation_id))
            self._apply(
                record.model_copy(update={"state": InstallationState.DELETED}),
                before,
                AuditEventType.INSTALLATION_REMOVED,
                affected=repositories,
                actor=GITHUB_ACTOR,
                installation_id=installation_id,
            )
            self._tokens.invalidate(event.installation_id)
            self._mirrors.remove_installation(event.installation_id)
        elif event.action is InstallationAction.SUSPEND:
            self._tokens.invalidate(event.installation_id)
            self._apply(
                record.model_copy(update={"state": InstallationState.SUSPENDED}),
                before,
                AuditEventType.INSTALLATION_SUSPENDED,
                actor=GITHUB_ACTOR,
                installation_id=installation_id,
            )
        elif event.action is InstallationAction.UNSUSPEND:
            self._apply(
                record,
                before,
                AuditEventType.INSTALLATION_UNSUSPENDED,
                actor=GITHUB_ACTOR,
                installation_id=installation_id,
            )
        else:  # new_permissions_accepted
            state = existing.state if existing else InstallationState.ACTIVE
            self._tokens.invalidate(event.installation_id)
            self._apply(
                record.model_copy(update={"state": state}),
                before,
                AuditEventType.INSTALLATION_PERMISSIONS_UPDATED,
                actor=GITHUB_ACTOR,
                installation_id=installation_id,
            )

    def _apply(
        self,
        record: InstallationRecord,
        before: InstallationState | None,
        audit_type: AuditEventType,
        *,
        affected: int | None = None,
        actor: Actor,
        installation_id: int,
        **data: Any,
    ) -> None:
        """Store the installation state, its audit event and any transition notification."""
        now = self._now()
        after = record.state
        with self._store.transaction() as db:
            self._store.upsert_installation_in(db, record)
            if after is InstallationState.DELETED:
                self._store.set_installation_state_in(db, installation_id, after, now)
            repositories = affected
            if repositories is None:
                repositories = int(
                    db.execute(
                        "SELECT COUNT(*) AS n FROM installation_repositories "
                        "WHERE installation_id = ?",
                        (installation_id,),
                    ).fetchone()["n"]
                )
            notification = self._transition_notification(db, record, before, repositories)
            audit = self._store.insert_audit_event(
                db,
                self._audit.build(
                    audit_type,
                    actor=actor,
                    installation_id=installation_id,
                    account_id=record.account_id,
                    **{k: v for k, v in data.items() if k != "account_id"},
                ),
            )
            if notification is not None:
                emit(db, notification, now)
        self._audit.log_stored(audit)

    @staticmethod
    def _transition_notification(
        db: sqlite3.Connection,
        record: InstallationRecord,
        before: InstallationState | None,
        repositories: int,
    ) -> NotificationEvent | None:
        after = record.state
        account = record.account_login
        if before is InstallationState.ACTIVE and after in (
            InstallationState.SUSPENDED,
            InstallationState.DELETED,
        ):
            how = "uninstalled" if after is InstallationState.DELETED else "suspended"
            return NotificationEvent(
                type=NotificationType.INSTALLATION_DISCONNECTED,
                account_id=record.account_id,
                severity=Severity.CRITICAL,
                installation_id=record.installation_id,
                resource_type="installation",
                resource_id=str(record.installation_id),
                dedup_key=domain_key(
                    NotificationType.INSTALLATION_DISCONNECTED, record.installation_id
                ),
                title=f"CommitGuard GitHub installation disconnected: {account}",
                body=(
                    f"The CommitGuard GitHub App was {how} for {account}. Affected repositories: "
                    f"{repositories}. GitHub enforcement: AT RISK - CommitGuard checks no longer "
                    "run, and required checks may block or stop protecting merges. Action: "
                    "reinstall or unsuspend the CommitGuard GitHub App."
                ),
                metadata={
                    "installation_id": record.installation_id,
                    "account": account,
                    "state": after.value,
                    "affected_repositories": repositories,
                    "enforcement": "at_risk",
                },
            )
        reconnected = before is InstallationState.SUSPENDED and after is InstallationState.ACTIVE
        if before is None and after is InstallationState.ACTIVE:
            previous = db.execute(
                "SELECT 1 FROM installations WHERE account_id = ? AND installation_id != ? "
                "AND state = 'deleted' LIMIT 1",
                (record.account_id, record.installation_id),
            ).fetchone()
            reconnected = previous is not None
        if before is InstallationState.DELETED and after is InstallationState.ACTIVE:
            reconnected = True
        if not reconnected:
            return None
        return NotificationEvent(
            type=NotificationType.INSTALLATION_RECONNECTED,
            account_id=record.account_id,
            severity=Severity.LOW,
            installation_id=record.installation_id,
            resource_type="installation",
            resource_id=str(record.installation_id),
            dedup_key=domain_key(NotificationType.INSTALLATION_RECONNECTED, record.account_id),
            title=f"CommitGuard GitHub installation restored: {account}",
            body=(
                f"The CommitGuard GitHub App is available again for {account}. Affected "
                f"repositories: {repositories}. GitHub enforcement: RESTORED for new events. "
                "Commits pushed while disconnected were not checked by the App; re-run the "
                "CommitGuard check on open pull requests."
            ),
            metadata={
                "installation_id": record.installation_id,
                "account": account,
                "affected_repositories": repositories,
                "enforcement": "restored",
            },
        )

    def handle_repositories(self, event: InstallationRepositoriesEvent) -> None:
        now = self._now()
        existing = self._store.get_installation(event.installation_id)
        if existing is not None and existing.state is InstallationState.DELETED:
            return  # late event for a removed installation
        if existing is None:
            self._store.upsert_installation(
                InstallationRecord(
                    installation_id=event.installation_id,
                    account_id=event.account.id,
                    account_login=event.account.login,
                    account_type=event.account.type,
                    repository_selection=event.repository_selection,
                    state=InstallationState.ACTIVE,
                    created_at=now,
                    updated_at=now,
                )
            )
        if event.action is RepositoriesAction.ADDED:
            self._store.add_repositories(event.installation_id, event.added, now)
            self._audit.record(
                AuditEventType.REPOSITORIES_ADDED,
                actor=GITHUB_ACTOR,
                installation_id=event.installation_id,
                repositories=len(event.added),
            )
            account_id = existing.account_id if existing is not None else event.account.id
            self._discovered(account_id, event.installation_id, event.added)
        else:
            removed = [r.id for r in event.removed]
            self._store.remove_repositories(event.installation_id, removed, now)
            for repository_id in removed:
                self._tokens.invalidate(event.installation_id, repository_id)
                self._mirrors.remove_repository(event.installation_id, repository_id)
            self._audit.record(
                AuditEventType.REPOSITORIES_REMOVED,
                actor=GITHUB_ACTOR,
                installation_id=event.installation_id,
                repositories=len(removed),
            )

    # -- authorization --------------------------------------------------- #
    def denial_reason(self, installation_id: int) -> str | None:
        """A reason to reject events for this installation without calling GitHub."""
        record = self._store.get_installation(installation_id)
        if record is None:
            return None  # unknown here (e.g. installed before this service ran): ask GitHub
        if record.state is InstallationState.DELETED:
            return "installation removed"
        if record.state is InstallationState.SUSPENDED:
            return "installation suspended"
        return None

    def ensure_installation_record(self, installation_id: int) -> InstallationRecord:
        """The stored installation, fetched from GitHub when this service has not seen it.

        An App installed before the service first ran has no ``installation``
        webhook on record; the dashboard needs its account to attribute data to a
        tenant, so the details are read with the App JWT and stored.
        """
        existing = self._store.get_installation(installation_id)
        if existing is not None:
            return existing
        info = self._fetch_installation(installation_id)
        record = self._record_from(info, None)
        self._store.upsert_installation(record)
        return record

    def _fetch_installation(self, installation_id: int) -> InstallationInfo:
        try:
            return self._client.get_installation(self._tokens.app_jwt(), installation_id)
        except GitHubUnauthorizedError:
            raise AuthenticationError("GitHub rejected the App credentials") from None
        except GitHubNotFoundError:
            raise AuthorizationError("GitHub App installation not found") from None

    def _record_from(
        self, info: InstallationInfo, existing: InstallationRecord | None
    ) -> InstallationRecord:
        now = self._now()
        state = InstallationState.SUSPENDED if info.suspended_at else InstallationState.ACTIVE
        return InstallationRecord(
            installation_id=info.id,
            account_id=info.account.id,
            account_login=info.account.login,
            account_type=AccountType(info.account.type),
            repository_selection=info.repository_selection,
            state=state,
            permissions=info.permissions,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )

    def sync_repositories(self, installation_id: int, actor: Actor) -> RepositorySync:
        """Refresh an installation's details and repository list from GitHub.

        Only the installation named by a caller that is already authorised for it
        is contacted; the repository list comes from GitHub with an installation
        token, never from the caller.
        """
        existing = self._store.get_installation(installation_id)
        if existing is None or existing.state is InstallationState.DELETED:
            raise AuthorizationError("GitHub App installation removed")
        info = self._fetch_installation(installation_id)
        if info.account.id != existing.account_id:
            raise AuthorizationError("installation account mismatch")
        record = self._record_from(info, existing)
        self._store.upsert_installation(record)
        if record.state is InstallationState.SUSPENDED:
            self._tokens.invalidate(installation_id)
            raise AuthorizationError("GitHub App installation suspended")
        before = {r.id: r for r in self._store.list_repositories(installation_id)}
        self._tokens.invalidate(installation_id, None)
        token = self._tokens.token(installation_id, None)
        try:
            listed = self._client.list_installation_repositories(token.token)
        finally:
            self._tokens.invalidate(installation_id, None)
        now = self._now()
        repositories = tuple(info.ref for info in listed)
        self._store.replace_repositories(installation_id, repositories, now)
        for repository_info in listed:
            self._store.set_repository_details(
                installation_id,
                repository_info.id,
                default_branch=repository_info.default_branch,
                private=repository_info.private,
                archived=repository_info.archived,
            )
        after = {r.id: r for r in repositories}
        added = tuple(r for rid, r in sorted(after.items()) if rid not in before)
        removed = tuple(r for rid, r in sorted(before.items()) if rid not in after)
        for repository in removed:
            self._tokens.invalidate(installation_id, repository.id)
            self._mirrors.remove_repository(installation_id, repository.id)
        self._audit.record(
            AuditEventType.REPOSITORIES_SYNCED,
            actor=actor,
            installation_id=installation_id,
            repositories=len(repositories),
            added=len(added),
            removed=len(removed),
        )
        self._discovered(record.account_id, installation_id, repositories)
        return RepositorySync(installation_id, repositories, added, removed, now)

    def authorize(self, installation_id: int, repository: RepositoryRef) -> AuthorizedRepository:
        reason = self.denial_reason(installation_id)
        if reason is not None:
            raise AuthorizationError(f"GitHub App {reason}")
        try:
            self.ensure_installation_record(installation_id)
        except GitHubAPIError as exc:
            log.warning("installation_details_unavailable", category=exc.category.value)
        token = self._tokens.token(installation_id, repository.id)
        try:
            info = self._client.get_repository(token.token, repository.id)
        except GitHubUnauthorizedError:
            self._tokens.invalidate(installation_id, repository.id)
            raise AuthenticationError("GitHub rejected the installation token") from None
        except (GitHubNotFoundError, GitHubForbiddenError):
            self._tokens.invalidate(installation_id, repository.id)
            raise AuthorizationError("the installation cannot access this repository") from None
        if info.id != repository.id:
            raise AuthorizationError("repository identity mismatch")
        canonical = info.ref
        self._store.add_repositories(installation_id, [canonical], self._now())
        self._store.set_repository_details(
            installation_id,
            canonical.id,
            default_branch=info.default_branch,
            private=info.private,
            archived=info.archived,
        )
        return AuthorizedRepository(
            installation_id=installation_id,
            repository=canonical,
            token=token,
            default_branch=info.default_branch,
        )
