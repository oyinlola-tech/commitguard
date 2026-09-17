# Organizations, settings and members

An organization is a GitHub account (organization or user) with the GitHub App
installed, identified by its numeric account ID. These endpoints list the
organizations the caller can access, and read and change organization security
settings and member roles. For the model, see
[../organization-governance.md](../organization-governance.md).

Source: handlers in `src/commitguard/api/app.py` and
`src/commitguard/api/governance.py`; `src/commitguard/controlplane/members.py`;
`src/commitguard/governance/settings.py`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| [`GET /api/v1/organizations`](#get-apiv1organizations) | session | `read` |
| [`GET /api/v1/organizations/{organization_id}`](#get-apiv1organizationsorganization_id) | `organization:read`, `security:read` | `read` |
| [`GET /api/v1/organizations/{organization_id}/settings`](#get-apiv1organizationsorganization_idsettings) | `organization:read` | `read` |
| [`PUT /api/v1/organizations/{organization_id}/settings`](#put-apiv1organizationsorganization_idsettings) | `organization:manage` | `sensitive` |
| [`GET /api/v1/organizations/{organization_id}/members`](#get-apiv1organizationsorganization_idmembers) | `members:read` | `read` |
| [`PUT /api/v1/organizations/{organization_id}/members/{user_id}`](#put-apiv1organizationsorganization_idmembersuser_id) | `members:manage` | `write` |
| [`DELETE /api/v1/organizations/{organization_id}/members/{user_id}`](#delete-apiv1organizationsorganization_idmembersuser_id) | `members:manage` | `write` |

Notification settings for an organization are described in
[notifications.md](notifications.md). Security posture, search and reports are
described in [security-posture.md](security-posture.md) and [compliance-exports.md](compliance-exports.md).

For every `/organizations/{organization_id}/...` endpoint, a caller without a
role in the organization gets `404 NOT_FOUND`. A member whose role lacks the
permission gets `403 FORBIDDEN`. A role only counts when GitHub listed the
organization's installation for the caller at sign-in; see
[authorization.md](authorization.md).

## GET /api/v1/organizations

Lists the organizations the caller can access, sorted by login. The endpoint
checks no permission and is not paginated.

Response `200`. `data` is an array of `OrganizationView`:

| Field | Type | Description |
|---|---|---|
| `organization` | object | `{id, login, type}`; `type` is the GitHub account type, `Organization` or `User` |
| `role` | string | `viewer`, `security_manager`, `admin` or `owner` |
| `implicit_role` | boolean | `true` for the owner of a personal (user-account) installation |
| `permissions` | array of strings | Permissions of the role, sorted |
| `installation_ids` | array of integers | Installations of this account that GitHub listed for the session |

```json
{
  "data": [
    {
      "organization": { "id": 5001, "login": "example-org", "type": "Organization" },
      "role": "security_manager",
      "implicit_role": false,
      "permissions": [
        "audit:read", "exceptions:create", "exceptions:read", "notifications:read",
        "organization:read", "policies:read", "repositories:read", "rules:read",
        "scans:read", "scans:trigger", "security:read", "violations:manage", "violations:read"
      ],
      "installation_ids": [42]
    }
  ],
  "meta": {}
}
```

## GET /api/v1/organizations/{organization_id}

The organization's posture summary and settings in one response.

Response `200`. `data` is `{organization, settings}`: an
[`OrganizationPostureView`](security-posture.md#get-apiv1organizationsorganization_idsecurityoverview)
and a [`SettingsView`](#settingsview).

## SettingsView

| Field | Type | Description |
|---|---|---|
| `organization_id` | integer | |
| `version` | integer | `0` while the defaults have never been saved |
| `settings` | object | See [Settings](#settings) |
| `updated_at` | string (date-time) or null | |
| `updated_by` | string or null | |
| `can_manage` | boolean | Whether the caller has `organization:manage` |

If a stored settings document no longer validates, the defaults are returned
with the stored `version`.

### Settings

| Key | Type and allowed values | Default |
|---|---|---|
| `security_baseline` | object: rule ID → `warn` or `block` | `{}` |
| `require_policy_approval` | boolean | `false` |
| `require_separate_approver` | boolean | `true` |
| `exception_approval_min_severity` | `info`, `low`, `medium`, `high`, `critical` | `high` |
| `exception_max_days` | integer, 1 to 365 | `90` |
| `allow_permanent_exceptions` | boolean | `false` |
| `exception_warning_days` | array of up to 5 integers, each 1 to 90; de-duplicated and sorted descending | `[7, 3, 1]` |
| `default_onboarding_mode` | `enforce` or `monitor` | `enforce` |
| `auto_onboard_new_repositories` | boolean | `true` |
| `archived_repositories` | `keep` or `exclude` | `keep` |
| `rollout_auto_pause` | boolean | `true` |
| `rollout_max_error_rate` | number, 0 to 1 | `0.2` |
| `rollout_max_block_rate` | number, 0 to 1 | `0.5` |
| `rollout_min_scans` | integer, 1 to 10,000 | `5` |
| `rollout_auto_rollback` | boolean | `false` |
| `aggregate_violation_alerts` | boolean | `false` |
| `timezone` | IANA time zone, at most 64 characters | `UTC` |

## GET /api/v1/organizations/{organization_id}/settings

Response `200`. `data` is a `SettingsView`.

## PUT /api/v1/organizations/{organization_id}/settings

Changes some settings. The keys in `settings` are merged over the current
settings, and unknown keys are rejected.

| Body field | Type | Required | Description |
|---|---|---|---|
| `expected_version` | integer ≥ 0 | yes | The `version` you read |
| `settings` | object | yes | Non-empty; the keys to change |
| `reason` | string | when relaxing a control | At most 500 characters |
| `confirm` | boolean | when relaxing a control | Default `false` |

These changes **relax** a control. Each one needs `confirm: true`, a `reason`, and
a sign-in within the last 15 minutes. The text in the second column is the entry
returned in `details.changes`:

| Change | `details.changes` entry |
|---|---|
| a baseline rule removed or lowered | `security baseline <rule>: <old> -> <new or removed>` |
| `require_policy_approval` turned off | `policy approval no longer required` |
| `require_separate_approver` turned off | `authors may approve their own policy changes` |
| `exception_approval_min_severity` raised | `fewer exceptions need approval (<old> -> <new>)` |
| `exception_max_days` increased | `longer exceptions allowed (<old> -> <new> days)` |
| `allow_permanent_exceptions` turned on | `permanent exceptions allowed` |
| `default_onboarding_mode` from `enforce` to `monitor` | `new repositories onboard in monitor mode (not blocking)` |
| `rollout_auto_pause` turned off | `staged rollouts no longer pause automatically` |
| `rollout_max_error_rate` increased | `higher rollout error threshold` |
| `rollout_max_block_rate` increased | `higher rollout block threshold` |

Checks, in this order:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not a member / lacks `organization:manage` |
| 400 | `VALIDATION_ERROR` | invalid `expected_version`, `settings` (not a non-empty object), `confirm` or `reason` |
| 409 | `CONFLICT` | "The settings were changed by someone else (now version N). Reload before saving." |
| 400 | `VALIDATION_ERROR` (`field` `settings.<key>`) | "Invalid setting <key>: <message>", including unknown keys |
| 400 | `VALIDATION_ERROR` | "The settings are identical to the current version." |
| 409 | `CONFIRMATION_REQUIRED` | relaxing change without `confirm: true`; `details.changes` lists each relaxed control |
| 401 | `REAUTHENTICATION_REQUIRED` | relaxing change and last sign-in more than 15 minutes ago |
| 400 | `VALIDATION_ERROR` (`field` `reason`) | "A reason is required when relaxing security controls." |

Response `200`. `data` is the new `SettingsView`, with `version` incremented.

Side effects:

- An `organization_settings_changed` audit event.
- An `organization_settings_changed` notification (`critical` when relaxing,
  otherwise `medium`).
- The effective policy of every repository in the organization is marked stale.

```json
{
  "error": {
    "code": "CONFIRMATION_REQUIRED",
    "message": "This change relaxes security controls and must be confirmed: policy approval no longer required",
    "request_id": "5b1f0c2e9d8a4b7c8e6f1a2b3c4d5e6f",
    "details": { "changes": ["policy approval no longer required"] }
  }
}
```

## GET /api/v1/organizations/{organization_id}/members

Lists members in the order they were granted. For a personal (user-account)
installation, the account owner is listed first as an implicit `owner`.

| Query parameter | Description |
|---|---|
| `cursor`, `limit` | Offset cursor; see [pagination.md](pagination.md) |

Response `200`. `data` is an array of `MemberView`; `meta` is `{next_cursor, limit}`.

| Field | Type | Description |
|---|---|---|
| `user_id` | integer | GitHub user ID |
| `login` | string or null | Known login, if any |
| `role` | string | |
| `granted_by` | string | Login of the granting user; `GitHub account owner` for the implicit owner |
| `created_at`, `updated_at` | string (date-time) | For the implicit owner, the Unix epoch |
| `implicit` | boolean | |

## PUT /api/v1/organizations/{organization_id}/members/{user_id}

Grants a role, or changes an existing one. `user_id` is the GitHub user ID.
Grants never use logins.

| Body field | Type | Required | Description |
|---|---|---|---|
| `role` | string | yes | `viewer`, `security_manager`, `admin` or `owner` |
| `login` | string | no | A valid GitHub login, stored if the user is not yet known |

Checks, in this order:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not a member / lacks `members:manage` |
| 400 | `VALIDATION_ERROR` | `role` missing or invalid; `login` not text or not a valid login; `user_id` out of range |
| 404 | `NOT_FOUND` | no installation is known for the account |
| 409 | `CONFLICT` | "The owner of a personal account is always its owner." |
| 403 | `FORBIDDEN` | "You cannot change your own role." |
| 409 | `CONFLICT` | "An organization must keep at least one owner." (demoting the last owner) |

Response `200`. `data` is the member's `MemberView`.

Side effect: a `member_role_granted` or `member_role_changed` audit event. The
member's next request uses the new role.

```bash
curl -s -X PUT https://commitguard.example.com/api/v1/organizations/5001/members/2002 \
  -H 'Cookie: __Host-commitguard_session=<session-token>' \
  -H 'Origin: https://commitguard.example.com' \
  -H 'X-CSRF-Token: <csrf-token>' \
  -H 'Content-Type: application/json' \
  -d '{"role": "security_manager", "login": "octo-analyst"}'
```

## DELETE /api/v1/organizations/{organization_id}/members/{user_id}

Removes a member's role.

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not a member / lacks `members:manage` |
| 403 | `FORBIDDEN` | "You cannot remove your own membership." |
| 404 | `NOT_FOUND` | the user has no explicit membership (the implicit owner cannot be removed) |
| 409 | `CONFLICT` | "An organization must keep at least one owner." |

Response `200`: `{"data": {"removed": true}, "meta": {}}`.

Side effects: a `member_removed` audit event. If the user has no membership left
in any organization, all their sessions are deleted.
