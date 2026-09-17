# Policy drafts

A policy draft is a proposed change to the policy of an organization, a
repository group or a repository. A draft is edited, optionally submitted
and approved, and then published as a new immutable policy version. It can be
published with a staged rollout. For the workflow, see
[../policy-management.md](../policy-management.md).

Source: `src/commitguard/governance/workflow.py`; handlers in
`src/commitguard/api/governance.py`; policy documents are validated by
`src/commitguard/controlplane/policies.py`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| [`GET /api/v1/organizations/{organization_id}/policy-drafts`](#get-apiv1organizationsorganization_idpolicy-drafts) | `policies:read` | `read` |
| [`POST /api/v1/organizations/{organization_id}/policy-drafts`](#post-apiv1organizationsorganization_idpolicy-drafts) | `policies:write` | `write` |
| [`GET /api/v1/policy-drafts/{draft_id}`](#get-apiv1policy-draftsdraft_id) | `policies:read` | `read` |
| [`PATCH /api/v1/policy-drafts/{draft_id}`](#patch-apiv1policy-draftsdraft_id) | `policies:write` | `write` |
| [`POST /api/v1/policy-drafts/{draft_id}/submit`](#post-apiv1policy-draftsdraft_idsubmit) | `policies:write` | `write` |
| [`POST /api/v1/policy-drafts/{draft_id}/approve`](#post-apiv1policy-draftsdraft_idapprove-and-reject) | `policies:approve` | `sensitive` |
| [`POST /api/v1/policy-drafts/{draft_id}/reject`](#post-apiv1policy-draftsdraft_idapprove-and-reject) | `policies:approve` | `write` |
| [`POST /api/v1/policy-drafts/{draft_id}/cancel`](#post-apiv1policy-draftsdraft_idcancel) | `policies:write` | `write` |
| [`POST /api/v1/policy-drafts/{draft_id}/publish`](#post-apiv1policy-draftsdraft_idpublish) | `policies:publish` | `sensitive` |
| [`POST /api/v1/policy-drafts/{draft_id}/emergency-publish`](#post-apiv1policy-draftsdraft_idemergency-publish) | `policies:emergency` | `sensitive` |

Simulating a draft is described in [simulations.md](simulations.md).

## Access rules

For `/policy-drafts/{draft_id}` routes, the organization comes from the stored
draft. The service checks, in this order:

1. The draft exists (`404`).
2. The caller has `policies:read` in its organization (`404` for non-members).
3. The target still exists, and a repository target is listed for the session
   (`404`).
4. The caller has the operation's permission (`403`).

Some handlers parse the body before these checks, so a malformed body can
answer `400` or `415` before `404` or `403`. This applies to publish,
emergency-publish, approve and reject.

## States

| State | Meaning |
|---|---|
| `draft` | Editable |
| `pending_approval` | Submitted; waiting for a decision |
| `approved` | Approved; can be published |
| `rejected` | Rejected; can be edited (back to `draft`) or cancelled |
| `cancelled` | Final |
| `published` | Final; `published_version` is set |

`draft`, `pending_approval` and `approved` are open states. An organization can
have at most 50 open drafts.

| Action | Allowed from |
|---|---|
| edit (`PATCH`) | `draft`, `approved`, `rejected`; the state returns to `draft` |
| submit | `draft` |
| approve, reject | `pending_approval` |
| cancel | any state except `published` and `cancelled` |
| publish | any state except `published`, `cancelled` and `rejected`. When the organization setting `require_policy_approval` is on, only `approved`. |
| emergency-publish | any state except `published`, `cancelled` and `rejected` |

An action from another state answers `409 CONFLICT`.

## Policy documents

A draft carries two maps from rule ID to action:

- `floors` (mandatory): `warn`, `block` or `null`.
- `defaults`: `allow`, `warn`, `block` or `null`.

`null` entries are dropped. The valid rule IDs are `ai_coauthor`, `ai_identity`,
`ai_trailer`, `malformed_trailer` and `bot_identity`. A rule may not appear in
both maps. At least one rule must be set, and the canonical document may be at
most 16,384 bytes.

| Message | `field` |
|---|---|
| "floors must be an object of policy IDs" / "unknown policy ID in floors" | `floors` |
| "the floor for X must be warn, block or null" | `floors.X` |
| "defaults must be an object of policy IDs" / "unknown policy ID in defaults" | `defaults` |
| "the default for X must be allow, warn, block or null" | `defaults.X` |
| "a rule is either mandatory or a default, not both: ..." | `defaults` |
| "a policy must set at least one rule" | `floors` |

## PolicyDraftView

| Field | Type | Description |
|---|---|---|
| `id` | string | 32 hexadecimal characters |
| `organization_id` | integer | |
| `target` | object | `{type, id, label}`. `type` is `organization`, `group` or `repository`. `id` is `""` for the organization. |
| `title` | string | |
| `reason` | string or null | |
| `state` | string | See [States](#states) |
| `revision` | integer | Starts at 1; use as `expected_revision` |
| `base_version` | integer | Target version the draft was based on |
| `current_version` | integer | Target's published version now |
| `floors`, `defaults` | object | Rule ID → action |
| `changes` | array | `{policy_id, old, new, weakening, enforcement}`; `enforcement` is `mandatory` or `default` |
| `diff` | object | `{from_version, to_version, added, changed, removed, weakening}`; each list holds `{policy_id, old, new, weakening, enforcement}` |
| `weakening` | boolean | Whether the change weakens enforcement |
| `rebase_required` | boolean | Not published and `current_version` ≠ `base_version` |
| `requires_approval` | boolean | The organization's `require_policy_approval` setting |
| `created_by` | string or null | |
| `created_at`, `updated_at` | string (date-time) | |
| `submitted_by` | string or null | |
| `submitted_at` | string (date-time) or null | |
| `published_version` | integer or null | |
| `published_at` | string (date-time) or null | |
| `published_by` | string or null | |
| `emergency` | boolean | |
| `rollout_id` | string or null | Rollout started at publication |
| `approvals` | array | Newest first: `{id, status, requested_by, requested_at, decided_by, decided_at, reason}`; `status` is `pending`, `approved`, `rejected` or `cancelled` |
| `can_edit`, `can_submit`, `can_approve`, `can_publish`, `can_cancel`, `can_emergency_publish` | boolean | What the caller may do now |

## GET /api/v1/organizations/{organization_id}/policy-drafts

| Query parameter | Values |
|---|---|
| `state` | one of the six states. An empty or unknown value answers `400` ("unknown draft state", `field` `state`). |

Returns up to 200 drafts, most recently updated first, then drops drafts whose
repository target is not listed for the session. The list is not paginated.

Response `200`. `data` is an array of `PolicyDraftView`.

## POST /api/v1/organizations/{organization_id}/policy-drafts

| Body field | Type | Required | Description |
|---|---|---|---|
| `target_type` | string | yes | `organization`, `group` or `repository` |
| `target_id` | string or integer | for `group` and `repository` | Omitted, `null` or `""` for `organization`. A group ID for `group`. A repository ID (integer or digit string) for `repository`. |
| `floors` | object | see [Policy documents](#policy-documents) | |
| `defaults` | object | see [Policy documents](#policy-documents) | |
| `title` | string | no | At most 100 characters; default `<target label> policy change` |
| `reason` | string | no | At most 500 characters |

Errors:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not a member / lacks `policies:write` |
| 400 | `VALIDATION_ERROR` | invalid target or document, `title` or `reason` (`field` names it) |
| 404 | `NOT_FOUND` | the group or repository is not in the organization, or the repository is not listed for the session |
| 409 | `CONFLICT` | "There are already 50 open policy drafts. Publish or cancel some before creating another." |

The draft's `base_version` is the target's current version. A draft identical
to the current policy is accepted. Publishing it fails.

Response `201`. `data` is a `PolicyDraftView`.

Side effect: a `policy_draft_created` audit event.

```json
{
  "target_type": "organization",
  "title": "Block bot identities",
  "floors": { "bot_identity": "block" },
  "defaults": {},
  "reason": "Bots must not author production commits"
}
```

## GET /api/v1/policy-drafts/{draft_id}

Response `200`. `data` is a `PolicyDraftView`.

## PATCH /api/v1/policy-drafts/{draft_id}

| Body field | Type | Required | Description |
|---|---|---|---|
| `expected_revision` | integer | yes | The `revision` you read |
| `floors`, `defaults` | object | no | **If either key is present, both maps are replaced.** A missing map becomes empty, so send both. |
| `title` | string | no | A blank or missing title keeps the stored one |
| `reason` | string | no | A missing key keeps the stored reason; `""` clears it |
| `rebase` | boolean | no | `true` sets `base_version` to the target's current version |

Checks, in this order: access and `policies:write`; document validation;
`409 CONFLICT` ("A <state> draft cannot be edited."); `400` for
`expected_revision` ("expected_revision must be an integer"); `409 CONFLICT`
("The draft was changed by someone else. Reload before editing.") when the
revision is not current.

Effects: the state becomes `draft` and `revision` increments. Pending and
approved approvals are cancelled ("draft edited after approval").

Response `200`. `data` is the updated `PolicyDraftView`.

Side effect: a `policy_draft_updated` audit event.

## POST /api/v1/policy-drafts/{draft_id}/submit

Requests approval. This endpoint takes no body. It is allowed only from `draft`,
whether or not the organization requires approval.

Effects: the state becomes `pending_approval`. A pending approval is recorded
for the draft's current content.

Response `200`. `data` is the updated `PolicyDraftView`.

Side effects: a `policy_approval_requested` audit event and a
`policy_approval_requested` notification.

## POST /api/v1/policy-drafts/{draft_id}/approve and /reject

| Body field | Type | Required | Description |
|---|---|---|---|
| `reason` | string | for `reject` | At most 500 characters. Optional for `approve`. |

Checks, in this order:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | access rules; lacks `policies:approve` |
| 409 | `CONFLICT` | not `pending_approval` |
| 403 | `FORBIDDEN` | "Separation of duties: the author of a policy change cannot approve it." Applies when the organization setting `require_separate_approver` is on (the default) and the caller created or submitted the draft. It applies to **reject** as well as approve. |
| 400 | `VALIDATION_ERROR` (`field` `reason`) | missing (reject) or invalid reason |
| 409 | `CONFLICT` | no pending approval request; or "The draft changed after it was submitted; submit it again." |

Response `200`. `data` is the updated `PolicyDraftView`, now `approved` or `rejected`.

Side effect: a `policy_approved` or `policy_rejected` audit event.

## POST /api/v1/policy-drafts/{draft_id}/cancel

This endpoint takes no body. Any holder of `policies:write` can cancel any open
or rejected draft. Pending approvals are cancelled.

Errors: `409 CONFLICT` ("The draft is <state>.") for published or cancelled drafts.

Response `200`. `data` is the updated `PolicyDraftView`, now `cancelled`.

Side effect: a `policy_draft_cancelled` audit event.

## POST /api/v1/policy-drafts/{draft_id}/publish

Publishes the draft as a new immutable version of its target.

| Body field | Type | Required | Description |
|---|---|---|---|
| `confirm_weakening` | boolean | when the change weakens enforcement | Default `false` |
| `reason` | string | when the change weakens enforcement | At most 500 characters; falls back to the draft's `reason` |
| `rollout` | object | no | Start a staged rollout; not allowed for repository targets. See below. |

`rollout` object:

| Field | Type | Description |
|---|---|---|
| `stages` | array | Required. 1 to 10 stages. Each stage has an optional `name` and **either** `repositories` (1 to 5,000 repository IDs) **or** `percent` (1 to 100; percentages must not decrease). If the last stage is below 100 %, an "All repositories" stage at 100 % is added. |
| `thresholds` | object | Optional `max_error_rate` (0 to 1), `max_block_rate` (0 to 1), `min_scans` (≥ 0). Omitted values use the organization settings (`rollout_max_error_rate` 0.2, `rollout_max_block_rate` 0.5, `rollout_min_scans` 5). |
| `auto_pause` | boolean | Default: organization setting `rollout_auto_pause` (`true`) |
| `auto_rollback` | boolean | Default: organization setting `rollout_auto_rollback` (`false`) |

Checks, in this order:

| Status | Code | When |
|---|---|---|
| 400 | `VALIDATION_ERROR` | `confirm_weakening` not a boolean; invalid `rollout`. Validation errors for stages use `field` values such as `stages` or `stages.0.percent`. Threshold errors use the bare key, for example `max_error_rate`. These checks run before the access rules. |
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | access rules; lacks `policies:publish` |
| 409 | `CONFLICT` | "A <state> draft cannot be published." |
| 409 | `APPROVAL_REQUIRED` | the organization requires approval and the draft is not `approved` |
| 409 | `CONFLICT` | the target changed since `base_version` ("The policy was changed by someone else (now version N). ..."); edit with `rebase: true` |
| 400 | `VALIDATION_ERROR` | "The new policy is identical to the current version." |
| 409 | `CONFIRMATION_REQUIRED` | weakening change without `confirm_weakening: true` |
| 401 | `REAUTHENTICATION_REQUIRED` | weakening change and last sign-in more than 15 minutes ago |
| 400 | `VALIDATION_ERROR` (`field` `reason`) | weakening change without a reason |
| 400 | `VALIDATION_ERROR` (`field` `rollout`) | rollout requested for a repository target; nothing is published |

When `require_policy_approval` is off, a draft in `pending_approval` can also be
published, although its `can_publish` is `false`.

Response `200`. `data` is the updated `PolicyDraftView`, now `published`, with
`rollout_id` set if a rollout started.

Side effects:

- An `organization_policy_changed` audit event (organization target) or a
  `policy_published` audit event (group or repository target).
- A `policy_changed` notification (`critical` when weakening, otherwise `high`).
- Effective policies are recomputed.
- With a rollout, a `policy_rollout_started` audit event. See [rollouts.md](rollouts.md).

```json
{
  "confirm_weakening": false,
  "rollout": {
    "stages": [
      { "name": "Pilot", "repositories": [7001, 7002] },
      { "name": "Half", "percent": 50 }
    ],
    "thresholds": { "max_block_rate": 0.3 }
  }
}
```

## POST /api/v1/policy-drafts/{draft_id}/emergency-publish

Publishes without approval. It requires `policies:emergency`, which only owners
hold.

| Body field | Type | Required | Description |
|---|---|---|---|
| `reason` | string | yes | At most 500 characters. The draft's stored reason is not used. |
| `confirm_weakening` | boolean | when weakening | |

The endpoint does not read `rollout`. It skips the approval requirement and
separation of duties. The target-version conflict, the identical-document check,
`CONFIRMATION_REQUIRED` and `REAUTHENTICATION_REQUIRED` apply as for publish.
It is allowed from `draft`, `pending_approval` and `approved`.

Response `200`. `data` is the updated `PolicyDraftView`, with `emergency: true`.

Side effects: everything publish does, plus a `policy_emergency_published` audit
event and a `policy_emergency_published` notification (`critical`).
