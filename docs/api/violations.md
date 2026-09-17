# Violations

A violation is a finding that a policy acts on (`warn` or `block`), tracked
across scans of the same commit. For the lifecycle, see
[../dashboard.md](../dashboard.md#violations-and-their-lifecycle). The findings
of one scan are returned by
[`GET /api/v1/scans/{scan_id}`](scans.md#get-apiv1scansscan_id).

Source: handlers in `src/commitguard/api/app.py`; `DashboardQueries` in
`src/commitguard/controlplane/queries.py`; `acknowledge_violation` and
`remove_acknowledgement` in `src/commitguard/controlplane/commands.py`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| [`GET /api/v1/violations`](#get-apiv1violations) | `violations:read` | `read` (`search` with `q`) |
| [`GET /api/v1/violations/{violation_id}`](#get-apiv1violationsviolation_id) | `violations:read` | `read` |
| [`PUT /api/v1/violations/{violation_id}/acknowledgement`](#put-apiv1violationsviolation_idacknowledgement) | `violations:manage` | `write` |
| [`DELETE /api/v1/violations/{violation_id}/acknowledgement`](#delete-apiv1violationsviolation_idacknowledgement) | `violations:manage` | `write` |

The API cannot resolve a violation. A violation becomes `resolved` when
CommitGuard no longer detects it. An acknowledgement records that someone has
seen the violation. It does not change GitHub checks or enforcement.

`{violation_id}` is 32 lowercase hexadecimal characters. Visibility follows the
same rules as scans: the organization must grant `violations:read`, and GitHub
must have listed the installation and the repository for the session. Otherwise
the answer is `404 NOT_FOUND`.

## Resource models

**ViolationSummary**

| Field | Type | Description |
|---|---|---|
| `id` | string | |
| `rule_id` | string | |
| `title` | string | |
| `severity` | string | `info`, `low`, `medium`, `high`, `critical` |
| `action` | string | `allow`, `warn`, `block` |
| `repository` | object | `{id, installation_id, full_name}` |
| `organization_id` | integer or null | |
| `commit_sha` | string or null | |
| `author` | string or null | |
| `status` | string | `open`, `acknowledged` or `resolved` |
| `first_detected_at` | string (date-time) | |
| `last_detected_at` | string (date-time) | |
| `detections` | integer | |

`acknowledged` means the violation is still open and someone has acknowledged it.

## GET /api/v1/violations

| Query parameter | Values | Default |
|---|---|---|
| `organization` | positive integer | |
| `repository` | positive integer | |
| `status` | `open` (open, not acknowledged), `acknowledged`, `resolved` | |
| `severity` | `info`, `low`, `medium`, `high`, `critical` | |
| `rule` | `ai_coauthor`, `ai_identity`, `ai_trailer`, `malformed_trailer`, `bot_identity` | |
| `action` | `block`, `warn` | |
| `from`, `to` | ISO 8601 date or date-time, compared with `last_detected_at`; `from` inclusive, `to` exclusive | |
| `q` | Up to 100 characters. Matches part of the rule ID, author or `owner/name`. A value of 4 to 64 hexadecimal characters also matches a commit SHA prefix. | |
| `sort` | `newest`, `oldest`, `severity` (highest first), `repository` (by full name) | `newest` |
| `cursor`, `limit` | See [pagination.md](pagination.md) | `limit` 25 |

Response `200`. `data` is an array of `ViolationSummary`; `meta` is
`{next_cursor, limit}`. Pagination uses a keyset cursor. A cursor is only valid
with the `sort` that produced it; with another sort it usually answers
`400 VALIDATION_ERROR` ("invalid cursor").

Errors: `400 VALIDATION_ERROR`; `403 FORBIDDEN` (no organization with
`violations:read`); `404 NOT_FOUND` (`organization` without `violations:read`).

```bash
curl -s 'https://commitguard.example.com/api/v1/violations?status=open&severity=critical' \
  -H 'Cookie: __Host-commitguard_session=<session-token>'
```

## GET /api/v1/violations/{violation_id}

Response `200`. `data` is a `ViolationDetail`:

| Field | Type | Description |
|---|---|---|
| `violation` | object | `ViolationSummary` |
| `detector` | string | |
| `message` | string | |
| `committer` | string or null | |
| `policy_reason` | string | |
| `evidence` | array | `{source, source_label, value, line_number, matched, notes}`, as in [scans.md](scans.md#resource-models) |
| `remediation` | string | |
| `recommended_steps` | array of strings | The last step always says CommitGuard does not rewrite Git history |
| `resolved_at` | string (date-time) or null | |
| `resolution` | string or null | |
| `acknowledgement` | object or null | `{by, at, note}` |
| `exposures` | array | At most 100 items: `{kind, label, active, opened_at, closed_at, closed_reason}`. `kind` is `pull_request`, `branch` or `merge_group`. |
| `detections` | array | At most 50 items: `{scan, result, detected_at, action, head_sha}` |
| `first_scan_id` | string | |
| `last_scan_id` | string | |
| `can_manage` | boolean | `true` when the caller has `violations:manage` and the violation is not resolved |

Errors: `404 NOT_FOUND`.

## PUT /api/v1/violations/{violation_id}/acknowledgement

Acknowledges an open violation. The route checks `violations:read`. The
command then requires `violations:manage` in the violation's organization.

| Body field | Type | Required | Description |
|---|---|---|---|
| `note` | string | no | At most 500 characters. It is trimmed and cleaned; a blank note is stored as `null`. |

Checks, in this order:

| Status | Code | When |
|---|---|---|
| 404 | `NOT_FOUND` | not visible |
| 403 | `FORBIDDEN` | caller lacks `violations:manage` |
| 400 | `VALIDATION_ERROR` (`field` `note`) | `note` is not text of at most 500 characters |
| 409 | `CONFLICT` | "Only open violations can be acknowledged." |

Acknowledging a violation that is already acknowledged succeeds. It replaces
the acknowledgement's author, time and note.

Response `200`. `data` is the updated `ViolationDetail`.

Side effect: a `violation_acknowledged` audit event.

```bash
curl -s -X PUT https://commitguard.example.com/api/v1/violations/4b2e9a7c0d1f43e8b6a5c9d2e7f01a3b/acknowledgement \
  -H 'Cookie: __Host-commitguard_session=<session-token>' \
  -H 'Origin: https://commitguard.example.com' \
  -H 'X-CSRF-Token: <csrf-token>' \
  -H 'Content-Type: application/json' \
  -d '{"note": "Author contacted; commit will be amended."}'
```

## DELETE /api/v1/violations/{violation_id}/acknowledgement

Removes the acknowledgement. This endpoint takes no body.

| Status | Code | When |
|---|---|---|
| 404 | `NOT_FOUND` | not visible |
| 403 | `FORBIDDEN` | caller lacks `violations:manage` |
| 409 | `CONFLICT` | "This violation is not acknowledged." |

Response `200`. `data` is the updated `ViolationDetail`.

Side effect: a `violation_acknowledgement_removed` audit event.
