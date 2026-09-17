# Policy rollouts

A staged rollout publishes a new organization or group policy version to a
growing set of repositories. It is started by publishing a draft with a
`rollout` object (see
[policy-drafts.md](policy-drafts.md#post-apiv1policy-draftsdraft_idpublish)).
For the concepts, see [../policy-rollouts.md](../policy-rollouts.md).

Source: `src/commitguard/governance/rollouts.py`; handlers in
`src/commitguard/api/governance.py`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| [`GET /api/v1/organizations/{organization_id}/rollouts`](#get-apiv1organizationsorganization_idrollouts) | `policies:read` | `read` |
| [`GET /api/v1/rollouts/{rollout_id}`](#get-apiv1rolloutsrollout_id) | `policies:read` | `read` |
| [`POST /api/v1/rollouts/{rollout_id}/advance`](#post-apiv1rolloutsrollout_idadvance) | `policies:publish` | `sensitive` |
| [`POST /api/v1/rollouts/{rollout_id}/pause`](#post-apiv1rolloutsrollout_idpause) | `policies:publish` | `write` |
| [`POST /api/v1/rollouts/{rollout_id}/resume`](#post-apiv1rolloutsrollout_idresume) | `policies:publish` | `sensitive` |
| [`POST /api/v1/rollouts/{rollout_id}/rollback`](#post-apiv1rolloutsrollout_idrollback) | `policies:rollback` | `sensitive` |

A non-member of the rollout's organization gets `404 NOT_FOUND`, and a member
without the permission gets `403 FORBIDDEN`.

## States

| State | Meaning |
|---|---|
| `pilot` | The first stage is enrolled |
| `rollout` | Later stages are being enrolled |
| `paused` | Paused manually or by a safety threshold |
| `active` | Every repository in scope is enrolled |
| `rolled_back` | A new version restoring the previous policy was published |

## RolloutView

| Field | Type | Description |
|---|---|---|
| `id` | string | 32 hexadecimal characters |
| `organization_id` | integer | |
| `target` | object | `{type, id, label}` |
| `from_version`, `to_version` | integer | |
| `state` | string | |
| `stages` | array | `{index, name, kind, percent, repositories, enrolled, state}`. `kind` is `repositories` or `percent`. `repositories` is the planned count for explicit stages and `0` for percent stages. `state` is `done`, `current` or `planned`. |
| `current_stage` | integer | |
| `scope_repositories` | integer | Repositories of the target |
| `enrolled`, `propagated` | integer | |
| `scanned`, `passed`, `blocked`, `errors` | integer | Scans of enrolled repositories since enrollment |
| `complete` | boolean | Active, every repository in scope enrolled, and the policy propagated to all of them |
| `thresholds` | object | `{max_error_rate, max_block_rate, min_scans}` (numbers) |
| `auto_pause`, `auto_rollback` | boolean | |
| `paused_reason` | string or null | |
| `created_by` | string | |
| `created_at`, `stage_started_at` | string (date-time) | |
| `completed_at`, `rolled_back_at` | string (date-time) or null | |
| `rollback_version` | integer or null | |
| `can_manage` | boolean | Whether the caller has `policies:publish` |

**Automatic pause.** A background task checks each rollout in `pilot` or
`rollout` that has `auto_pause` on, once it has at least `min_scans` scans.
It pauses the rollout when the error rate is above `max_error_rate`, or the
block rate is above `max_block_rate`. If `auto_rollback` is also on, it then
rolls the rollout back.

## GET /api/v1/organizations/{organization_id}/rollouts

| Query parameter | Description |
|---|---|
| `active` | `true` returns only `pilot`, `rollout` and `paused` rollouts; any other value returns all |

Returns the 100 newest rollouts. The list is not paginated.

Response `200`. `data` is an array of `RolloutView`.

## GET /api/v1/rollouts/{rollout_id}

Response `200`. `data` is a `RolloutView`.

Errors: `404 NOT_FOUND`.

## POST /api/v1/rollouts/{rollout_id}/advance

Enrolls the next stage. When no stage remains, the rollout becomes `active` and
every remaining repository in scope is enrolled. This endpoint takes no body.

Errors: `409 CONFLICT` ("A <state> rollout cannot be expanded.") unless the
state is `pilot` or `rollout`.

Response `200`. `data` is the updated `RolloutView`.

Side effect: a `policy_rollout_advanced` or `policy_rollout_completed` audit event.

## POST /api/v1/rollouts/{rollout_id}/pause

| Body field | Type | Required | Description |
|---|---|---|---|
| `reason` | string | yes | At most 500 characters |

Errors: `409 CONFLICT` unless the state is `pilot` or `rollout` (checked before
`reason`), or when the rollout changed concurrently; `400 VALIDATION_ERROR`
(`field` `reason`).

Response `200`. `data` is the updated `RolloutView`, now `paused`.

Side effects: a `policy_rollout_paused` audit event. A manual pause also sends a
`policy_rollout_failed` notification.

## POST /api/v1/rollouts/{rollout_id}/resume

Returns a paused rollout to the state it was paused from. This endpoint takes
no body.

Errors: `409 CONFLICT` ("The rollout is <state>, not paused.").

Response `200`. `data` is the updated `RolloutView`.

Side effect: a `policy_rollout_resumed` audit event.

## POST /api/v1/rollouts/{rollout_id}/rollback

Publishes a new version that restores `from_version` and ends the rollout.

| Body field | Type | Required | Description |
|---|---|---|---|
| `reason` | string | yes | At most 500 characters |
| `confirm` | boolean | yes | Must be `true` |

Checks, in this order:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not a member / lacks `policies:rollback` |
| 409 | `CONFLICT` | state is not `pilot`, `rollout` or `paused`. An `active` rollout cannot be rolled back here; use [policy target rollback](policies.md#post-apiv1organizationsorganization_idpolicy-targetstarget_typerollback). |
| 400 | `VALIDATION_ERROR` (`field` `reason`) | missing or invalid reason |
| 409 | `CONFLICT` | the rollout started from the first version; there is nothing to restore |
| 400 | `VALIDATION_ERROR` (`field` `target_version`) | the restored document is identical to the current one |
| 409 | `CONFIRMATION_REQUIRED` | `confirm` is not `true` |
| 401 | `REAUTHENTICATION_REQUIRED` | the rollback weakens enforcement and the last sign-in was more than 15 minutes ago |

Response `200`. `data` is the updated `RolloutView`, now `rolled_back`, with
`rollback_version` set.

Side effects:

- An `organization_policy_rolled_back` audit event (organization target) or a
  `policy_rolled_back` audit event (group target).
- A `policy_rollout_rolled_back` audit event.
- A `policy_rolled_back` notification.
