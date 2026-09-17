# Authorization

Once a request is authenticated (see [authentication.md](authentication.md)),
CommitGuard decides what it may see and do from three inputs:

- the caller's **role** in each organization;
- the **installations and repositories GitHub listed** for the caller at sign-in;
- the **permission** the operation requires.

Authorization is always checked on the server, by permission and never by role
name.

Source: `src/commitguard/controlplane/access.py` (roles, permissions, access
scope); `DashboardApi._dispatch` and `DashboardApi._organization` in
`src/commitguard/api/app.py`; `require` and `visible_repository_ids` in
`src/commitguard/governance/common.py`.

## Tenants

- An **organization** (tenant) is a GitHub account, organization or user,
  identified by its numeric account ID. Its GitHub App installation owns its
  repositories, scans, violations, policies and audit events.
- A **member** is a GitHub user with a CommitGuard role in that organization.
  Owners grant roles through
  [`PUT /api/v1/organizations/{organization_id}/members/{user_id}`](organizations.md#put-apiv1organizationsorganization_idmembersuser_id),
  and operators through the `commitguard dashboard members grant` command.
  GitHub organization roles do not grant CommitGuard roles.
- The owner of a **personal** (user-account) installation is always its `owner`
  (`implicit_role: true`).

A role counts only if GitHub listed that organization's installation for the
caller at sign-in. A user with a role but no GitHub access to the installation
does not see the organization. Routes that require a permission answer
`403 FORBIDDEN` for such a user.

## Roles and permissions

Each role includes every permission of the roles above it.

| Permission | `viewer` | `security_manager` | `admin` | `owner` |
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

The roles are fixed. There are no custom roles. `GET /api/v1/auth/session` and
`GET /api/v1/organizations` return each organization's role and permission list.
Clients may use it to hide controls, but the server decides.

## Separation of duties and other rules

These rules apply on top of permissions and answer `403 FORBIDDEN` or
`409 CONFLICT`:

- **Policy drafts.** When the organization setting `require_separate_approver`
  is on (the default), the author or submitter of a draft cannot approve or
  reject it. See [policy-drafts.md](policy-drafts.md).
- **Exceptions.** The requester of an exception cannot approve it. A request can
  be cancelled by its requester, or by a holder of `exceptions:revoke`. Permanent
  exceptions can only be requested by holders of `exceptions:approve`, and only
  when the organization allows them. See [exceptions.md](exceptions.md).
- **Members.** Nobody changes or removes their own role. The last owner cannot be
  demoted or removed. The implicit owner of a personal installation cannot be
  edited.
- **Recent sign-in.** Some weakening changes also require a sign-in within the
  last 15 minutes; see
  [authentication.md](authentication.md#re-authentication-for-sensitive-changes).

## Where checks happen

**Route-level permission.** Some routes declare a permission in the route table.
For them, the dispatcher checks the following:

1. If the caller holds the permission in no organization: `403 FORBIDDEN`.
2. If an `organization` query parameter names an organization where the caller
   lacks it: `404 NOT_FOUND`.
3. Otherwise, the handler receives an **access scope** for the permission. The
   scope holds the organizations that grant it, the installations of those
   organizations that GitHub listed for the session, and the session's
   repository list.

**Service-level permission.** All other routes check the permission in the
service, against the resource's own organization:

1. Load the resource, taking its organization from the stored row, never from
   the URL or body.
2. If the caller has no role in that organization, or cannot see the resource:
   `404 NOT_FOUND`.
3. If the caller's role lacks the permission: `403 FORBIDDEN`.

Some routes do both. For example, `PUT /api/v1/repositories/{repository_id}/monitoring`
finds the repository with `repositories:read`, then requires
`repositories:manage`.

## Repository visibility

Repository-level data needs more than a role. **GitHub must have listed the
repository for the caller at sign-in.** This applies to repositories, scans,
findings, violations, repository-scoped exceptions, notifications and audit
events that concern a repository. An admin without GitHub access to a private
repository does not see its scans or violations.

A resource outside the caller's access is `404 NOT_FOUND`, exactly like one that
does not exist. The API never tells a caller that a resource exists in another
tenant.

Some aggregates count all repositories of the organization, not only those listed
for the session. The endpoint pages note them:

- `repository_count` of a group
- `repositories_covered` of a scan schedule
- the exception counts in the organization posture
- security trends
- the `repositories` total in policy propagation

These aggregates return counts, not repository names or data.

## Permissions by endpoint group

| Group | Read | Change |
|---|---|---|
| [Sessions](authentication.md) | session only | session only (own sessions) |
| [Organizations](organizations.md) | session (list); `organization:read` and `security:read` (detail); `organization:read` (settings) | `organization:manage` (settings) |
| [Members](organizations.md#get-apiv1organizationsorganization_idmembers) | `members:read` | `members:manage` |
| [Overview and posture](security-posture.md) | `repositories:read` (dashboard overview); `security:read`; `policies:read` and `exceptions:read` for their summaries; `organization:read` (search) | `violations:manage` (acknowledge security events) |
| [Compliance reports](compliance-exports.md) | `security:read`; `audit:read` for `policy_changes` | |
| [Repositories](repositories.md) | `repositories:read` | `repositories:manage` |
| [Repository groups](repository-groups.md) | `repositories:read` | `repositories:manage` |
| [Scans](scans.md) | `scans:read` | `scans:trigger` (re-scan) |
| [Violations](violations.md) | `violations:read` | `violations:manage` |
| [Rules](rules.md) | `rules:read` | `rules:manage` (organization rules) |
| [Policies](policies.md) | `policies:read` | `policies:write` (save); `policies:rollback` (roll back) |
| [Policy drafts](policy-drafts.md) | `policies:read` | `policies:write`, `policies:approve`, `policies:publish`, `policies:emergency` |
| [Simulations](simulations.md) | `policies:read` | `policies:write` |
| [Rollouts](rollouts.md) | `policies:read` | `policies:publish`; `policies:rollback` (roll back) |
| [Exceptions](exceptions.md) | `exceptions:read` | `exceptions:create`, `exceptions:approve`, `exceptions:revoke` |
| [Bulk operations](bulk-operations.md) | `repositories:read` | `repositories:manage`; `scans:trigger` for `schedule_scan` |
| [Scan schedules](scan-schedules.md) | `security:read` | `security:manage` |
| [Audit log](audit.md) | `audit:read` | read-only |
| [GitHub installations](github-installations.md) | `repositories:read` | `github:manage` (sync) |
| [Notifications](notifications.md) | own inbox, filtered by each type's permission; `notifications:read` (settings); `notifications:manage` (deliveries) | own inbox and preferences; `notifications:manage` (organization settings, webhooks) |
| [Webhook and health](webhooks.md) | none (`/health`, `/ready`) | webhook signature (`/webhooks/github`) |
