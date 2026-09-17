# Scan schedules

A scan schedule queues default-branch scans for an organization, a repository
group or a repository, daily or weekly at a local time. A background task runs
due schedules. For the concepts, see
[../repository-management.md](../repository-management.md).

Source: `src/commitguard/governance/schedules.py`; handlers in
`src/commitguard/api/governance.py`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| [`GET /api/v1/organizations/{organization_id}/scan-schedules`](#get-apiv1organizationsorganization_idscan-schedules) | `security:read` | `read` |
| [`POST /api/v1/organizations/{organization_id}/scan-schedules`](#post-apiv1organizationsorganization_idscan-schedules) | `security:manage` | `write` |
| [`GET /api/v1/scan-schedules/{schedule_id}`](#get-apiv1scan-schedulesschedule_id) | `security:read` | `read` |
| [`PATCH /api/v1/scan-schedules/{schedule_id}`](#patch-apiv1scan-schedulesschedule_id) | `security:manage` | `write` |
| [`POST /api/v1/scan-schedules/{schedule_id}/disable`](#post-apiv1scan-schedulesschedule_iddisable) | `security:manage` | `write` |

A non-member gets `404 NOT_FOUND`, and a member without the permission gets
`403 FORBIDDEN`. For `/scan-schedules/{schedule_id}`, the organization comes
from the stored schedule. Schedules cannot be deleted; disable them instead.

## ScanScheduleView

| Field | Type | Description |
|---|---|---|
| `id` | string | 32 hexadecimal characters |
| `organization_id` | integer | |
| `name` | string | |
| `target` | object | `{type, id, label}`. `type` is `organization`, `group` or `repository`. `label` is `(removed)` when the target no longer exists. |
| `cadence` | string | `daily` or `weekly` |
| `hour` | integer | 0 to 23 |
| `minute` | integer | 0 to 59 |
| `weekday` | integer or null | 0 (Monday) to 6 (Sunday); `null` for daily |
| `timezone` | string | IANA time zone |
| `enabled` | boolean | |
| `next_run_at` | string (date-time) or null | `null` while disabled |
| `revision` | integer | Starts at 1; use as `expected_revision` |
| `repositories_covered` | integer | Repositories of the organization in the target, including ones not listed for the session |
| `last_run` | object or null | `ScheduleRunView` |
| `created_by`, `updated_by` | string | |
| `created_at`, `updated_at` | string (date-time) | |
| `can_manage` | boolean | Whether the caller has `security:manage` |

**ScheduleRunView**: `id`, `slot` (date-time), `state` (`running`, `completed`,
`partial`, `failed`), `started_at`, `completed_at` (or null), `repositories`,
`queued`, `skipped`, `failed` (integers), `detail` (object: skip reason → count).

## GET /api/v1/organizations/{organization_id}/scan-schedules

Lists up to 100 schedules, oldest first. It is not paginated.

Response `200`. `data` is an array of `ScanScheduleView`.

## POST /api/v1/organizations/{organization_id}/scan-schedules

| Body field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | At most 100 characters |
| `target_type` | string | yes | `organization`, `group` or `repository` |
| `target_id` | string or integer | for `group` and `repository` | Omitted, `null` or `""` for `organization`. A group ID (active group) for `group`. A repository ID for `repository`; the repository must be listed for the session. |
| `cadence` | string | yes | `daily` or `weekly` |
| `hour` | integer | yes | 0 to 23 |
| `minute` | integer | no | 0 to 59, default 0 |
| `weekday` | integer | for `weekly` | 0 (Monday) to 6 (Sunday). Ignored for `daily`. |
| `timezone` | string | no | IANA time zone, at most 64 characters. Defaults to the organization's `timezone` setting (default `UTC`). |
| `enabled` | boolean | no | Default `true` |

Errors:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not a member / lacks `security:manage` |
| 400 | `VALIDATION_ERROR` | invalid field (`field` names it) |
| 404 | `NOT_FOUND` | the group or repository target is not in this organization or not listed for the session |
| 409 | `CONFLICT` | "An organization can have at most 100 schedules." (disabled schedules count) |

Response `201`. `data` is a `ScanScheduleView`. `next_run_at` is the first
matching local time strictly after now.

Side effect: a `scan_schedule_created` audit event.

```json
{
  "name": "Nightly default branches",
  "target_type": "organization",
  "cadence": "daily",
  "hour": 2,
  "minute": 0,
  "timezone": "Europe/London"
}
```

## GET /api/v1/scan-schedules/{schedule_id}

Response `200`. `data` is `{schedule, runs}`: a `ScanScheduleView` and up to 20
`ScheduleRunView` items, newest slot first.

Errors: `404 NOT_FOUND`.

## PATCH /api/v1/scan-schedules/{schedule_id}

| Body field | Type | Required | Description |
|---|---|---|---|
| `expected_revision` | integer | yes | The `revision` you read |
| `name`, `cadence`, `hour`, `minute`, `weekday`, `timezone`, `enabled` | as for creation | no | Each is validated together with the stored values. For example, switching to `weekly` needs a `weekday`. |

The target cannot be changed, and unknown fields are ignored. Every accepted
request increments `revision` and recomputes `next_run_at`, even when nothing
changed.

Errors: `404`, `403`, `400 VALIDATION_ERROR`, and `409 CONFLICT` ("The schedule
was changed by someone else. Reload first.") when `expected_revision` is not
current.

Response `200`. `data` is the updated `ScanScheduleView`.

Side effect: a `scan_schedule_disabled` audit event when the schedule goes from
enabled to disabled, otherwise `scan_schedule_changed`.

## POST /api/v1/scan-schedules/{schedule_id}/disable

Disables the schedule at its current revision. This endpoint takes no body.
Disabling a schedule that is already disabled succeeds, increments `revision`
and writes `scan_schedule_changed`.

Response `200`. `data` is the updated `ScanScheduleView`.

Errors: `404`, `403`, and `409 CONFLICT` if the schedule changes concurrently.
