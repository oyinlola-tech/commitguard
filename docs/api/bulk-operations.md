# Bulk operations

A bulk operation applies one change to many repositories of an organization.
The change can be group membership, onboarding, mode, monitoring or a scan. The
request only queues the operation. A background task processes its items in
batches. For the concepts, see
[../repository-management.md](../repository-management.md).

Source: `src/commitguard/governance/bulk.py`; handlers in
`src/commitguard/api/governance.py`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| [`GET /api/v1/organizations/{organization_id}/bulk-operations`](#get-apiv1organizationsorganization_idbulk-operations) | `repositories:read` | `read` |
| [`POST /api/v1/organizations/{organization_id}/bulk-operations`](#post-apiv1organizationsorganization_idbulk-operations) | `repositories:manage`; `scans:trigger` for `schedule_scan` | `sensitive` |
| [`GET /api/v1/bulk-operations/{operation_id}`](#get-apiv1bulk-operationsoperation_id) | `repositories:read` | `read` |
| [`POST /api/v1/bulk-operations/{operation_id}/cancel`](#post-apiv1bulk-operationsoperation_idcancel) | as for creation | `write` |
| [`POST /api/v1/bulk-operations/{operation_id}/retry`](#post-apiv1bulk-operationsoperation_idretry) | as for creation | `write` |

A non-member gets `404 NOT_FOUND`, and a member without the permission gets
`403 FORBIDDEN`. For `/bulk-operations/{operation_id}`, the organization comes
from the stored operation. No recent sign-in is required.

## Types and statuses

| `type` | Effect per repository | `parameters` |
|---|---|---|
| `add_to_group` | Add to a repository group | `{"group_id": "<group-id>"}` |
| `remove_from_group` | Remove from a repository group | `{"group_id": "<group-id>"}` |
| `onboard` | Onboard in a mode | `{"mode": "monitor" \| "enforce", "reason": "..."}` |
| `set_mode` | Switch mode | `{"mode": "monitor" \| "enforce", "reason": "..."}` |
| `set_monitoring` | Pause or resume monitoring | `{"enabled": true \| false}` |
| `schedule_scan` | Queue a default-branch scan | ignored |

Operation `status`: `queued`, `running`, `completed`, `partial`, `failed`,
`cancelled`. When processing ends, the status is `completed` if no item failed.
It is `failed` if items failed and none completed or was skipped, and `partial`
otherwise.

Item `status`: `pending`, `completed`, `failed`, `skipped`, `cancelled`.

## BulkOperationView

| Field | Type | Description |
|---|---|---|
| `id` | string | 32 hexadecimal characters |
| `organization_id` | integer | |
| `type` | string | |
| `parameters` | object | As stored |
| `status` | string | |
| `total` | integer | Repositories at creation |
| `completed`, `failed`, `skipped`, `pending` | integer | Current item counts. `completed` does not include skipped items. |
| `requested_by` | string | |
| `created_at` | string (date-time) | |
| `started_at`, `completed_at` | string (date-time) or null | |
| `cancelled_by` | string or null | |
| `items` | array | `{repository_id, full_name, status, attempts, detail}`. `full_name` is `null` for repositories not listed for the session. |
| `can_manage` | boolean | Whether the caller holds the permission for this type |

## GET /api/v1/organizations/{organization_id}/bulk-operations

Lists the 50 newest operations, without items (`items` is `[]`). It is not
paginated.

Response `200`. `data` is an array of `BulkOperationView`.

## POST /api/v1/organizations/{organization_id}/bulk-operations

| Body field | Type | Required | Description |
|---|---|---|---|
| `type` | string | yes | See [Types and statuses](#types-and-statuses) |
| `repository_ids` | array of integers | yes | 1 to 5,000 repository IDs; de-duplicated. Every repository must belong to the organization and be listed for the session. |
| `parameters` | object | depends on `type` | |
| `idempotency_key` | string | no | 8 to 128 characters |
| `confirm` | boolean | for `onboard`, `set_mode`, and `set_monitoring` with `enabled: false` | Must be `true` |

Checks, in this order:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not a member / lacks `scans:trigger` (for `schedule_scan`) or `repositories:manage` (any other value, including an invalid one) |
| 400 | `VALIDATION_ERROR` (`field` `repository_ids`) | not a non-empty list of IDs, or more than 5,000 |
| 400 | `VALIDATION_ERROR` (`field` `type`) | unknown type |
| 404 | `NOT_FOUND` | "N of the selected repositories were not found in this organization." |
| 400 | `VALIDATION_ERROR` | invalid parameters. For group types: `group_id` missing (`field` `parameters.group_id`). For mode types: `mode` invalid (`field` `mode`), `reason` longer than 500 characters (`field` `reason`), or monitor mode without a reason (`field` `parameters.reason`). For `set_monitoring`: `enabled` not a boolean (`field` `parameters.enabled`). |
| 404 | `NOT_FOUND` | the group does not exist or belongs to another organization |
| 409 | `CONFIRMATION_REQUIRED` | `onboard` or `set_mode` without `confirm: true`, or `set_monitoring` with `enabled: false` without `confirm: true` |
| 400 | `VALIDATION_ERROR` (`field` `idempotency_key`) | not 8 to 128 characters |
| 409 | `CONFLICT` | 10 operations of the organization are already queued or running |

**Idempotency.** Each operation stores a key that is unique within the
organization, with no expiry. The key is `idempotency_key` if given. Otherwise
it is a fingerprint of the type, parameters and repositories. When the key
already exists, the existing operation is returned with `202`, and nothing new
is queued. The type, parameters and repositories are not compared. An identical
request without `idempotency_key` therefore returns the earlier operation, even
after it has finished.

Response `202`. `data` is a `BulkOperationView`.

Side effect: a `bulk_operation_requested` audit event. As items are processed,
the usual per-change audit events are written, followed by
`bulk_operation_finished`.

```bash
curl -s -X POST https://commitguard.example.com/api/v1/organizations/5001/bulk-operations \
  -H 'Cookie: __Host-commitguard_session=<session-token>' \
  -H 'Origin: https://commitguard.example.com' \
  -H 'X-CSRF-Token: <csrf-token>' \
  -H 'Content-Type: application/json' \
  -d '{"type": "set_mode", "repository_ids": [7001, 7002],
       "parameters": {"mode": "enforce"}, "confirm": true}'
```

## GET /api/v1/bulk-operations/{operation_id}

Returns the operation with up to 500 items: failed items first, then pending
items, then the rest, each group ordered by repository ID. `repositories:read`
is enough for every type.

Response `200`. `data` is a `BulkOperationView`.

Errors: `404 NOT_FOUND`.

## POST /api/v1/bulk-operations/{operation_id}/cancel

Cancels the pending items of a `queued` or `running` operation. This endpoint
takes no body.

Checks, in order: `404`, `403` (lacks the type's permission),
`409 CONFLICT` ("A <status> operation cannot be cancelled.").

Response `200`. `data` is the `BulkOperationView`, now `cancelled`.

Side effect: a `bulk_operation_cancelled` audit event.

## POST /api/v1/bulk-operations/{operation_id}/retry

Returns the failed items of a `partial` or `failed` operation to `pending`, and
queues the operation again. This endpoint takes no body.

Checks, in order: `404`, `403`, `409 CONFLICT` ("Only an operation with failed
items can be retried.").

Response `200`. `data` is the `BulkOperationView`, now `queued`.

This endpoint writes no audit event itself.
