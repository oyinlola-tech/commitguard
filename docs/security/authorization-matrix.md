# Authorization matrix

Roles, permissions and the checks that enforce them in the GitHub App
dashboard and JSON API (`/api/v1`). Verified against the code on 2026-09-17.

- Role and permission table: `src/commitguard/controlplane/access.py`
  (`ROLE_PERMISSIONS`). It is the only place that maps roles to permissions.
- Route-level checks: `src/commitguard/api/app.py` (`_build_routes`,
  `_dispatch`, `_organization`).
- Service-level checks: `src/commitguard/controlplane/*.py`,
  `src/commitguard/governance/*.py` (`require()` in
  `src/commitguard/governance/common.py`), `src/commitguard/api/governance.py`.

The local CLI, Git hooks and the GitHub Action have no roles: whoever can run
them or edit the workflow controls them. Their boundaries are described in
[security-boundaries.md](security-boundaries.md).

## Model

- **Tenant (organization):** a GitHub account (organization or user) that
  installed the App, identified by its immutable numeric account ID.
- **Member:** a GitHub user (immutable numeric user ID) with a CommitGuard role
  in that tenant. Roles are granted by an owner in the dashboard or by the
  operator with `commitguard dashboard members grant`; GitHub does not grant
  them. The owner of a personal (user-account) installation is implicitly its
  `owner`.
- **Access requires both** a CommitGuard role **and** GitHub listing the
  installation for the user at sign-in. Repository-level data additionally
  requires that GitHub listed that repository for the user's session.
- **Roles are cumulative:** `viewer` ⊂ `security_manager` ⊂ `admin` ⊂ `owner`
  (tested by `test_roles_have_practical_differences`).
- **Authorization is by permission, never by role name.** Permissions are
  resolved from the database on every request, so role changes apply to the
  next request.

## Roles × permissions

| Permission | Viewer | Security manager | Admin | Owner |
|---|:-:|:-:|:-:|:-:|
| `repositories:read` | ✓ | ✓ | ✓ | ✓ |
| `scans:read` | ✓ | ✓ | ✓ | ✓ |
| `violations:read` | ✓ | ✓ | ✓ | ✓ |
| `policies:read` | ✓ | ✓ | ✓ | ✓ |
| `rules:read` | ✓ | ✓ | ✓ | ✓ |
| `notifications:read` | ✓ | ✓ | ✓ | ✓ |
| `organization:read` | ✓ | ✓ | ✓ | ✓ |
| `exceptions:read` | ✓ | ✓ | ✓ | ✓ |
| `security:read` | ✓ | ✓ | ✓ | ✓ |
| `violations:manage` | | ✓ | ✓ | ✓ |
| `scans:trigger` | | ✓ | ✓ | ✓ |
| `audit:read` | | ✓ | ✓ | ✓ |
| `exceptions:create` | | ✓ | ✓ | ✓ |
| `policies:write` | | | ✓ | ✓ |
| `policies:rollback` | | | ✓ | ✓ |
| `policies:publish` | | | ✓ | ✓ |
| `policies:approve` | | | ✓ | ✓ |
| `notifications:manage` | | | ✓ | ✓ |
| `repositories:manage` | | | ✓ | ✓ |
| `github:manage` | | | ✓ | ✓ |
| `members:read` | | | ✓ | ✓ |
| `organization:manage` | | | ✓ | ✓ |
| `rules:manage` | | | ✓ | ✓ |
| `exceptions:approve` | | | ✓ | ✓ |
| `exceptions:revoke` | | | ✓ | ✓ |
| `security:manage` | | | ✓ | ✓ |
| `members:manage` | | | | ✓ |
| `policies:emergency` | | | | ✓ |

## Roles × actions

Where a route declares a broad read permission and the service checks a
narrower one, both are listed ("route / service").

### Dashboard, repositories, scans and violations

| Action | Permission checked | Viewer | Sec. mgr | Admin | Owner | Extra conditions |
|---|---|:-:|:-:|:-:|:-:|---|
| Overview, list and view repositories, installations | `repositories:read` | ✓ | ✓ | ✓ | ✓ | only installations and repositories GitHub listed for the session |
| List and view scans, comparisons, executions | `scans:read` | ✓ | ✓ | ✓ | ✓ | same |
| List and view violations | `violations:read` | ✓ | ✓ | ✓ | ✓ | same |
| Request a re-scan | `scans:read` / `scans:trigger` | | ✓ | ✓ | ✓ | stale re-runs are refused and audited |
| Acknowledge a violation or remove acknowledgement | `violations:read` / `violations:manage` | | ✓ | ✓ | ✓ | does not resolve or change enforcement; audited |
| Pause or resume monitoring a repository | `repositories:read` / `repositories:manage` | | | ✓ | ✓ | audited |
| Refresh enforcement status | `repositories:read` / `repositories:manage` | | | ✓ | ✓ | uses a down-scoped installation token |
| Sync installation repositories | `repositories:read` / `github:manage` | | | ✓ | ✓ | |
| Read the audit log | `audit:read` | | ✓ | ✓ | ✓ | |
| List and view rules | `rules:read` | ✓ | ✓ | ✓ | ✓ | |

