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

from commitguard.audit.models import AuditEventType
from commitguard.github.auth import InstallationToken, InstallationTokenProvider
from commitguard.github.client import GitHubClient
from commitguard.github.errors import (
    AuthenticationError,
    AuthorizationError,
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
from commitguard.github.identifiers import RepositoryRef
from commitguard.github.repositories import MirrorManager
from commitguard.github.storage import InstallationRecord, InstallationState, SqliteStateStore
from commitguard.observability.logging import get_logger
from commitguard.services.audit import AuditService

log = get_logger(__name__)


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
            self._audit.record(AuditEventType.INSTALLATION_REMOVED, installation_id=installation_id)
        elif event.action is InstallationAction.SUSPEND:
            self._store.upsert_installation(
                record.model_copy(update={"state": InstallationState.SUSPENDED})
            )
            self._tokens.invalidate(event.installation_id)
            self._audit.record(
                AuditEventType.INSTALLATION_SUSPENDED, installation_id=installation_id
            )
        elif event.action is InstallationAction.UNSUSPEND:
            self._store.upsert_installation(record)
            self._audit.record(
                AuditEventType.INSTALLATION_UNSUSPENDED, installation_id=installation_id
            )
        else:  # new_permissions_accepted
            state = existing.state if existing else InstallationState.ACTIVE
            self._store.upsert_installation(record.model_copy(update={"state": state}))
            self._tokens.invalidate(event.installation_id)
            self._audit.record(
                AuditEventType.INSTALLATION_PERMISSIONS_UPDATED, installation_id=installation_id
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
                installation_id=event.installation_id,
                repositories=len(event.added),
            )
        else:
            removed = [r.id for r in event.removed]
            self._store.remove_repositories(event.installation_id, removed)
            for repository_id in removed:
                self._tokens.invalidate(event.installation_id, repository_id)
                self._mirrors.remove_repository(event.installation_id, repository_id)
            self._audit.record(
                AuditEventType.REPOSITORIES_REMOVED,
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

    def authorize(self, installation_id: int, repository: RepositoryRef) -> AuthorizedRepository:
        reason = self.denial_reason(installation_id)
        if reason is not None:
            raise AuthorizationError(f"GitHub App {reason}")
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
        return AuthorizedRepository(
            installation_id=installation_id,
            repository=canonical,
            token=token,
            default_branch=info.default_branch,
        )
