# Scans

A scan is one execution of CommitGuard over a range of commits, created by a
webhook, a merge queue, a scan schedule or a manual request. Findings are part
of the scan detail. No separate findings endpoint exists. For the
lifecycle and the meaning of each result, see
[../dashboard.md](../dashboard.md#scans).

Source: handlers in `src/commitguard/api/app.py`; `DashboardQueries` in
`src/commitguard/controlplane/queries.py`; `ControlPlaneCommands.request_rescan`
in `src/commitguard/controlplane/commands.py`; models in
`src/commitguard/controlplane/views.py`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| [`GET /api/v1/scans`](#get-apiv1scans) | `scans:read` | `read` (`search` with `q`) |
| [`GET /api/v1/scans/{scan_id}`](#get-apiv1scansscan_id) | `scans:read` | `read` |
| [`GET /api/v1/scans/{scan_id}/comparison`](#get-apiv1scansscan_idcomparison) | `scans:read` | `read` |
| [`GET /api/v1/scans/{scan_id}/executions`](#get-apiv1scansscan_idexecutions) | `scans:read` | `read` |
| [`POST /api/v1/scans/{scan_id}/rescan`](#post-apiv1scansscan_idrescan) | `scans:read` to see the scan, `scans:trigger` to repeat it | `github` |

Scheduled scans are configured through [scan-schedules.md](scan-schedules.md).
Bulk re-scans are configured through [bulk-operations.md](bulk-operations.md).

## Identifiers and visibility

`{scan_id}` is the `id` field of a `ScanSummary`: 32 lowercase hexadecimal
characters that identify one execution. It is **not** the nullable `scan_id`
field inside the summary.

A scan is visible when all of these hold:

- its organization grants `scans:read` to the caller;
- its installation is one GitHub listed for the session;
- its repository is one GitHub listed for the session.

Anything else is `404 NOT_FOUND`. See [authorization.md](authorization.md).

## Resource models

**ScanSummary**

| Field | Type | Description |
|---|---|---|
| `id` | string | Execution ID (path parameter of the scan endpoints) |
| `scan_id` | string or null | Core scan ID, when the scan produced a result |
| `repository` | object | `{id, installation_id, full_name}` |
| `organization_id` | integer or null | |
| `event` | string | GitHub event that caused the scan |
| `pull_request_number` | integer or null | |
| `ref` | string or null | |
| `base_sha` | string or null | |
| `head_sha` | string | |
| `check_name` | string | |
| `result` | string | `queued`, `running`, `pass`, `warning`, `blocked`, `error`, `cancelled` or `stale` |
| `commits_scanned` | integer or null | |
| `violations` | integer or null | |
| `warnings` | integer or null | |
| `findings` | integer or null | |
| `created_at` | string (date-time) | |
| `started_at` | string (date-time) or null | |
| `completed_at` | string (date-time) or null | |
| `duration_ms` | integer or null | |
| `requested_by` | string or null | Login of the user who requested a manual execution |
| `trigger` | string | `push`, `pull_request`, `merge_group`, `manual`, `rerun` or `retry`; falls back to the event name |
| `execution` | integer | Execution number within the scan |
| `failure_source` | string | `pull_request`, `push` or `merge_queue` |

**FindingView** (inside `ScanDetail.findings`)

| Field | Type |
|---|---|
| `id` | integer |
| `violation_id` | string or null |
| `rule_id`, `detector`, `title`, `message` | string |
| `severity` | string (`info`, `low`, `medium`, `high`, `critical`) |
| `confidence` | string |
| `action` | string (`allow`, `warn`, `block`) |
| `reason` | string |
| `commit_sha`, `author`, `committer` | string or null |
| `evidence` | array of `{source, source_label, value, line_number, matched: [{kind, value, rule}], notes: [string]}` |
| `remediation` | string |

Text taken from Git metadata (for example `author`, `message` and evidence
values) is returned as plain text. Clients must render it as text, not HTML.

## GET /api/v1/scans

Lists visible scans.

| Query parameter | Values | Default |
|---|---|---|
| `organization` | positive integer | all organizations where the caller has `scans:read` |
| `repository` | positive integer (repository ID) | |
| `result` | `queued`, `running`, `pass`, `warning`, `blocked`, `error`, `cancelled`, `stale` | |
| `event` | `pull_request`, `push` | |
| `rule` | `ai_coauthor`, `ai_identity`, `ai_trailer`, `malformed_trailer`, `bot_identity` | |
| `severity` | `info`, `low`, `medium`, `high`, `critical`; scans with at least one finding of that severity | |
| `from`, `to` | ISO 8601 date or date-time, compared with `created_at`; `from` inclusive, `to` exclusive | |
| `q` | Text of up to 100 characters matching part of `owner/name`. A value of 4 to 64 hexadecimal characters also matches a `head_sha` prefix. | |
| `sort` | `newest`, `oldest` | `newest` |
| `cursor`, `limit` | See [pagination.md](pagination.md) | `limit` 25 |

Response `200`. `data` is an array of `ScanSummary`; `meta` is
`{next_cursor, limit}`. Pagination uses a keyset cursor.

Errors: `400 VALIDATION_ERROR` for any invalid parameter (`rule` answers
"unknown rule"); `403 FORBIDDEN` when the caller has `scans:read` in no
organization; `404 NOT_FOUND` when `organization` names an organization without
`scans:read` for the caller.

```bash
curl -s 'https://commitguard.example.com/api/v1/scans?result=blocked&limit=2' \
  -H 'Cookie: __Host-commitguard_session=<session-token>'
```

```json
{
  "data": [
    {
      "id": "0f6e2c9a4b1d47e8a3c5b7d9e1f20a4c",
      "scan_id": "b1c2d3e4f5a6478990a1b2c3d4e5f607",
      "repository": { "id": 7001, "installation_id": 42, "full_name": "example-org/api" },
      "organization_id": 5001,
      "event": "pull_request",
      "pull_request_number": 17,
      "ref": "refs/heads/feature",
      "base_sha": "1111111111111111111111111111111111111111",
      "head_sha": "2222222222222222222222222222222222222222",
      "check_name": "commitguard-app",
      "result": "blocked",
      "commits_scanned": 3,
      "violations": 1,
      "warnings": 0,
      "findings": 1,
      "created_at": "2026-09-17T09:30:00Z",
      "started_at": "2026-09-17T09:30:01Z",
      "completed_at": "2026-09-17T09:30:03Z",
      "duration_ms": 2100,
      "requested_by": null,
      "trigger": "pull_request",
      "execution": 1,
      "failure_source": "pull_request"
    }
  ],
  "meta": { "next_cursor": null, "limit": 2 }
}
```

## GET /api/v1/scans/{scan_id}

Response `200`. `data` is a `ScanDetail`:

| Field | Type | Description |
|---|---|---|
| `scan` | object | `ScanSummary` |
| `conclusion` | string or null | |
| `tool_version` | string or null | CommitGuard version that ran the scan |
| `rules_version` | string or null | |
| `policy_version` | string or null | |
| `policy_source` | string or null | |
| `organization_policy_version` | integer or null | |
| `effective_policies` | array | `{id, enabled, action}` for each rule |
| `detector_failures` | integer or null | |
| `notices` | array of strings | |
| `failure` | object or null | `{kind, message}`; set only for `error` and `cancelled` results |
| `findings` | array | `FindingView` |
| `can_rescan` | boolean | Whether `POST .../rescan` would be accepted now |
| `rescan_blocked_reason` | string or null | Why it would not |
| `executions` | integer | Number of executions of this scan |
| `latest_execution` | string | ID of the newest execution |
| `merge_group` | object or null | Merge group, as in [repositories.md](repositories.md#get-apiv1repositoriesrepository_idmerge-queue) |

Errors: `404 NOT_FOUND`.

## GET /api/v1/scans/{scan_id}/comparison

Compares the scan's findings with the previous completed (passed or failed)
execution in the same pull request or branch. Findings are matched by
fingerprint, up to 5,000 findings per scan.

Response `200`. `data` is a `ScanComparison`:

| Field | Type | Description |
|---|---|---|
| `scan_id` | string | |
| `previous_scan_id` | string or null | `null` when there is no earlier scan to compare with |
| `new` | array of strings | Fingerprints only in this scan |
| `resolved` | array of strings | Fingerprints only in the previous scan |
| `unchanged` | array of strings | Fingerprints in both |
| `new_findings` | array | `FindingView` for `new` |
| `resolved_findings` | array | `FindingView` for `resolved` |

Errors: `404 NOT_FOUND`.

## GET /api/v1/scans/{scan_id}/executions

Every execution of the same commits and check, newest first: at most 100. It
is not paginated.

Response `200`. `data` is an `ExecutionHistory`:

| Field | Type | Description |
|---|---|---|
| `scan_id` | string | The path parameter |
| `items` | array | `ExecutionView`, see below |
| `policy_changed` | boolean | Whether evaluated executions used different policy versions |
| `rules_changed` | boolean | Whether evaluated executions used different rules versions |

`ExecutionView`: `id`, `execution` (integer), `trigger`, `current` (boolean,
`true` for the first item), `result`, `head_sha`, `base_sha`,
`organization_policy_version` (integer or null), `policy_version`,
`rules_version`, `tool_version`, `conclusion`, `requested_by`, `failure`
(`{kind, message}` or null), `created_at`, `started_at`, `completed_at`,
`duration_ms`.

Errors: `404 NOT_FOUND`.

## POST /api/v1/scans/{scan_id}/rescan

Queues a new execution of exactly the commits the stored scan covered. The
repository, commits and event come from the stored scan, and the request has
no body. The new execution uses the current effective policy.

Checks, in this order:

| Status | Code | When |
|---|---|---|
| 404 | `NOT_FOUND` | the scan is not visible with `scans:read` |
| 403 | `FORBIDDEN` | the caller lacks `scans:trigger` in the scan's organization |
| 409 | `CONFLICT` | "This scan has not finished yet." (queued or running) |
| 409 | `CONFLICT` | "A newer scan exists for this pull request or branch." |
| 409 | `CONFLICT` | "The GitHub App installation is not active." |
| 409 | `CONFLICT` | "The GitHub App no longer has access to this repository." |
| 409 | `CONFLICT` | "Monitoring is paused for this repository." |
| 409 | `CONFLICT` | "A scan of these commits is already queued or running." |

Response `202`:

```json
{ "data": { "scan": "<new-execution-id>", "result": "queued" }, "meta": {} }
```

`scan` is the ID of the new execution. It has trigger `manual`, and
`requested_by` is the caller's login.

Side effect: a `scan_requested` audit event.

```bash
curl -s -X POST https://commitguard.example.com/api/v1/scans/0f6e2c9a4b1d47e8a3c5b7d9e1f20a4c/rescan \
  -H 'Cookie: __Host-commitguard_session=<session-token>' \
  -H 'Origin: https://commitguard.example.com' \
  -H 'X-CSRF-Token: <csrf-token>'
```