### Members and sessions

| Action | Permission checked | Viewer | Sec. mgr | Admin | Owner | Extra conditions |
|---|---|:-:|:-:|:-:|:-:|---|
| List own organizations and sessions | signed in | ✓ | ✓ | ✓ | ✓ | |
| Revoke one of one's own sessions, sign out | signed in | ✓ | ✓ | ✓ | ✓ | only the caller's sessions |
| List members | `members:read` | | | ✓ | ✓ | |
| Grant or change a role | `members:manage` | | | | ✓ | not one's own role; implicit owner of a personal account cannot be edited; grants by numeric user ID; audited |
| Remove a member | `members:manage` | | | | ✓ | not oneself; the last explicit owner cannot be removed or demoted; removing the last membership deletes the user's sessions; audited |

### Organization policy (Phase 6/7 endpoints)

| Action | Permission checked | Viewer | Sec. mgr | Admin | Owner | Extra conditions |
|---|---|:-:|:-:|:-:|:-:|---|
| View policy, versions, diff, preview | `policies:read` | ✓ | ✓ | ✓ | ✓ | |
| Save organization policy directly | `policies:write` | | | ✓ | ✓ | refused with `APPROVAL_REQUIRED` when `require_policy_approval` is on; weakening needs confirmation, a reason and a sign-in within 15 minutes; optimistic concurrency |
| Roll back organization policy | `policies:rollback` | | | ✓ | ✓ | reason; confirmation; recent sign-in when it weakens a floor; creates a new immutable version |

### Organization governance (Phase 8 endpoints)

| Action | Permission checked | Viewer | Sec. mgr | Admin | Owner | Extra conditions |
|---|---|:-:|:-:|:-:|:-:|---|
| View organization, security settings | `organization:read` | ✓ | ✓ | ✓ | ✓ | |
| Change security settings | `organization:manage` | | | ✓ | ✓ | relaxing a control needs confirmation, a reason and a recent sign-in; audited and notified |
| Security posture, matrix, trends, events, reports | `security:read` | ✓ | ✓ | ✓ | ✓ | `policy_changes` report also needs `audit:read`; exports audited |
| Acknowledge a security event | `violations:manage` | | ✓ | ✓ | ✓ | resolves nothing |
| Organization search | `organization:read` | ✓ | ✓ | ✓ | ✓ | each result type filtered by its own permission and repository visibility |
| List and view repository groups | `repositories:read` | ✓ | ✓ | ✓ | ✓ | |
| Create, edit, archive groups; change membership | `repositories:manage` | | | ✓ | ✓ | archiving a group with a policy or exceptions needs confirmation |
| Onboard repositories, change mode (`enforce` / `monitor`) | `repositories:manage` | | | ✓ | ✓ | monitor needs confirmation and a reason |
| Bulk operation: scheduled scans | `scans:trigger` | | ✓ | ✓ | ✓ | idempotency key; bounded |
| Bulk operation: other types | `repositories:manage` | | | ✓ | ✓ | same |
| View policy targets, versions, drafts, simulations, rollouts | `policies:read` | ✓ | ✓ | ✓ | ✓ | repository targets must be visible to the caller |
| Create, edit, submit, cancel a policy draft | `policies:write` | | | ✓ | ✓ | |
| Run a policy simulation | `policies:write` | | | ✓ | ✓ | read-only; at most 3 open per organization |
| Approve or reject a policy draft | `policies:approve` | | | ✓ | ✓ | with `require_separate_approver` (default on) not the draft's creator or submitter; approval bound to the document fingerprint |
| Publish a draft | `policies:publish` | | | ✓ | ✓ | must be approved when `require_policy_approval` is on |
| Emergency publication | `policies:emergency` | | | | ✓ | reason required; critical audit event and mandatory notification |
| Advance, pause, resume a rollout | `policies:publish` | | | ✓ | ✓ | |
| Roll back a policy target or rollout | `policies:rollback` | | | ✓ | ✓ | |
| View exceptions | `exceptions:read` | ✓ | ✓ | ✓ | ✓ | repository exceptions only for visible repositories |
| Request an exception | `exceptions:create` | | ✓ | ✓ | ✓ | permanent requests need `exceptions:approve` and `allow_permanent_exceptions` |
| Approve or reject an exception | `exceptions:approve` | | | ✓ | ✓ | never the requester |
| Cancel an exception request | requester, or `exceptions:revoke` | | own | ✓ | ✓ | viewers cannot request exceptions |
| Revoke an active exception | `exceptions:revoke` | | | ✓ | ✓ | reason required |
| View organization rules | `rules:read` | ✓ | ✓ | ✓ | ✓ | |
| Change organization rules | `rules:manage` | | | ✓ | ✓ | identity data only; versioned |
| View scan schedules | `security:read` | ✓ | ✓ | ✓ | ✓ | |
| Create or change scan schedules | `security:manage` | | | ✓ | ✓ | at most 100 per organization |

