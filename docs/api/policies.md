# Policies

Policy endpoints read and change the policy of an organization, and read the
policies of groups and repositories. They also list version history, compare
versions, roll back, and show the effective policy of a repository.

There are two families of endpoints:

- `/api/v1/policies/...`: the **organization** policy, saved directly with
  `PUT`. This is refused with `409 APPROVAL_REQUIRED` when the organization
  requires approval.
- `/api/v1/organizations/{organization_id}/policies` and `.../policy-targets/...`:
  organization, group and repository **targets**. They are changed through
  [policy drafts](policy-drafts.md).

For the model, see [../policy-management.md](../policy-management.md) and
[../policy-inheritance.md](../policy-inheritance.md).

Source: `src/commitguard/controlplane/policies.py`,
`src/commitguard/governance/resolver.py`; handlers in
`src/commitguard/api/app.py` and `src/commitguard/api/governance.py`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| [`GET /api/v1/policies`](#get-apiv1policies) | `policies:read` | `read` |
| [`GET /api/v1/policies/{organization_id}`](#get-apiv1policiesorganization_id) | `policies:read` | `read` |
| [`PUT /api/v1/policies/{organization_id}`](#put-apiv1policiesorganization_id) | `policies:write` | `write` |
| [`POST /api/v1/policies/{organization_id}/preview`](#post-apiv1policiesorganization_idpreview) | `policies:read` | `read` |
| [`GET /api/v1/policies/{organization_id}/versions`](#get-apiv1policiesorganization_idversions) | `policies:read` | `read` |
| [`GET /api/v1/policies/{organization_id}/versions/{version}`](#get-apiv1policiesorganization_idversionsversion) | `policies:read` | `read` |
| [`GET /api/v1/policies/{organization_id}/diff`](#get-apiv1policiesorganization_iddiff) | `policies:read` | `read` |
| [`POST /api/v1/policies/{organization_id}/rollback`](#post-apiv1policiesorganization_idrollback) | `policies:rollback` | `sensitive` |
| [`GET /api/v1/organizations/{organization_id}/policies`](#get-apiv1organizationsorganization_idpolicies) | `policies:read` | `read` |
| [`GET /api/v1/organizations/{organization_id}/policy-targets/{target_type}/versions`](#get-apiv1organizationsorganization_idpolicy-targetstarget_typeversions) | `policies:read` | `read` |
| [`POST /api/v1/organizations/{organization_id}/policy-targets/{target_type}/rollback`](#post-apiv1organizationsorganization_idpolicy-targetstarget_typerollback) | `policies:rollback` | `sensitive` |
| [`GET /api/v1/repositories/{repository_id}/effective-policy`](#get-apiv1repositoriesrepository_ideffective-policy) | `policies:read` | `read` |
| [`GET /api/v1/organizations/{organization_id}/policy-propagation`](#get-apiv1organizationsorganization_idpolicy-propagation) | `policies:read` | `read` |

None of these routes declares a permission in the route table. The handler or
service checks the permission in the organization named by the path: a
non-member gets `404 NOT_FOUND`, and a member without the permission gets
`403 FORBIDDEN`. Only admins and owners hold `policies:write` and
`policies:rollback`.

## Policy documents

A policy has **floors** (mandatory minimum actions) and **defaults**:

- A floor is `warn` or `block`. In a request, `null` removes the floor.
- A default is `allow`, `warn` or `block`.
- Rule IDs are `ai_coauthor`, `ai_identity`, `ai_trailer`, `malformed_trailer`
  and `bot_identity`.
- Actions rank `allow` < `warn` < `block`.
- A change is **weakening** when it removes or lowers a floor, or lowers a default.

## Resource models

**OrganizationPolicyView**

| Field | Type | Description |
|---|---|---|
| `organization` | object | `{id, login, type}` |
| `version` | integer | `0` when no organization policy has been saved |
| `fingerprint` | string or null | |
| `updated_at` | string (date-time) or null | |
| `updated_by` | object or null | `{id, login}` |
| `reason` | string or null | |
| `service_policy` | string or null | |
| `rules` | array | One `PolicyRuleView` per rule, see below |
| `can_write` | boolean | Whether the caller has `policies:write` |

`PolicyRuleView` fields:

| Field | Type |
|---|---|
| `policy_id`, `name`, `description` | string |
| `default_action` | action |
| `service_floor`, `organization_floor`, `minimum_action` | action or null |
| `repository_override` | `any` or `stricter_only` |
| `source` | `built_in_default`, `service_policy`, `organization_policy` or `organization_default` |
| `organization_default` | action or null |

**ScopedPolicyView** (group or repository target): `organization`, `target`
(`{type, id, label}`), `version`, `fingerprint`, `updated_at`, `updated_by`,
`reason`, `rules` (`{policy_id, name, mandatory, default}`), `can_write`.

**PolicyChange**: `{policy_id, old, new, weakening, enforcement}`, where
`enforcement` is `mandatory` or `default`.

**PolicyVersionView**

| Field | Type | Description |
|---|---|---|
| `version` | integer | |
| `fingerprint` | string | |
| `floors`, `defaults` | object | Rule ID → action |
| `created_at` | string (date-time) | |
| `created_by` | object | `{id, login}` |
| `reason` | string or null | |
| `status` | string | `active` for the newest version, otherwise `archived` |
| `kind` | string | `change` or `rollback` |
| `rollback_of`, `restored_version` | integer or null | |
| `changes` | array | `PolicyChange` compared with the previous version |
| `summary` | string | |
| `draft_id` | string or null | |
| `emergency` | boolean | |

**PolicyDiffView**: `{from_version, to_version, added, changed, removed,
weakening}`. Each list holds `{policy_id, old, new, weakening, enforcement}`.

## GET /api/v1/policies

Lists the organization policy of every organization in which the caller has
`policies:read`, ordered by organization ID. It is not paginated.

| Query parameter | Description |
|---|---|
| `organization` | Only this organization. An organization without `policies:read` gives an empty list. |

Response `200`. `data` is an array of `OrganizationPolicyView`.

## GET /api/v1/policies/{organization_id}

Response `200`. `data` is an `OrganizationPolicyView`.

## PUT /api/v1/policies/{organization_id}

Saves a new organization policy version.

| Body field | Type | Required | Description |
|---|---|---|---|
| `expected_version` | integer ≥ 0 | yes | The `version` you read |
| `floors` | object | yes | Rule ID → `warn`, `block` or `null`; may be `{}` |
| `defaults` | object | no | Rule ID → `allow`, `warn`, `block` or `null`. Omitted or `null` keeps the current defaults. |
| `reason` | string | when weakening | At most 500 characters |
| `confirm_weakening` | boolean | when weakening | Default `false` |

Checks, in this order:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not a member / lacks `policies:write` |
| 400 | `VALIDATION_ERROR` | invalid `expected_version` or `reason` |
| 409 | `APPROVAL_REQUIRED` | the organization setting `require_policy_approval` is on; use a draft |
| 400 | `VALIDATION_ERROR` | invalid `floors` (`field` `floors` or `floors.<rule>`), `defaults` or `confirm_weakening` |
| 409 | `CONFLICT` | "The policy was changed by someone else (now version N). Reload to see the latest version before saving." |
| 400 | `VALIDATION_ERROR` | "The new policy is identical to the current version." |
| 409 | `CONFIRMATION_REQUIRED` | weakening change without `confirm_weakening: true` |
| 401 | `REAUTHENTICATION_REQUIRED` | weakening change and last sign-in more than 15 minutes ago |
| 400 | `VALIDATION_ERROR` | weakening change without `reason`; a rule in both `floors` and `defaults`; document larger than 16,384 bytes |

Response `200`. `data` is the new `OrganizationPolicyView`. `meta` is
`{"changes": [PolicyChange, ...]}`.

Side effects:

- An `organization_policy_changed` audit event.
- A `policy_changed` notification (`critical` when weakening, otherwise `high`).
- The effective policies of the organization's repositories are marked stale.

```bash
curl -s -X PUT https://commitguard.example.com/api/v1/policies/5001 \
  -H 'Cookie: __Host-commitguard_session=<session-token>' \
  -H 'Origin: https://commitguard.example.com' \
  -H 'X-CSRF-Token: <csrf-token>' \
  -H 'Content-Type: application/json' \
  -d '{"expected_version": 3, "floors": {"ai_coauthor": "block", "bot_identity": "block"}, "reason": "Q3 baseline"}'
```

## POST /api/v1/policies/{organization_id}/preview

Classifies a change to the organization **floors** without saving. `defaults`
is ignored. As a `POST`, it needs `Origin` and `X-CSRF-Token`.

| Body field | Type | Required |
|---|---|---|
| `floors` | object | yes |

Response `200`: `data` is `{version, changes, weakening}`, where `version` is the
current version and `changes` is an array of `PolicyChange`.

Errors: `404`, `403`, `400 VALIDATION_ERROR`.

## GET /api/v1/policies/{organization_id}/versions

Versions, newest first. Uses an offset cursor; see [pagination.md](pagination.md).

Response `200`. `data` is an array of `PolicyVersionView`; `meta` is
`{next_cursor, limit}`.

## GET /api/v1/policies/{organization_id}/versions/{version}

`version` is a positive integer (at most 9 digits). `0` does not match the route.

Response `200`. `data` is a `PolicyVersionView`.

Errors: `404 NOT_FOUND` for a version that does not exist.

## GET /api/v1/policies/{organization_id}/diff

| Query parameter | Required | Description |
|---|---|---|
| `from` | yes | Version number (digits, at most 9); `0` means "no organization policy" |
| `to` | yes | Version number, as for `from` |

`from` may be greater than `to`.

Response `200`. `data` is a `PolicyDiffView`.

Errors: `400 VALIDATION_ERROR` ("from must be a policy version number",
`field` `from` or `to`); `404 NOT_FOUND` when a version other than `0` does not
exist.

## POST /api/v1/policies/{organization_id}/rollback

Publishes a new version that restores an earlier version. The earlier
version is not changed. The organization's approval setting is not checked.

| Body field | Type | Required | Description |
|---|---|---|---|
| `target_version` | integer | yes | An earlier version: `1 ≤ target_version < current` |
| `expected_current_version` | integer | yes | The current version you read |
| `reason` | string | yes | At most 500 characters |
| `confirm` | boolean | yes | Must be `true` |

Checks, in this order:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not a member / lacks `policies:rollback` |
| 400 | `VALIDATION_ERROR` | invalid `target_version`, `expected_current_version`, `reason` or `confirm`; missing reason ("A reason is required to roll back policy.") |
| 409 | `CONFLICT` | `expected_current_version` is not current |
| 400 | `VALIDATION_ERROR` (`field` `target_version`) | not an earlier version; does not exist; identical to the active version |
| 422 | `POLICY_VERSION_INVALID` | the stored target version fails its integrity check |
| 409 | `CONFIRMATION_REQUIRED` | `confirm` is not `true` |
| 401 | `REAUTHENTICATION_REQUIRED` | the rollback weakens enforcement and the last sign-in was more than 15 minutes ago |

Response `200`. `data` is the `OrganizationPolicyView`. `meta` is:

```json
{
  "rollback": {
    "new_version": 5,
    "restored_version": 2,
    "rollback_of": 4,
    "diff": { "from_version": 4, "to_version": 2, "added": [], "changed": [], "removed": [], "weakening": false }
  }
}
```

In `diff`, `from_version` is the version that was active and `to_version` is the
restored version.

Side effects: an `organization_policy_rolled_back` audit event, a
`policy_rolled_back` notification, and effective policies marked stale.

## GET /api/v1/organizations/{organization_id}/policies

The policy of every target in the organization. It is not paginated.

Response `200`. `data` is:

| Field | Type | Description |
|---|---|---|
| `organization` | object | `OrganizationPolicyView` |
| `groups` | array | `ScopedPolicyView` for each active group, by name (up to 1,000) |
| `repositories` | array | `ScopedPolicyView` for each repository that has its own policy and is listed for the session; order not guaranteed |

## GET /api/v1/organizations/{organization_id}/policy-targets/{target_type}/versions

| Parameter | In | Description |
|---|---|---|
| `target_type` | path | `organization`, `group` or `repository` |
| `target_id` | query | Omitted or empty for `organization`. A group ID (32 hexadecimal characters) for `group`. A repository ID for `repository`. |
| `cursor`, `limit` | query | Offset cursor; see [pagination.md](pagination.md) |

The permission is checked before the target is parsed. A malformed target
answers `400 VALIDATION_ERROR` (`field` `target_type` or `target_id`). An archived
or unknown group, a repository not known to the organization, and a repository
not listed for the session each answer `404 NOT_FOUND`.

Response `200`. `data` is `{policy, versions}`. `policy` is an
`OrganizationPolicyView` for the organization, otherwise a `ScopedPolicyView`.
`versions` is an array of `PolicyVersionView`. `meta` is `{next_cursor, limit}`.

## POST /api/v1/organizations/{organization_id}/policy-targets/{target_type}/rollback

Rolls one target back. The target is identified by the path and the
**`target_id` query parameter**, as for the versions endpoint.

| Body field | Type | Required | Description |
|---|---|---|---|
| `target_version` | integer | yes | |
| `expected_current_version` | integer | yes | |
| `reason` | string | yes | A non-string is treated as missing; longer text is truncated to 500 characters |
| `confirm` | boolean | yes | Must be `true` |

The permission is `policies:rollback`. Errors are the same as for
[`POST /api/v1/policies/{organization_id}/rollback`](#post-apiv1policiesorganization_idrollback),
plus the target errors above.

Response `200`. `data` is `{version, restored_version, diff}`. `version` is the
new version, and `diff` is a `PolicyDiffView`. `meta` is `{}`.

Side effects: an `organization_policy_rolled_back` audit event (organization
target) or a `policy_rolled_back` audit event (group or repository target); a
`policy_rolled_back` notification; the affected effective policies are marked
stale.

```bash
curl -s -X POST 'https://commitguard.example.com/api/v1/organizations/5001/policy-targets/group/rollback?target_id=9a1b2c3d4e5f60718293a4b5c6d7e8f9' \
  -H 'Cookie: __Host-commitguard_session=<session-token>' \
  -H 'Origin: https://commitguard.example.com' \
  -H 'X-CSRF-Token: <csrf-token>' \
  -H 'Content-Type: application/json' \
  -d '{"target_version": 1, "expected_current_version": 2, "reason": "Pilot regression", "confirm": true}'
```

## GET /api/v1/repositories/{repository_id}/effective-policy

The policy that applies to one repository, with the source of every rule.

The repository must be listed for the session in an organization where the
caller has `repositories:read`, and the caller needs `policies:read` there.
Otherwise the answer is `404 NOT_FOUND`.

Response `200`. `data` is an `EffectivePolicyView`:

| Field | Type | Description |
|---|---|---|
| `organization_id`, `repository_id` | integer | |
| `full_name` | string | |
| `mode` | string | `enforce` or `monitor` |
| `effective` | object | `{policies, rules, inputs_fingerprint, description, mode}`. `policies` holds `{id, enabled, action, description}`. `rules` holds provenance entries, see below. |
| `versions` | object | `{organization_policy, settings, groups, repository_policy, rollouts, exceptions, organization_rules}` |
| `propagation` | string | `up_to_date`, `stale`, `syncing`, `error` or `pending` |
| `resolved_at` | string (date-time) | |
| `last_scan_id` | string or null | |
| `last_scan_completed_at` | string (date-time) or null | |
| `last_scan_effective` | object or null | The effective policy used by the last scan |
| `last_scan_used_current_policy` | boolean or null | |

Each `effective.rules` entry has these fields:

- `policy_id`, `enabled`, `action`
- `source`: `built_in`, `service`, `organization`, `group`, `repository_policy`,
  `repository_configuration`, `exception` or `monitor_mode`
- `source_label`, `enforcement`, `required_action`, `required_by`,
  `required_label`
- `conflict`: an object or `null`. The object has `policy_id`,
  `requested_action`, `requested_enabled`, `requested_by`, `requested_label`,
  `required_action`, `required_by`, `required_label`, `effective_action` and
  `reason`.
- `exception_id`, `exception_expires_at`, `action_before_exception`
- `monitor_mode`, `repository_configuration_known`

`meta` is `{"exceptions": {"active": <integer>, "expiring_soon": <integer>}}`.
Expiring soon means within 7 days.

If the stored effective policy is not current, the request resolves it again and
stores it.

## GET /api/v1/organizations/{organization_id}/policy-propagation

Response `200`. `data` is a `PropagationStatus`:

| Field | Type | Description |
|---|---|---|
| `organization_id` | integer | |
| `repositories` | integer | All repositories of the organization |
| `up_to_date`, `stale`, `syncing`, `error`, `pending` | integer | Counts; an `up_to_date` entry past its validity is counted as `stale` |
| `complete` | boolean | `up_to_date` equals `repositories` |
| `failing` | array | Up to 50 `{repository_id, full_name, error}` for repositories listed for the session |
| `checked_at` | string (date-time) | |
