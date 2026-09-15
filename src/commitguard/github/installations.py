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
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from commitguard.audit.models import GITHUB_ACTOR, Actor, AuditEventType
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
        if event.action is InstallationAction.CREATED:
            self._store.upsert_installation(record)
            self._store.replace_repositories(event.installation_id, event.repositories, now)
            self._audit.record(
                AuditEventType.INSTALLATION_CREATED,
                actor=GITHUB_ACTOR,
                installation_id=installation_id,
                account_id=event.account.id,
                account_type=event.account.type.value,
                repository_selection=event.repository_selection,
                repositories=len(event.repositories),
            )
        elif event.action is InstallationAction.DELETED:
            self._store.upsert_installation(
                record.model_copy(update={"state": InstallationState.DELETED})
            )
            self._store.set_installation_state(
                event.installation_id, InstallationState.DELETED, now
            )
            self._tokens.invalidate(event.installation_id)
            self._mirrors.remove_installation(event.installation_id)
            self._audit.record(
                AuditEventType.INSTALLATION_REMOVED,
                actor=GITHUB_ACTOR,
                installation_id=installation_id,
            )
        elif event.action is InstallationAction.SUSPEND:
            self._store.upsert_installation(
                record.model_copy(update={"state": InstallationState.SUSPENDED})
            )
            self._tokens.invalidate(event.installation_id)
            self._audit.record(
                AuditEventType.INSTALLATION_SUSPENDED,
                actor=GITHUB_ACTOR,
                installation_id=installation_id,
            )
        elif event.action is InstallationAction.UNSUSPEND:
            self._store.upsert_installation(record)
            self._audit.record(
                AuditEventType.INSTALLATION_UNSUSPENDED,
                actor=GITHUB_ACTOR,
                installation_id=installation_id,
            )
        else:  # new_permissions_accepted
            state = existing.state if existing else InstallationState.ACTIVE
            self._store.upsert_installation(record.model_copy(update={"state": state}))
            self._tokens.invalidate(event.installation_id)
            self._audit.record(
                AuditEventType.INSTALLATION_PERMISSIONS_UPDATED,
                actor=GITHUB_ACTOR,
                installation_id=installation_id,
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
            self._store.set_default_branch(
                installation_id, repository_info.id, repository_info.default_branch
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
        self._store.set_default_branch(installation_id, canonical.id, info.default_branch)
        return AuthorizedRepository(
            installation_id=installation_id,
            repository=canonical,
            token=token,
            default_branch=info.default_branch,
        )
