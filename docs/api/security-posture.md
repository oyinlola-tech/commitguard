# Overview, security posture and search

These are read models that summarize what CommitGuard protects: the dashboard
overview, organization posture, the repository security matrix, trends,
security events and organization search. For how posture is decided, see
[../security-posture.md](../security-posture.md).

Source: `DashboardQueries.overview` in
`src/commitguard/controlplane/queries.py`;
`src/commitguard/governance/posture.py`; handlers in
`src/commitguard/api/app.py` and `src/commitguard/api/governance.py`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| [`GET /api/v1/dashboard/overview`](#get-apiv1dashboardoverview) | `repositories:read` | `read` |
| [`GET /api/v1/organizations/{organization_id}/security/overview`](#get-apiv1organizationsorganization_idsecurityoverview) | `security:read` | `read` |
| [`GET /api/v1/organizations/{organization_id}/security/repositories`](#get-apiv1organizationsorganization_idsecurityrepositories) | `security:read` | `read` (`search` with `q`) |
| [`GET /api/v1/organizations/{organization_id}/security/policies`](#get-apiv1organizationsorganization_idsecuritypolicies) | `policies:read` | `read` |
| [`GET /api/v1/organizations/{organization_id}/security/exceptions`](#get-apiv1organizationsorganization_idsecurityexceptions) | `exceptions:read` | `read` |
| [`GET /api/v1/organizations/{organization_id}/security/trends`](#get-apiv1organizationsorganization_idsecuritytrends) | `security:read` | `read` |
| [`GET /api/v1/organizations/{organization_id}/security/events`](#get-apiv1organizationsorganization_idsecurityevents) | `security:read` | `read` |
| [`POST /api/v1/organizations/{organization_id}/security/events/{event_id}/acknowledge`](#post-apiv1organizationsorganization_idsecurityeventsevent_idacknowledge) | `violations:manage` | `write` |
| [`GET /api/v1/organizations/{organization_id}/search`](#get-apiv1organizationsorganization_idsearch) | `organization:read` | `search` |

Point-in-time exports are described in [compliance-exports.md](compliance-exports.md).

For organization routes, a non-member gets `404 NOT_FOUND`, and a member without
the permission gets `403 FORBIDDEN`. Responses are computed on each request. The
API does not cache them; `computed_at` is the time of the request.

## GET /api/v1/dashboard/overview

A summary across the caller's organizations, or one organization.

| Query parameter | Values | Default |
|---|---|---|
| `period` | `24h`, `7d`, `30d` | `7d` |
| `organization` | positive integer | all accessible organizations |

Repository figures use the caller's `repositories:read` scope, scan figures
the `scans:read` scope, and violation figures the `violations:read` scope.

Response `200`. `data` is an `OverviewView`:

| Field | Type | Description |
|---|---|---|
| `period` | object | `{key, start, end}` |
| `summary` | object | Integers: `repositories_monitored`, `repositories_protected`, `repositories_at_risk`, `repositories_unprotected`, `repositories_unknown`, `repositories_configuration_error`, `scans`, `scans_passed`, `scans_blocked`, `scans_error` (scans created in the period), `open_violations`, `open_warnings`, `critical_open`, `high_open` (open now) |
| `integration` | object | `{status, installations_connected, installations_suspended, installations_disconnected, detail}`; `status` is `connected`, `action_required` or `disconnected` |
| `health` | array | `{id, label, status, detail}`. `id` is `github_integration`, `required_check`, `critical_violations`, `open_violations`, `scan_errors` or `configuration`. `status` is `ok`, `attention` or `unknown`. |
| `recent_scans` | array | Up to 8 [`ScanSummary`](scans.md#resource-models) |
| `recent_violations` | array | Up to 8 [`ViolationSummary`](violations.md#resource-models) |
| `repository_health` | array | Up to 8 [`RepositorySummary`](repositories.md#repositorysummary), riskiest first |

Errors: `400 VALIDATION_ERROR`; `403 FORBIDDEN` (no organization with
`repositories:read`); `404 NOT_FOUND` (`organization` without it).

## GET /api/v1/organizations/{organization_id}/security/overview

Response `200`. `data` is an `OrganizationPostureView`:

| Field | Type | Description |
|---|---|---|
| `organization_id` | integer | |
| `login`, `type`, `github_url` | string | |
| `posture` | string | `secure`, `at_risk`, `unprotected` or `unknown` |
| `posture_reasons` | array of strings | |
| `members` | integer | |
| `repositories` | integer | Repositories listed for the session |
| `required_repositories` | integer | Connected, not archived, onboarded (or not yet tracked) |
| `compliant_repositories` | integer | Required repositories with `secure` posture |
| `compliance` | string | "N of M required repositories satisfy all mandatory controls" |
| `by_posture` | object | Posture → count |
| `by_protection` | object | Protection status → count |
| `monitor_mode` | integer | |
| `critical_open`, `high_open` | integer | |
| `active_exceptions`, `expiring_exceptions`, `expired_exceptions_30d` | integer | Organization-wide, not limited to repositories listed for the session |
| `drift` | object | `{compliant, customized, drift, unknown}` counts |
| `installations` | array | `{installation_id, account_login, state, sync, sync_detail, last_success_at, repositories}`. `sync` is `healthy`, `syncing`, `degraded`, `failed` or `never`. |
| `policy` | object | `{organization_version, updated_at, updated_by, approvals_pending, exceptions_requested, rollouts_in_progress, propagation, baseline}` |
| `recent_activity` | array | Up to 12 `{id, type, occurred_at, actor}`; empty unless the caller has `audit:read` |
| `computed_at` | string (date-time) | |

## GET /api/v1/organizations/{organization_id}/security/repositories

The repository security matrix: one row per repository listed for the session.

| Query parameter | Values |
|---|---|
| `q` | Part of `owner/name`, case-insensitive |
| `group` | Group ID (32 hexadecimal characters) |
| `posture` | `secure`, `at_risk`, `unprotected`, `unknown` |
| `protection` | `protected`, `at_risk`, `unprotected`, `configuration_error`, `unknown` |
| `mode` | `monitor`, `enforce` |
| `onboarding` | `discovered`, `onboarded`, `excluded` |
| `policy_state` | `up_to_date`, `stale`, `syncing`, `error`, `pending` |
| `drift` | `compliant`, `customized`, `drift`, `unknown` |
| `exceptions` | `active`, `expiring`, `none` |
| `severity` | `critical` (open critical violations), `any` (any open violation or warning) |
| `last_scan` | `never`, or a number of days from 1 to 365 (not scanned within that many days, including never) |
| `sort` | `posture` (default, worst first), `name`, `violations`, `last_scan` (oldest first) |
| `cursor`, `limit` | Offset cursor; see [pagination.md](pagination.md) |

An empty value is ignored. An invalid value answers `400 VALIDATION_ERROR`
with `field` set to the parameter name. `cursor` and `limit` are validated before
the permission check.

Response `200`. `data` is an array of `RepositoryPosture`. `meta` is
`{next_cursor, limit, total, computed_at}`, where `total` counts rows after filtering.

| Field | Type | Description |
|---|---|---|
| `repository_id` | integer | |
| `full_name`, `github_url` | string | |
| `installation_id` | integer | |
| `groups` | array | `{id, name}` of active groups |
| `connection` | string | `connected`, `suspended` or `disconnected` |
| `archived` | boolean | |
| `onboarding` | string | |
| `mode` | string | |
| `protection`, `protection_reason` | string | |
| `posture` | string | |
| `posture_reasons` | array of strings | |
| `organization_policy_version` | integer or null | |
| `policy_state` | string | |
| `last_scan_result` | string or null | |
| `last_scan_at` | string (date-time) or null | |
| `open_violations`, `open_warnings`, `critical_open` | integer | |
| `active_exceptions`, `expiring_exceptions` | integer | |
| `drift` | string | |
| `drift_differences` | array | `{policy_id, requested, requested_by, required, required_by, effective}` |

## GET /api/v1/organizations/{organization_id}/security/policies

Policy targets, drafts, rollouts and propagation in one response. It is not
paginated.

Response `200`. `data` has these fields:

| Field | Description |
|---|---|
| `targets` | Same as [`GET /api/v1/organizations/{organization_id}/policies`](policies.md#get-apiv1organizationsorganization_idpolicies) |
| `drafts` | Up to 200 [`PolicyDraftView`](policy-drafts.md#policydraftview) |
| `rollouts` | Up to 100 [`RolloutView`](rollouts.md#rolloutview) |
| `propagation` | [`PropagationStatus`](policies.md#get-apiv1organizationsorganization_idpolicy-propagation) |

## GET /api/v1/organizations/{organization_id}/security/exceptions

Response `200`. `data` is `{counts, exceptions}`:

- `exceptions`: up to 500 [`PolicyExceptionView`](exceptions.md#policyexceptionview),
  ordered requested, then active, then others.
- `counts`: a count per status that appears among those items, plus
  `expiring_soon`. With more than 500 exceptions, the counts cover only the
  returned items.

## GET /api/v1/organizations/{organization_id}/security/trends

| Query parameter | Values | Default |
|---|---|---|
| `days` | 1 to 365 | 30 |

Response `200`. `data` has these fields:

| Field | Type | Description |
|---|---|---|
| `organization_id`, `days` | integer | |
| `history` | array | `{day, values}` per day with data. `values` may contain `scans`, `blocked_scans`, `scan_errors`, `new_violations`, `new_critical`. |
| `snapshots` | array | `{day, values}` from daily snapshots: `protected_repositories`, `unprotected_repositories`, `open_violations`, `critical_open`, `active_exceptions`, `drift_repositories` |
| `snapshot_note` | string | |
| `computed_at` | string (date-time) | |

Trends are organization-wide. They are not limited to repositories listed for
the session.

Errors: `400 VALIDATION_ERROR` ("days must be a number", checked before the
permission; "days must be between 1 and 365").

## GET /api/v1/organizations/{organization_id}/security/events

The 50 most recent critical and high security events. Events for repositories
not listed for the session are removed after that limit, so fewer may be
returned. The list is not paginated.

Response `200`. `data` is an array of objects with these fields:

- `id`, `type`, `severity`
- `repository_id`: an integer or `null`
- `resource_type`, `resource_id`, `title`, `body`
- `occurrences`, `last_occurred_at`
- `acknowledged_by`, `acknowledged_at`: `null` until acknowledged

## POST /api/v1/organizations/{organization_id}/security/events/{event_id}/acknowledge

Records that someone has seen an event. It resolves nothing.

| Body field | Type | Required | Description |
|---|---|---|---|
| `note` | string | no | At most 500 characters |

Checks, in this order: `404`/`403` (`violations:manage`); `404` for an event of
another organization; `409 CONFLICT` ("Only critical and high security events are
acknowledged."); `400` for an invalid `note`; `409 CONFLICT` ("This event was
already acknowledged.").

Response `200`. `data` is `{event_id, acknowledged_by, acknowledged_at, meaning}`.

Side effect: a `security_event_acknowledged` audit event.

## GET /api/v1/organizations/{organization_id}/search

| Query parameter | Required | Description |
|---|---|---|
| `q` | yes | 2 to 100 characters |

Returns up to 50 results in total. It is not paginated.

| `kind` | Requires | Matches |
|---|---|---|
| `repository` | `repositories:read` | `owner/name`, repositories listed for the session (up to 10) |
| `group` | `repositories:read` | active group names (up to 10) |
| `policy` | `policies:read` | policy draft titles (up to 10) |
| `rule` | `rules:read` | bundled rule ID or name |
| `exception` | `exceptions:read` | exception rule ID or reason (up to 20) |
| `finding` | `violations:read` | violation rule ID, title or commit SHA prefix, for repositories listed for the session (up to 10) |

Response `200`. `data` is an array of `{kind, id, title, detail, link}`.
`link` is a dashboard path.

Errors: `400 VALIDATION_ERROR` ("q must be 2-100 characters", `field` `q`).
