"""The ``/api/v1`` routes of organization governance.

Every route is authenticated and CSRF-checked by :class:`~commitguard.api.app.DashboardApi`.
Authorization happens in the governance services, per resource: the caller must
be a member of the resource's organization (otherwise the answer is 404, exactly
like a missing resource) and hold the permission the operation needs
(otherwise 403). Nested resources are resolved from the stored row - a group's,
draft's or exception's organization is never taken from the URL or the body.

======================================================  ==================================
Route                                                   Permission (checked in services)
======================================================  ==================================
GET  /organizations/{id}                                organization:read / security:read
GET  PUT /organizations/{id}/settings                   organization:read / :manage
GET  /organizations/{id}/security/overview|repositories  security:read
GET  /organizations/{id}/security/policies|exceptions   policies:read / exceptions:read
GET  /organizations/{id}/security/trends|events         security:read
POST /organizations/{id}/security/events/{e}/acknowledge  violations:manage
GET  /organizations/{id}/reports/{kind}                 security:read (policy_changes: audit)
GET  /organizations/{id}/search                         organization:read (+ per kind)
POST /organizations/{id}/repositories/onboard|mode      repositories:manage
GET  /repositories/{r}/effective-policy                 policies:read
GET  POST /organizations/{id}/repository-groups         repositories:read / :manage
GET  PATCH DELETE /repository-groups/{g}                repositories:read / :manage
POST /repository-groups/{g}/repositories[/remove]       repositories:manage
GET  /organizations/{id}/policies                       policies:read
GET  /policy-targets/{type}/{id}/versions               policies:read
POST /policy-targets/{type}/{id}/rollback               policies:rollback
GET  POST /organizations/{id}/policy-drafts             policies:read / :write
GET  PATCH /policy-drafts/{d}                           policies:read / :write
POST /policy-drafts/{d}/submit|cancel                   policies:write
POST /policy-drafts/{d}/approve|reject                  policies:approve (not the author)
POST /policy-drafts/{d}/publish                         policies:publish
POST /policy-drafts/{d}/emergency-publish               policies:emergency
POST /policy-drafts/{d}/simulations                     policies:write
GET  /organizations/{id}/simulations, /simulations/{s}  policies:read
GET  /organizations/{id}/rollouts, /rollouts/{r}        policies:read
POST /rollouts/{r}/advance|pause|resume                 policies:publish
POST /rollouts/{r}/rollback                             policies:rollback
GET  /organizations/{id}/policy-propagation             policies:read
GET  POST /organizations/{id}/exceptions                exceptions:read / :create
GET  /exceptions/{x}                                    exceptions:read
POST /exceptions/{x}/approve|reject                     exceptions:approve (not the requester)
POST /exceptions/{x}/revoke                             exceptions:revoke
POST /exceptions/{x}/cancel                             requester or exceptions:revoke
GET  PUT /organizations/{id}/rules, .../rules/history   rules:read / rules:manage
GET  POST /organizations/{id}/bulk-operations           repositories:read / :manage
GET  /bulk-operations/{b}; POST .../cancel|retry        repositories:read / :manage
GET  POST /organizations/{id}/scan-schedules            security:read / :manage
GET  PATCH /scan-schedules/{s}; POST .../disable        security:read / :manage
======================================================  ==================================

Security exceptions are never deleted through the API: they are revoked,
rejected, cancelled or expire, and the history is kept. Groups are archived.
"""

from collections.abc import Callable
from typing import Any

from commitguard.api.http import ApiError, Request, Response, bad_request, ok
from commitguard.audit.models import Actor
from commitguard.controlplane.access import Permission, Principal
from commitguard.controlplane.errors import NotFoundError
from commitguard.controlplane.pagination import encode_cursor, offset_cursor, parse_limit
from commitguard.controlplane.policies import (
    ORGANIZATION_TARGET,
    PolicyTarget,
    PolicyTargetType,
    canonical_document,
)
from commitguard.controlplane.views import OrganizationRef
from commitguard.governance.common import (
    account_repository,
    repository_ids,
    require,
    visible_repository_ids,
)
from commitguard.governance.rollouts import parse_stages
from commitguard.governance.service import GovernanceServices
from commitguard.governance.workflow import parse_rules, parse_target

type RouteRow = tuple[str, str, Callable[[Request], Response], Permission | None, bool, str]


def _principal(request: Request) -> Principal:
    if request.principal is None:  # pragma: no cover - guaranteed by the dispatcher
        raise ApiError(401, "UNAUTHENTICATED", "Sign in to continue.")
    return request.principal


