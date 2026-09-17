# Repositories

Repository endpoints cover protection and enforcement status, the merge queue,
pausing and resuming monitoring, onboarding, and monitor or enforce mode. For
the concepts, see [../repository-management.md](../repository-management.md) and
[../dashboard.md](../dashboard.md#repository-protection-and-enforcement-status).

Source: handlers in `src/commitguard/api/app.py` and
`src/commitguard/api/governance.py`; `DashboardQueries` and
`ControlPlaneCommands` in `src/commitguard/controlplane/`;
`src/commitguard/governance/inventory.py`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| [`GET /api/v1/repositories`](#get-apiv1repositories) | `repositories:read` | `read` (`search` with `q`) |
| [`GET /api/v1/repositories/{repository_id}`](#get-apiv1repositoriesrepository_id) | `repositories:read` | `read` |
| [`GET /api/v1/repositories/{repository_id}/merge-queue`](#get-apiv1repositoriesrepository_idmerge-queue) | `repositories:read` | `read` |
| [`PUT /api/v1/repositories/{repository_id}/monitoring`](#put-apiv1repositoriesrepository_idmonitoring) | `repositories:manage` | `write` |
| [`POST /api/v1/repositories/{repository_id}/enforcement/refresh`](#post-apiv1repositoriesrepository_idenforcementrefresh) | `repositories:manage` | `github` |
| [`POST /api/v1/organizations/{organization_id}/repositories/onboard`](#post-apiv1organizationsorganization_idrepositoriesonboard-and-mode) | `repositories:manage` | `write` |
| [`POST /api/v1/organizations/{organization_id}/repositories/mode`](#post-apiv1organizationsorganization_idrepositoriesonboard-and-mode) | `repositories:manage` | `sensitive` |

Related endpoints:

- The effective policy of a repository:
  [policies.md](policies.md#get-apiv1repositoriesrepository_ideffective-policy).
- The security matrix of an organization's repositories:
  [security-posture.md](security-posture.md).
- Repository groups: [repository-groups.md](repository-groups.md).

## Visibility

`{repository_id}` is the GitHub repository ID. A repository is visible when all
of these hold:

- its organization grants `repositories:read` to the caller;
- its installation is one GitHub listed for the session;
- GitHub listed the repository itself for the session.

Anything else is `404 NOT_FOUND`. The two write routes
(`monitoring` and `enforcement/refresh`) declare `repositories:read` in the route
table. The command then requires `repositories:manage` in the repository's
organization and answers `403 FORBIDDEN` without it.

## RepositorySummary

| Field | Type | Description |
|---|---|---|
| `id` | integer | GitHub repository ID |
| `installation_id` | integer | |
| `organization` | object or null | `{id, login, type}` |
| `owner`, `name`, `full_name` | string | |
| `github_url` | string | |
| `default_branch` | string or null | |
| `protection` | string | `protected`, `at_risk`, `unprotected`, `configuration_error` or `unknown` |
| `protection_reason` | string | |
| `app_connection` | string | `connected`, `suspended` or `disconnected` |
| `monitoring_enabled` | boolean | |
| `last_scan` | object or null | [`ScanSummary`](scans.md#resource-models) |
| `open_violations` | integer | Open violations with action `block` |
| `open_warnings` | integer | Open violations with action `warn` |
| `critical_open` | integer | |

## GET /api/v1/repositories

| Query parameter | Values | Default |
|---|---|---|
| `organization` | positive integer | |
| `protection` | `protected`, `at_risk`, `unprotected`, `configuration_error`, `unknown` | |
| `q` | up to 100 characters, part of `owner/name` | |
| `sort` | `name`, `risk` (most critical first), `recent` (latest scan first) | `name` |
| `cursor`, `limit` | See [pagination.md](pagination.md); offset cursor | `limit` 25 |

Response `200`. `data` is an array of `RepositorySummary`; `meta` is
`{next_cursor, limit}`.

Errors: `400 VALIDATION_ERROR`; `403 FORBIDDEN` (no organization with
`repositories:read`); `404 NOT_FOUND` (`organization` without `repositories:read`).

## GET /api/v1/repositories/{repository_id}

Response `200`. `data` is a `RepositoryDetail`:

| Field | Type | Description |
|---|---|---|
| `repository` | object | `RepositorySummary` |
| `enforcement` | object | See below |
| `organization_policy_version` | integer or null | |
| `effective_policies` | array | `{id, enabled, action}` |
| `effective_policy_scan` | string or null | Scan whose effective policy is shown |
| `recent_scans` | array | Up to 10 `ScanSummary` |
| `open_violations` | array | Up to 10 [`ViolationSummary`](violations.md#resource-models), by severity |
| `audit` | array | Up to 10 [`AuditEventView`](audit.md#auditeventview). Empty unless the caller has `audit:read`. |
| `permissions` | object | `{manage, trigger_scans, read_audit}` booleans |

`enforcement` object:

| Field | Shape |
|---|---|
| `github_app` | `{status, detail, checked_at}`; `status` is `connected`, `suspended` or `disconnected` |
| `github_actions` | `{status, detail, checked_at}`; `status` is `detected`, `not_detected` or `unknown` |
| `required_check` | `{status, branch, required_checks, detail, checked_at}`; `status` is `required`, `not_required` or `unknown` |
| `latest_check` | `{result, scan, head_sha, completed_at}` |
| `local_hooks` | `{status, detail, checked_at}`; `status` is always `not_verifiable` |
| `merge_queue` | `{status, detail, checked_at}`; `status` is `enabled`, `not_enabled` or `unknown` |
| `monitoring_enabled` | boolean |

Errors: `404 NOT_FOUND`.

## GET /api/v1/repositories/{repository_id}/merge-queue

Response `200`. `data` is a `MergeQueueView`:

| Field | Type | Description |
|---|---|---|
| `repository_id` | integer | |
| `status` | string | `enabled`, `not_enabled` or `unknown` |
| `detail` | string | |
| `checked_at` | string (date-time) or null | |
| `permission` | string | `granted` or `missing`: whether the installation can read merge queues |
| `current` | object or null | The merge group whose checks are requested |
| `recent` | array | Up to 10 merge groups |

A merge group has these fields:

- `head_sha`, `base_sha`, `base_ref`
- `pull_requests`: an array of integers
- `state`: `checks_requested` or `destroyed`
- `destroyed_reason`
- `result`: a scan result or `null`
- `scan`
- `created_at`, `updated_at`
- `validated_at`: a date-time or `null`

Errors: `404 NOT_FOUND`.

## PUT /api/v1/repositories/{repository_id}/monitoring

Pauses or resumes CommitGuard scans for one repository.

| Body field | Type | Required | Description |
|---|---|---|---|
| `enabled` | boolean | yes | `false` pauses monitoring, `true` resumes it |
| `confirm` | boolean | to pause | Must be `true` |
| `reason` | string | to pause | At most 500 characters |

Checks, in this order:

| Status | Code | When |
|---|---|---|
| 400 | `VALIDATION_ERROR` (`field` `enabled`) | `enabled` is not a boolean. This is checked before visibility. |
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not visible / lacks `repositories:manage` |
| 400 | `VALIDATION_ERROR` (`field` `reason`) | pausing without a reason, or reason too long |
| 409 | `CONFIRMATION_REQUIRED` | pausing without `confirm: true` |
| 401 | `REAUTHENTICATION_REQUIRED` | pausing with a last sign-in more than 15 minutes ago |
| 409 | `CONFLICT` | "Monitoring is already enabled." / "Monitoring is already paused." |

Response `200`. `data` is the updated `RepositoryDetail`.

Side effects: pausing cancels the repository's queued scans. The request writes a
`repository_monitoring_disabled` or `repository_monitoring_enabled` audit event.

```bash
curl -s -X PUT https://commitguard.example.com/api/v1/repositories/7001/monitoring \
  -H 'Cookie: __Host-commitguard_session=<session-token>' \
  -H 'Origin: https://commitguard.example.com' \
  -H 'X-CSRF-Token: <csrf-token>' \
  -H 'Content-Type: application/json' \
  -d '{"enabled": false, "confirm": true, "reason": "Repository is being migrated"}'
```

## POST /api/v1/repositories/{repository_id}/enforcement/refresh

Reads enforcement evidence from GitHub now: GitHub Actions, branch protection
and required checks, and the merge queue. This endpoint takes no body.

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not visible / lacks `repositories:manage` |
| 409 | `CONFLICT` | "GitHub denied access: <detail>" |
| 502 | `GITHUB_UNAVAILABLE` | GitHub could not be reached, or the status could not be checked |

Response `200`. `data` is the updated `RepositoryDetail`.

Side effects:

- An `enforcement_status_checked` audit event.
- A `repository_unprotected` notification when the check was required before
  and no longer is.

## POST /api/v1/organizations/{organization_id}/repositories/onboard and /mode

`onboard` marks repositories as onboarded in a mode. `mode` switches repositories
between `monitor` and `enforce` and keeps their onboarding state.

| Body field | Type | Required | Description |
|---|---|---|---|
| `repository_ids` | array of integers | yes | 1 to 5,000 repository IDs, all in this organization and listed for the session |
| `mode` | string | yes | `monitor` or `enforce` |
| `confirm` | boolean | when a mode changes | Must be `true` |
| `reason` | string | when switching to `monitor` | At most 500 characters |

Checks, in this order:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not a member / lacks `repositories:manage` (checked before the body is read) |
| 400 | `VALIDATION_ERROR` | invalid `repository_ids` or `mode` |
| 404 | `NOT_FOUND` | "N of the selected repositories were not found in this organization." |
| 400 | `VALIDATION_ERROR` (`field` `reason`) | reason too long |
| 409 | `CONFIRMATION_REQUIRED` | a repository's mode would change and `confirm` is not `true`; the message explains the effect of the target mode |
| 400 | `VALIDATION_ERROR` (`field` `reason`) | "A reason is required to stop blocking (monitor mode)." |

No recent sign-in is required. No confirmation is needed when no repository's mode
changes.

Response `200`: `{"data": {"changed": [7001, 7002]}, "meta": {}}`. `changed`
lists, sorted, the repositories whose mode changed or that were newly onboarded.

Side effects: `repository_mode_changed` and `repository_onboarded` audit events;
the effective policy of repositories whose mode changed is marked stale.