### Notifications

| Action | Permission checked | Viewer | Sec. mgr | Admin | Owner | Extra conditions |
|---|---|:-:|:-:|:-:|:-:|---|
| Read, mark, archive own notifications; own preferences | signed in; row belongs to the caller; the member's current role must still permit the notification type | ✓ | ✓ | ✓ | ✓ | mandatory types cannot be muted |
| View organization notification settings | `notifications:read` | ✓ | ✓ | ✓ | ✓ | |
| Change organization notification settings, e-mail recipients | `notifications:manage` | | | ✓ | ✓ | turning a delivery off needs confirmation; audited |
| Add or remove a webhook endpoint | `notifications:manage` | | | ✓ | ✓ | confirmation and a sign-in within 15 minutes; HTTPS; at most 10 endpoints |
| View delivery log | `notifications:manage` | | | ✓ | ✓ | |

### Outside the dashboard

| Actor | Can do | Control |
|---|---|---|
| Operator with shell access to the service host | grant or revoke any role (`commitguard dashboard members grant|revoke|list`), read and modify the database, change credentials and the mandatory policy | host access; CLI grants are audited with a system actor. The operator is trusted (see [threat model](threat-model.md#trust-assumptions)). |
| Unauthenticated client | `GET /api/v1/auth/login`, `GET /api/v1/auth/callback`, `POST /webhooks/github` (signature required), `/health`, `/ready`, static dashboard files | rate limits; everything else answers `401` |

## Tenant isolation

- **Scope construction.** For each request the API builds a `Principal`
  (memberships from the database; installations and repositories that GitHub
  listed at sign-in) and, for routes with a permission, an `AccessScope`:
  installations whose account grants that permission **and** that GitHub
  reported for the session.
- **Every read query filters by scope.** `src/commitguard/controlplane/queries.py`
  filters on `installation_id IN (SELECT value FROM json_each(?))` and on
  `session_repositories` for repository-level rows.
- **Governance services** check membership first: another organization's
  resource answers `404` (never `403`), before request bodies are parsed, so
  validation errors cannot reveal existence.
- **Writes resolve the target inside the scope** before checking the
  permission, so guessing an ID in another tenant gives `404`.
- **Organization query parameter.** If `?organization=` names an account where
  the caller lacks the route's permission, the API answers `404`.

## Repository visibility

A role never reveals a repository the user could not open on GitHub:

- the list of repositories GitHub returns for each installation at sign-in is
  stored with the session (`session_repositories`);
- repository-level rows (scans, violations, findings, repository exceptions,
  repository policy targets, report rows, search results, notifications about
  a repository) are returned only for repositories in that list;
- repository IDs in request bodies must belong to the organization **and** be
  visible to the caller (`require_visible_repositories`), otherwise `404`;
- changes to GitHub access take effect at the next sign-in; sessions last at
  most 8 hours (2 hours idle).

## Separation of duties

| Control | Enforced where | Default | Notes |
|---|---|---|---|
| Policy changes require approval before publication | `src/commitguard/governance/workflow.py` (`publish`) | **off** (`require_policy_approval = False`) | while off, an admin can publish a draft or save policy directly, alone |
| Author of a draft cannot approve it | `workflow.py` (`decide`) | **on** (`require_separate_approver = True`) | applies to the draft's creator and submitter; only meaningful when approval is used |
| Approval is bound to a document | fingerprint check in `decide` and state reset on edit | always | editing an approved draft returns it to `draft` |
| Exception requester cannot approve it | `src/commitguard/governance/exceptions.py` (`approve`) | always | not configurable |
| Permanent exceptions | `exceptions.py` and a database check constraint | off (`allow_permanent_exceptions = False`) | needs the setting, a requester with `exceptions:approve`, and approval by someone else |
| Relaxing a control | `src/commitguard/governance/settings.py` (`weakening_changes`) | always | confirmation, reason, sign-in within 15 minutes; audited and notified, but one admin can do it |
| Emergency publication | `workflow.py` (`publish`, `policies:emergency`) | always available to owners | bypasses approval by design; reason, critical audit event and mandatory notification |
| Member management | `src/commitguard/controlplane/members.py` | always | nobody changes their own role or removes themselves; last owner protected |

**What separation of duties does not cover.** An organization with a single
admin cannot satisfy `require_separate_approver` except through emergency
publication. A single admin can turn approval off (audited and notified). An
owner can publish alone in an emergency. The operator can grant roles from the
command line. Separation of duties therefore depends on the organization
enabling approval and having at least two people with `policies:approve`.

## Tests

| Property | Tests |
|---|---|
| Role table is strictly cumulative; every permission is checked somewhere in `api`, `controlplane` or `governance` | `tests/integration/github/app/dashboard/test_dashboard_authorization.py::test_roles_have_practical_differences`, `::test_every_permission_is_checked_somewhere` |
| Unauthenticated access, sessions, sign-in binding, redirects, token handling | `test_dashboard_authorization.py::test_unauthenticated_requests_are_rejected`, `::test_session_lifecycle_expiry_logout_and_revocation`, `::test_absolute_session_lifetime`, `::test_sign_in_flow_is_bound_to_the_browser`, `::test_sign_in_never_redirects_off_site`, `::test_github_user_token_is_never_stored_or_returned` |
| Role behaviour | `test_dashboard_authorization.py::test_viewer_reads_but_cannot_change_anything`, `::test_admin_changes_policy_and_it_is_audited`, `::test_security_manager_triage_but_not_policy`, `::test_role_changes_apply_to_the_next_request`, `::test_owner_rules_for_members` |
| Tenant isolation and repository visibility | `test_dashboard_authorization.py::test_tenants_cannot_read_each_other`, `::test_idor_on_every_write_is_rejected`, `::test_repositories_hidden_when_github_denies_the_user`, `::test_membership_requires_github_installation_access` |
| Governance isolation sweep (every governance route as another tenant, as a viewer, unauthenticated) | `tests/integration/github/app/dashboard/test_governance_isolation.py::test_other_tenants_get_not_found_for_every_governance_route`, `::test_viewers_cannot_change_anything`, `::test_security_manager_can_request_but_not_approve_or_publish` |
| Separation of duties, emergency publication, exception lifecycle | `tests/integration/github/app/dashboard/test_governance_workflow.py::test_approval_workflow_enforces_separation_of_duties`, `::test_emergency_publication_is_owner_only_reasoned_and_loud`, `::test_exception_lifecycle_request_approve_activate_expire` |
| Weakening needs confirmation and recent sign-in | `tests/integration/github/app/dashboard/test_dashboard_policies.py::test_weakening_needs_confirmation_reason_and_recent_sign_in`, `tests/unit/controlplane/test_governance_units.py::test_settings_weakening_is_detected_per_control` |
| Notification access | `tests/integration/github/app/dashboard/test_dashboard_notifications.py::test_notification_idor_and_state_changes`, `::test_organization_settings_require_manage_permission_and_confirmation`, `::test_webhook_endpoints_are_signed_confirmed_and_tenant_scoped` |
| Group membership and search scoping | `tests/integration/github/app/dashboard/test_governance_policies.py::test_group_membership_is_tenant_scoped`, `tests/integration/github/app/dashboard/test_governance_operations.py::test_search_is_tenant_scoped` |

`test_dashboard_authorization.py` and `test_governance_isolation.py` are part
of the security marker set (`pytest -m security`). `test_governance_workflow.py`,
`test_dashboard_policies.py`, `test_dashboard_notifications.py`,
`test_governance_policies.py` and `test_governance_operations.py` are not; add
them to `SECURITY_TEST_PATHS` in `tests/conftest.py` if they should run with
the security suite.

## Notes for reviewers

- Several write routes declare a read permission at the route level
  (`repositories:read`, `scans:read`, `violations:read`) and check the write
  permission in the service. Governance routes declare no route permission and
  rely entirely on service checks. A new route that forgets the service check
  would be reachable by any member with the read permission; review new routes
  for a matching `require()` or `principal.can()` call.
- `test_every_permission_is_checked_somewhere` proves each permission name
  appears in the source, not that each route checks the right one.