def _flag(value: object, field: str, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise bad_request(f"{field} must be true or false", field)
    return value


def _optional_ids(value: object, field: str) -> list[int] | None:
    return None if value is None else repository_ids(value, field)


class GovernanceRoutes:
    def __init__(self, governance: GovernanceServices) -> None:
        self.g = governance

    # -- organization ----------------------------------------------------- #
    def organization(self, request: Request) -> Response:
        principal = _principal(request)
        account_id = request.params["organization_id"]
        require(principal, Permission.ORGANIZATION_READ, account_id)
        overview = self.g.posture.overview(principal, account_id)
        settings = self.g.settings.view(principal, account_id)
        return ok({"organization": overview, "settings": settings})

    def get_settings(self, request: Request) -> Response:
        principal = _principal(request)
        return ok(self.g.settings.view(principal, request.params["organization_id"]))

    def put_settings(self, request: Request) -> Response:
        principal = _principal(request)
        body = request.json()
        view = self.g.settings.update(
            principal,
            request.params["organization_id"],
            expected_version=body.get("expected_version"),
            changes=body.get("settings"),
            reason=body.get("reason"),
            confirm=body.get("confirm", False),
        )
        return ok(view)

    def security_overview(self, request: Request) -> Response:
        return ok(self.g.posture.overview(_principal(request), request.params["organization_id"]))

    def security_repositories(self, request: Request) -> Response:
        principal = _principal(request)
        offset = offset_cursor(request.arg("cursor"))
        limit = parse_limit(request.arg("limit"))
        names = (
            "q",
            "group",
            "posture",
            "protection",
            "mode",
            "onboarding",
            "policy_state",
            "drift",
            "exceptions",
            "severity",
            "last_scan",
            "sort",
        )
        page = self.g.posture.matrix(
            principal,
            request.params["organization_id"],
            filters={name: request.arg(name) for name in names},
            offset=offset,
            limit=limit,
        )
        return ok(
            list(page.items),
            {
                "next_cursor": page.next_cursor,
                "limit": limit,
                "total": page.total,
                "computed_at": page.computed_at.isoformat(),
            },
        )

    def security_policies(self, request: Request) -> Response:
        principal = _principal(request)
        account_id = request.params["organization_id"]
        require(principal, Permission.POLICIES_READ, account_id)
        return ok(
            {
                "targets": self._policy_targets(principal, account_id),
                "drafts": self.g.workflow.list_drafts(principal, account_id),
                "rollouts": self.g.rollouts.list_rollouts(principal, account_id),
                "propagation": self.g.resolver.propagation_status(principal, account_id),
            }
        )

    def security_exceptions(self, request: Request) -> Response:
        principal = _principal(request)
        account_id = request.params["organization_id"]
        items, _ = self.g.exceptions.list(principal, account_id, limit=500)
        counts: dict[str, int] = {}
        for item in items:
            counts[item.status] = counts.get(item.status, 0) + 1
        counts["expiring_soon"] = sum(1 for item in items if item.expiring_soon)
        return ok({"counts": counts, "exceptions": items})

    def security_trends(self, request: Request) -> Response:
        raw = request.arg("days") or "30"
        if not raw.isdigit() or len(raw) > 3:
            raise bad_request("days must be a number", "days")
        return ok(
            self.g.posture.trends(
                _principal(request), request.params["organization_id"], days=int(raw)
            )
        )

    def security_events(self, request: Request) -> Response:
        return ok(
            self.g.posture.security_events(_principal(request), request.params["organization_id"])
        )

    def acknowledge_event(self, request: Request) -> Response:
        body = request.json()
        return ok(
            self.g.posture.acknowledge_event(
                _principal(request),
                request.params["organization_id"],
                request.params["event_id"],
                body.get("note"),
            )
        )

    def report(self, request: Request) -> Response:
        principal = _principal(request)
        content_type, body, filename = self.g.posture.report(
            principal,
            request.params["organization_id"],
            kind=request.params["kind"],
            fmt=request.arg("format") or "json",
        )
        return Response(
            200,
            body,
            [
                ("Content-Type", content_type),
                ("Content-Disposition", f'attachment; filename="{filename}"'),
            ],
        )

    def search(self, request: Request) -> Response:
        results = self.g.posture.search(
            _principal(request), request.params["organization_id"], request.arg("q") or ""
        )
        return ok(results)

    # -- repositories ----------------------------------------------------- #
    def onboard(self, request: Request) -> Response:
        require(
            _principal(request), Permission.REPOSITORIES_MANAGE, request.params["organization_id"]
        )
        body = request.json()
        changed = self.g.inventory.onboard(
            _principal(request),
            request.params["organization_id"],
            repository_ids(body.get("repository_ids")),
            mode=body.get("mode"),
            confirm=body.get("confirm", False),
            reason=body.get("reason"),
        )
        return ok({"changed": changed})

    def set_mode(self, request: Request) -> Response:
        require(
            _principal(request), Permission.REPOSITORIES_MANAGE, request.params["organization_id"]
        )
        body = request.json()
        changed = self.g.inventory.set_mode(
            _principal(request),
            request.params["organization_id"],
            repository_ids(body.get("repository_ids")),
            mode=body.get("mode"),
            confirm=body.get("confirm", False),
            reason=body.get("reason"),
        )
        return ok({"changed": changed})

    def _repository_account(self, principal: Principal, repository_id: int) -> int:
        """The organization of a repository the caller can see (else 404)."""
        for account_id in principal.accounts_with(Permission.REPOSITORIES_READ):
            if repository_id in visible_repository_ids(
                self.g.store, principal, account_id
            ) and account_repository(self.g.store, account_id, repository_id):
                return account_id
        raise NotFoundError()

    def effective_policy(self, request: Request) -> Response:
        principal = _principal(request)
        repository_id = request.params["repository_id"]
        account_id = self._repository_account(principal, repository_id)
        view = self.g.resolver.effective_view(principal, account_id, repository_id)
        active, soon = self.g.exceptions.counts(
            account_id, repository_id, [g for g in view.versions.groups]
        )
        return ok(view, {"exceptions": {"active": active, "expiring_soon": soon}})

    # -- groups ----------------------------------------------------------- #
    def list_groups(self, request: Request) -> Response:
        archived = request.arg("archived") == "true"
        return ok(
            self.g.groups.list_groups(
                _principal(request), request.params["organization_id"], include_archived=archived
            )
        )

    def create_group(self, request: Request) -> Response:
        body = request.json()
        view = self.g.groups.create(
            _principal(request),
            request.params["organization_id"],
            name=body.get("name"),
            description=body.get("description"),
        )
        return ok(view, status=201)

    def get_group(self, request: Request) -> Response:
        return ok(self.g.groups.get(_principal(request), request.params["group_id"]))

    def patch_group(self, request: Request) -> Response:
        body = request.json()
        return ok(
            self.g.groups.update(
                _principal(request),
                request.params["group_id"],
                name=body.get("name"),
                description=body.get("description"),
            )
        )

    def archive_group(self, request: Request) -> Response:
        confirm = request.arg("confirm") == "true"
        self.g.groups.archive(_principal(request), request.params["group_id"], confirm=confirm)
        return ok({"archived": True})

    def add_group_members(self, request: Request) -> Response:
        self.g.groups.account_of(
            _principal(request), request.params["group_id"], Permission.REPOSITORIES_MANAGE
        )
        body = request.json()
        return ok(
            self.g.groups.add_members(
                _principal(request),
                request.params["group_id"],
                repository_ids(body.get("repository_ids")),
            )
        )

    def remove_group_members(self, request: Request) -> Response:
        self.g.groups.account_of(
            _principal(request), request.params["group_id"], Permission.REPOSITORIES_MANAGE
        )
        body = request.json()
        return ok(
            self.g.groups.remove_members(
                _principal(request),
                request.params["group_id"],
                repository_ids(body.get("repository_ids")),
            )
        )

    # -- policies --------------------------------------------------------- #
    def _organization_ref(self, principal: Principal, account_id: int) -> OrganizationRef:
        membership = principal.memberships.get(account_id)
        if membership is None:
            raise NotFoundError()
        return OrganizationRef(
            id=account_id, login=membership.account_login, type=membership.account_type
        )

    def _policy_targets(self, principal: Principal, account_id: int) -> dict[str, Any]:
        organization = self._organization_ref(principal, account_id)
        can_write = principal.can(Permission.POLICIES_WRITE, account_id)
        groups = self.g.groups.list_groups(principal, account_id)
        visible = visible_repository_ids(self.g.store, principal, account_id)
        repository_targets = [
            repository_id
            for repository_id in self.g.policies.repository_targets(account_id)
            if repository_id in visible
        ]
        return {
            "organization": self.g.policies.view(organization, can_write=can_write),
            "groups": [
                self.g.policies.scoped_view(
                    organization, PolicyTarget.group(group.id), can_write=can_write
                )
                for group in groups
            ],
            "repositories": [
                self.g.policies.scoped_view(
                    organization, PolicyTarget.repository(repository_id), can_write=can_write
                )
                for repository_id in repository_targets
            ],
        }

    def list_policy_targets(self, request: Request) -> Response:
        principal = _principal(request)
        account_id = request.params["organization_id"]
        require(principal, Permission.POLICIES_READ, account_id)
        return ok(self._policy_targets(principal, account_id))

    def _target(
        self,
        request: Request,
        principal: Principal,
        permission: Permission = Permission.POLICIES_READ,
    ) -> tuple[int, PolicyTarget]:
        account_id = request.params["organization_id"]
        require(principal, permission, account_id)  # before parsing: no validation oracle
        target = parse_target(request.params["target_type"], request.arg("target_id") or "")
        if target.type is PolicyTargetType.REPOSITORY and int(target.id) not in (
            visible_repository_ids(self.g.store, principal, account_id)
        ):
            raise NotFoundError()
        if self.g.policies.target_label(account_id, target) is None:
            raise NotFoundError()
        return account_id, target

    def policy_target_versions(self, request: Request) -> Response:
        principal = _principal(request)
        account_id, target = self._target(request, principal)
        offset = offset_cursor(request.arg("cursor"))
        limit = parse_limit(request.arg("limit"))
        versions = self.g.policies.versions(
            account_id, offset=offset, limit=limit + 1, target=target
        )
        next_cursor = encode_cursor([offset + limit]) if len(versions) > limit else None
        organization = self._organization_ref(principal, account_id)
        view = (
            self.g.policies.scoped_view(
                organization, target, can_write=principal.can(Permission.POLICIES_WRITE, account_id)
            )
            if target.scoped
            else self.g.policies.view(
                organization, can_write=principal.can(Permission.POLICIES_WRITE, account_id)
            )
        )
        return ok(
            {"policy": view, "versions": versions[:limit]},
            {"next_cursor": next_cursor, "limit": limit},
        )

    def policy_target_rollback(self, request: Request) -> Response:
        principal = _principal(request)
        account_id, target = self._target(request, principal, Permission.POLICIES_ROLLBACK)
        body = request.json()
        target_version = body.get("target_version")
        expected = body.get("expected_current_version")
        checks = (("target_version", target_version), ("expected_current_version", expected))
        for name, value in checks:
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise bad_request(f"{name} must be a non-negative integer", name)
        created, diff = self.g.policies.rollback(
            account_id=account_id,
            actor=Actor.user(principal.user_id, principal.login),
            authenticated_at=principal.authenticated_at,
            target_version=int(target_version),  # type: ignore[arg-type]
            expected_current_version=int(expected),  # type: ignore[arg-type]
            reason=body.get("reason") if isinstance(body.get("reason"), str) else None,
            confirm=_flag(body.get("confirm"), "confirm"),
            target=target,
        )
        return ok({"version": created.version, "restored_version": target_version, "diff": diff})

    # -- drafts ----------------------------------------------------------- #
    def list_drafts(self, request: Request) -> Response:
        return ok(
            self.g.workflow.list_drafts(
                _principal(request), request.params["organization_id"], state=request.arg("state")
            )
        )

    def create_draft(self, request: Request) -> Response:
        require(_principal(request), Permission.POLICIES_WRITE, request.params["organization_id"])
        body = request.json()
        floors, defaults = parse_rules(body)
        view = self.g.workflow.create(
            _principal(request),
            request.params["organization_id"],
            target_type=body.get("target_type"),
            target_id=body.get("target_id"),
            floors=floors,
            defaults=defaults,
            title=body.get("title"),
            reason=body.get("reason"),
        )
        return ok(view, status=201)

    def get_draft(self, request: Request) -> Response:
        return ok(self.g.workflow.get(_principal(request), request.params["draft_id"]))

    def patch_draft(self, request: Request) -> Response:
        self.g.workflow.authorize(
            _principal(request), request.params["draft_id"], Permission.POLICIES_WRITE
        )
        body = request.json()
        floors = defaults = None
        if "floors" in body or "defaults" in body:
            floors, defaults = parse_rules(body)
        return ok(
            self.g.workflow.update(
                _principal(request),
                request.params["draft_id"],
                expected_revision=body.get("expected_revision"),
                floors=floors,
                defaults=defaults,
                title=body.get("title"),
                reason=body.get("reason"),
            )
        )

    def submit_draft(self, request: Request) -> Response:
        return ok(self.g.workflow.submit(_principal(request), request.params["draft_id"]))

    def approve_draft(self, request: Request) -> Response:
        body = request.json()
        return ok(
            self.g.workflow.decide(
                _principal(request),
                request.params["draft_id"],
                approve=True,
                reason=body.get("reason"),
            )
        )

    def reject_draft(self, request: Request) -> Response:
        body = request.json()
        return ok(
            self.g.workflow.decide(
                _principal(request),
                request.params["draft_id"],
                approve=False,
                reason=body.get("reason"),
            )
        )

    def cancel_draft(self, request: Request) -> Response:
        return ok(self.g.workflow.cancel(_principal(request), request.params["draft_id"]))

    def _rollout_hook(self, principal: Principal, raw: object):  # type: ignore[no-untyped-def]
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise bad_request("rollout must be an object", "rollout")
        thresholds_raw = raw.get("thresholds")
        thresholds: dict[str, float] | None = None
        if thresholds_raw is not None:
            if not isinstance(thresholds_raw, dict):
                raise bad_request("rollout.thresholds must be an object", "rollout.thresholds")
            thresholds = {}
            for key in ("max_error_rate", "max_block_rate", "min_scans"):
                value = thresholds_raw.get(key)
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
                    raise bad_request(f"rollout.thresholds.{key} must be a number", key)
                if key != "min_scans" and value > 1:
                    raise bad_request(f"rollout.thresholds.{key} must be at most 1", key)
                thresholds[key] = float(value)
        return self.g.rollouts.creator(
            principal,
            stages=parse_stages(raw.get("stages")),
            thresholds=thresholds,
            auto_pause=_flag(raw.get("auto_pause"), "rollout.auto_pause", True)
            if raw.get("auto_pause") is not None
            else None,
            auto_rollback=_flag(raw.get("auto_rollback"), "rollout.auto_rollback")
            if raw.get("auto_rollback") is not None
            else None,
        )

    def publish_draft(self, request: Request) -> Response:
        principal = _principal(request)
        body = request.json()
        view = self.g.workflow.publish(
            principal,
            request.params["draft_id"],
            confirm_weakening=_flag(body.get("confirm_weakening"), "confirm_weakening"),
            rollout=self._rollout_hook(principal, body.get("rollout")),
        )
        return ok(view)

    def emergency_publish_draft(self, request: Request) -> Response:
        principal = _principal(request)
        body = request.json()
        view = self.g.workflow.publish(
            principal,
            request.params["draft_id"],
            confirm_weakening=_flag(body.get("confirm_weakening"), "confirm_weakening"),
            emergency=True,
            reason=body.get("reason"),
        )
        return ok(view)

    # -- simulations ------------------------------------------------------ #
    def simulate_draft(self, request: Request) -> Response:
        principal = _principal(request)
        self.g.workflow.authorize(principal, request.params["draft_id"], Permission.POLICIES_WRITE)
        draft = self.g.workflow.get(principal, request.params["draft_id"])
        body = request.json()
        target = (
            ORGANIZATION_TARGET
            if draft.target.type == "organization"
            else PolicyTarget(PolicyTargetType(draft.target.type), draft.target.id)
        )
        view = self.g.simulations.create(
            principal,
            draft.organization_id,
            target=target,
            document=canonical_document(draft.floors, draft.defaults),
            draft_id=draft.id,
            period_days=body.get("period_days"),
            repository_ids=_optional_ids(body.get("repository_ids"), "repository_ids"),
        )
        return ok(view, status=202)

    def list_simulations(self, request: Request) -> Response:
        return ok(
            self.g.simulations.list_simulations(
                _principal(request),
                request.params["organization_id"],
                draft_id=request.arg("draft"),
            )
        )

    def get_simulation(self, request: Request) -> Response:
        return ok(self.g.simulations.get(_principal(request), request.params["simulation_id"]))

    # -- rollouts --------------------------------------------------------- #
    def list_rollouts(self, request: Request) -> Response:
        return ok(
            self.g.rollouts.list_rollouts(
                _principal(request),
                request.params["organization_id"],
                active_only=request.arg("active") == "true",
            )
        )

    def get_rollout(self, request: Request) -> Response:
        return ok(self.g.rollouts.get(_principal(request), request.params["rollout_id"]))

    def advance_rollout(self, request: Request) -> Response:
        return ok(self.g.rollouts.advance(_principal(request), request.params["rollout_id"]))

    def pause_rollout(self, request: Request) -> Response:
        body = request.json()
        return ok(
            self.g.rollouts.pause(
                _principal(request), request.params["rollout_id"], body.get("reason")
            )
        )

    def resume_rollout(self, request: Request) -> Response:
        return ok(self.g.rollouts.resume(_principal(request), request.params["rollout_id"]))

    def rollback_rollout(self, request: Request) -> Response:
        body = request.json()
        return ok(
            self.g.rollouts.rollback(
                _principal(request),
                request.params["rollout_id"],
                reason=body.get("reason"),
                confirm=body.get("confirm"),
            )
        )

    def propagation(self, request: Request) -> Response:
        return ok(
            self.g.resolver.propagation_status(
                _principal(request), request.params["organization_id"]
            )
        )

    # -- exceptions ------------------------------------------------------- #
    def list_exceptions(self, request: Request) -> Response:
        principal = _principal(request)
        repository = request.arg("repository")
        if repository is not None and (not repository.isdigit() or len(repository) > 16):
            raise bad_request("repository must be a repository ID", "repository")
        offset = offset_cursor(request.arg("cursor"))
        limit = parse_limit(request.arg("limit"))
        items, more = self.g.exceptions.list(
            principal,
            request.params["organization_id"],
            status=request.arg("status"),
            rule_id=request.arg("rule"),
            repository_id=int(repository) if repository else None,
            limit=limit,
            offset=offset,
        )
        return ok(
            items,
            {"next_cursor": encode_cursor([offset + limit]) if more else None, "limit": limit},
        )

    def create_exception(self, request: Request) -> Response:
        body = request.json()
        view = self.g.exceptions.request(
            _principal(request),
            request.params["organization_id"],
            rule_id=body.get("rule_id"),
            scope_type=body.get("scope_type"),
            scope_id=body.get("scope_id"),
            action=body.get("action"),
            reason=body.get("reason"),
            expires_at=body.get("expires_at"),
            permanent=body.get("permanent", False),
        )
        return ok(view, status=201)

    def get_exception(self, request: Request) -> Response:
        return ok(self.g.exceptions.get(_principal(request), request.params["exception_id"]))

    def approve_exception(self, request: Request) -> Response:
        body = request.json()
        return ok(
            self.g.exceptions.approve(
                _principal(request), request.params["exception_id"], body.get("note")
            )
        )

    def reject_exception(self, request: Request) -> Response:
        body = request.json()
        return ok(
            self.g.exceptions.reject(
                _principal(request), request.params["exception_id"], body.get("note")
            )
        )

    def revoke_exception(self, request: Request) -> Response:
        body = request.json()
        return ok(
            self.g.exceptions.revoke(
                _principal(request), request.params["exception_id"], body.get("reason")
            )
        )

    def cancel_exception(self, request: Request) -> Response:
        return ok(self.g.exceptions.cancel(_principal(request), request.params["exception_id"]))

    # -- organization rules ----------------------------------------------- #
    def get_rules(self, request: Request) -> Response:
        return ok(self.g.rules.view(_principal(request), request.params["organization_id"]))

    def put_rules(self, request: Request) -> Response:
        body = request.json()
        return ok(
            self.g.rules.update(
                _principal(request),
                request.params["organization_id"],
                expected_version=body.get("expected_version"),
                rules=body.get("rules"),
                reason=body.get("reason"),
            )
        )

    def rules_history(self, request: Request) -> Response:
        return ok(self.g.rules.history(_principal(request), request.params["organization_id"]))

    # -- bulk operations -------------------------------------------------- #
    def list_bulk(self, request: Request) -> Response:
        return ok(
            self.g.bulk.list_operations(_principal(request), request.params["organization_id"])
        )

    def create_bulk(self, request: Request) -> Response:
        body = request.json()
        require(
            _principal(request),
            Permission.SCANS_TRIGGER
            if body.get("type") == "schedule_scan"
            else Permission.REPOSITORIES_MANAGE,
            request.params["organization_id"],
        )
        view = self.g.bulk.create(
            _principal(request),
            request.params["organization_id"],
            operation_type=body.get("type"),
            repository_ids=repository_ids(body.get("repository_ids")),
            parameters=body.get("parameters"),
            idempotency_key=body.get("idempotency_key"),
            confirm=body.get("confirm", False),
        )
        return ok(view, status=202)

    def get_bulk(self, request: Request) -> Response:
        return ok(self.g.bulk.get(_principal(request), request.params["operation_id"]))

    def cancel_bulk(self, request: Request) -> Response:
        return ok(self.g.bulk.cancel(_principal(request), request.params["operation_id"]))

    def retry_bulk(self, request: Request) -> Response:
        return ok(self.g.bulk.retry_failed(_principal(request), request.params["operation_id"]))

    # -- scan schedules --------------------------------------------------- #
    def list_schedules(self, request: Request) -> Response:
        return ok(
            self.g.schedules.list_schedules(_principal(request), request.params["organization_id"])
        )

    def create_schedule(self, request: Request) -> Response:
        view = self.g.schedules.create(
            _principal(request), request.params["organization_id"], request.json()
        )
        return ok(view, status=201)

    def get_schedule(self, request: Request) -> Response:
        view, runs = self.g.schedules.get(_principal(request), request.params["schedule_id"])
        return ok({"schedule": view, "runs": runs})

    def patch_schedule(self, request: Request) -> Response:
        return ok(
            self.g.schedules.update(
                _principal(request), request.params["schedule_id"], request.json()
            )
        )

    def disable_schedule(self, request: Request) -> Response:
        return ok(self.g.schedules.disable(_principal(request), request.params["schedule_id"]))

    # -- table ------------------------------------------------------------ #
    def routes(self) -> list[RouteRow]:
        org = "/organizations/{organization_id:int}"
        return [
            ("GET", org, self.organization, None, False, "read"),
            ("GET", f"{org}/settings", self.get_settings, None, False, "read"),
            ("PUT", f"{org}/settings", self.put_settings, None, False, "sensitive"),
            ("GET", f"{org}/security/overview", self.security_overview, None, False, "read"),
            (
                "GET",
                f"{org}/security/repositories",
                self.security_repositories,
                None,
                False,
                "read",
            ),
            ("GET", f"{org}/security/policies", self.security_policies, None, False, "read"),
            ("GET", f"{org}/security/exceptions", self.security_exceptions, None, False, "read"),
            ("GET", f"{org}/security/trends", self.security_trends, None, False, "read"),
            ("GET", f"{org}/security/events", self.security_events, None, False, "read"),
            (
                "POST",
                f"{org}/security/events/{{event_id:hex}}/acknowledge",
                self.acknowledge_event,
                None,
                False,
                "write",
            ),
            ("GET", f"{org}/reports/{{kind:ident}}", self.report, None, False, "search"),
            ("GET", f"{org}/search", self.search, None, False, "search"),
            ("POST", f"{org}/repositories/onboard", self.onboard, None, False, "write"),
            ("POST", f"{org}/repositories/mode", self.set_mode, None, False, "sensitive"),
            (
                "GET",
                "/repositories/{repository_id:int}/effective-policy",
                self.effective_policy,
                None,
                False,
                "read",
            ),
            ("GET", f"{org}/repository-groups", self.list_groups, None, False, "read"),
            ("POST", f"{org}/repository-groups", self.create_group, None, False, "write"),
            ("GET", "/repository-groups/{group_id:hex}", self.get_group, None, False, "read"),
            ("PATCH", "/repository-groups/{group_id:hex}", self.patch_group, None, False, "write"),
            (
                "DELETE",
                "/repository-groups/{group_id:hex}",
                self.archive_group,
                None,
                False,
                "sensitive",
            ),
            (
                "POST",
                "/repository-groups/{group_id:hex}/repositories",
                self.add_group_members,
                None,
                False,
                "write",
            ),
            (
                "POST",
                "/repository-groups/{group_id:hex}/repositories/remove",
                self.remove_group_members,
                None,
                False,
                "write",
            ),
            ("GET", f"{org}/policies", self.list_policy_targets, None, False, "read"),
            (
                "GET",
                f"{org}/policy-targets/{{target_type:ident}}/versions",
                self.policy_target_versions,
                None,
                False,
                "read",
            ),
            (
                "POST",
                f"{org}/policy-targets/{{target_type:ident}}/rollback",
                self.policy_target_rollback,
                None,
                False,
                "sensitive",
            ),
            ("GET", f"{org}/policy-drafts", self.list_drafts, None, False, "read"),
            ("POST", f"{org}/policy-drafts", self.create_draft, None, False, "write"),
            ("GET", "/policy-drafts/{draft_id:hex}", self.get_draft, None, False, "read"),
            ("PATCH", "/policy-drafts/{draft_id:hex}", self.patch_draft, None, False, "write"),
            (
                "POST",
                "/policy-drafts/{draft_id:hex}/submit",
                self.submit_draft,
                None,
                False,
                "write",
            ),
            (
                "POST",
                "/policy-drafts/{draft_id:hex}/approve",
                self.approve_draft,
                None,
                False,
                "sensitive",
            ),
            (
                "POST",
                "/policy-drafts/{draft_id:hex}/reject",
                self.reject_draft,
                None,
                False,
                "write",
            ),
            (
                "POST",
                "/policy-drafts/{draft_id:hex}/cancel",
                self.cancel_draft,
                None,
                False,
                "write",
            ),
            (
                "POST",
                "/policy-drafts/{draft_id:hex}/publish",
                self.publish_draft,
                None,
                False,
                "sensitive",
            ),
            (
                "POST",
                "/policy-drafts/{draft_id:hex}/emergency-publish",
                self.emergency_publish_draft,
                None,
                False,
                "sensitive",
            ),
            (
                "POST",
                "/policy-drafts/{draft_id:hex}/simulations",
                self.simulate_draft,
                None,
                False,
                "write",
            ),
            ("GET", f"{org}/simulations", self.list_simulations, None, False, "read"),
            (
                "GET",
                "/simulations/{simulation_id:hex}",
                self.get_simulation,
                None,
                False,
                "read",
            ),
            ("GET", f"{org}/rollouts", self.list_rollouts, None, False, "read"),
            ("GET", "/rollouts/{rollout_id:hex}", self.get_rollout, None, False, "read"),
            (
                "POST",
                "/rollouts/{rollout_id:hex}/advance",
                self.advance_rollout,
                None,
                False,
                "sensitive",
            ),
            (
                "POST",
                "/rollouts/{rollout_id:hex}/pause",
                self.pause_rollout,
                None,
                False,
                "write",
            ),
            (
                "POST",
                "/rollouts/{rollout_id:hex}/resume",
                self.resume_rollout,
                None,
                False,
                "sensitive",
            ),
            (
                "POST",
                "/rollouts/{rollout_id:hex}/rollback",
                self.rollback_rollout,
                None,
                False,
                "sensitive",
            ),
            ("GET", f"{org}/policy-propagation", self.propagation, None, False, "read"),
            ("GET", f"{org}/exceptions", self.list_exceptions, None, False, "read"),
            ("POST", f"{org}/exceptions", self.create_exception, None, False, "sensitive"),
            ("GET", "/exceptions/{exception_id:hex}", self.get_exception, None, False, "read"),
            (
                "POST",
                "/exceptions/{exception_id:hex}/approve",
                self.approve_exception,
                None,
                False,
                "sensitive",
            ),
            (
                "POST",
                "/exceptions/{exception_id:hex}/reject",
                self.reject_exception,
                None,
                False,
                "write",
            ),
            (
                "POST",
                "/exceptions/{exception_id:hex}/revoke",
                self.revoke_exception,
                None,
                False,
                "sensitive",
            ),
            (
                "POST",
                "/exceptions/{exception_id:hex}/cancel",
                self.cancel_exception,
                None,
                False,
                "write",
            ),
            ("GET", f"{org}/rules", self.get_rules, None, False, "read"),
            ("PUT", f"{org}/rules", self.put_rules, None, False, "sensitive"),
            ("GET", f"{org}/rules/history", self.rules_history, None, False, "read"),
            ("GET", f"{org}/bulk-operations", self.list_bulk, None, False, "read"),
            ("POST", f"{org}/bulk-operations", self.create_bulk, None, False, "sensitive"),
            ("GET", "/bulk-operations/{operation_id:hex}", self.get_bulk, None, False, "read"),
            (
                "POST",
                "/bulk-operations/{operation_id:hex}/cancel",
                self.cancel_bulk,
                None,
                False,
                "write",
            ),
            (
                "POST",
                "/bulk-operations/{operation_id:hex}/retry",
                self.retry_bulk,
                None,
                False,
                "write",
            ),
            ("GET", f"{org}/scan-schedules", self.list_schedules, None, False, "read"),
            ("POST", f"{org}/scan-schedules", self.create_schedule, None, False, "write"),
            ("GET", "/scan-schedules/{schedule_id:hex}", self.get_schedule, None, False, "read"),
            (
                "PATCH",
                "/scan-schedules/{schedule_id:hex}",
                self.patch_schedule,
                None,
                False,
                "write",
            ),
            (
                "POST",
                "/scan-schedules/{schedule_id:hex}/disable",
                self.disable_schedule,
                None,
                False,
                "write",
            ),
        ]
