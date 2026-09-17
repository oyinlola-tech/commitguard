# Policy simulations

A simulation estimates what a policy draft would change by re-evaluating stored
findings from recent scans against the draft's rules. It changes nothing. It
runs asynchronously in the service's maintenance loop. For the method and its
limits, see [../policy-simulation.md](../policy-simulation.md).

Source: `src/commitguard/governance/simulation.py`; handlers in
`src/commitguard/api/governance.py`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| [`POST /api/v1/policy-drafts/{draft_id}/simulations`](#post-apiv1policy-draftsdraft_idsimulations) | `policies:write` | `write` |
| [`GET /api/v1/organizations/{organization_id}/simulations`](#get-apiv1organizationsorganization_idsimulations) | `policies:read` | `read` |
| [`GET /api/v1/simulations/{simulation_id}`](#get-apiv1simulationssimulation_id) | `policies:read` | `read` |

A non-member gets `404 NOT_FOUND`, and a member without the permission gets
`403 FORBIDDEN`.

## SimulationView

| Field | Type | Description |
|---|---|---|
| `id` | string | 32 hexadecimal characters |
| `organization_id` | integer | |
| `target` | object | `{type, id, label}`; `label` is `(removed)` when the target no longer exists |
| `draft_id` | string or null | |
| `current_version` | integer | Target's version when requested; `0` if none |
| `state` | string | `queued`, `running`, `completed` or `failed` |
| `parameters` | object | `{period_days, repository_ids}` |
| `requested_by` | string | |
| `requested_at` | string (date-time) | |
| `started_at`, `completed_at` | string (date-time) or null | |
| `error` | string or null | For example `simulation failed (<ExceptionType>)` |
| `result` | object or null | Set when `completed`; see below |

`result`:

| Field | Type |
|---|---|
| `repositories_analyzed`, `repositories_without_data`, `scans_analyzed`, `findings_analyzed` | integer |
| `new_blocks`, `new_warnings`, `no_longer_blocked`, `unchanged` | integer |
| `scans_newly_blocked`, `scans_no_longer_blocked`, `scans_assumed_defaults` | integer |
| `most_affected` | array of up to 10 `{repository_id, full_name, scans, new_blocks, new_warnings, no_longer_blocked}`; `full_name` is `null` for repositories not listed for the session |
| `truncated` | boolean: the analysis stopped at 5,000 scans or 50,000 findings |
| `disclaimer` | string: states that the result is an estimate |

## POST /api/v1/policy-drafts/{draft_id}/simulations

Queues a simulation of the draft's current `floors` and `defaults`. The document
is copied when the request is made. The draft's state is not checked.

| Body field | Type | Required | Description |
|---|---|---|---|
| `period_days` | integer | no | 1 to 90, default 30 |
| `repository_ids` | array of integers | no | 1 to 5,000 repository IDs that narrow the target's repositories |

Errors:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | draft not accessible (see [policy-drafts.md](policy-drafts.md#access-rules)); lacks `policies:write` |
| 400 | `VALIDATION_ERROR` | invalid `repository_ids` or `period_days` |
| 409 | `CONFLICT` | "There are already simulations running for this organization. Wait for them to finish." (3 queued or running) |

Response `202`. `data` is a `SimulationView` with `state: "queued"` and
`result: null`. Poll `GET /api/v1/simulations/{simulation_id}` for the result.

Side effect: a `policy_simulated` audit event when the simulation completes.

## GET /api/v1/organizations/{organization_id}/simulations

| Query parameter | Description |
|---|---|
| `draft` | Only simulations of this draft ID. The value is not validated; an unknown ID gives an empty list. |

Returns the 50 most recently requested simulations. The list is not paginated.

Response `200`. `data` is an array of `SimulationView`.

## GET /api/v1/simulations/{simulation_id}

Response `200`. `data` is a `SimulationView`.

Errors: `404 NOT_FOUND`.
