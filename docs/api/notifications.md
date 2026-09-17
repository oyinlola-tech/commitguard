# Notifications

In-app notifications for the signed-in user, personal in-app preferences, and
organization notification settings: e-mail recipients, webhook endpoints and
delivery records. For what each notification type means and how deliveries are
retried and signed, see [../notifications.md](../notifications.md).

Source: `src/commitguard/controlplane/notifications.py`,
`src/commitguard/notifications/`, handlers in `src/commitguard/api/app.py`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| [`GET /api/v1/notifications`](#get-apiv1notifications) | per notification type | `read` |
| [`GET /api/v1/notifications/counts`](#get-apiv1notificationscounts) | per notification type | `read` |
| [`POST /api/v1/notifications/read-all`](#post-apiv1notificationsread-all) | per notification type | `write` |
| [`GET /api/v1/notifications/{notification_id}`](#get-apiv1notificationsnotification_id) | per notification type | `read` |
| [`POST /api/v1/notifications/{notification_id}/read`](#post-apiv1notificationsnotification_idread-unread-archive) | per notification type | `write` |
| [`POST /api/v1/notifications/{notification_id}/unread`](#post-apiv1notificationsnotification_idread-unread-archive) | per notification type | `write` |
| [`POST /api/v1/notifications/{notification_id}/archive`](#post-apiv1notificationsnotification_idread-unread-archive) | per notification type | `write` |
| [`GET /api/v1/notification-preferences`](#get-apiv1notification-preferences) | membership | `read` |
| [`PATCH /api/v1/notification-preferences`](#patch-apiv1notification-preferences) | `notifications:read` | `write` |
| [`GET /api/v1/organizations/{organization_id}/notification-settings`](#get-apiv1organizationsorganization_idnotification-settings) | `notifications:read` | `read` |
| [`PUT /api/v1/organizations/{organization_id}/notification-settings`](#put-apiv1organizationsorganization_idnotification-settings) | `notifications:manage` | `sensitive` |
| [`POST /api/v1/organizations/{organization_id}/notification-webhooks`](#post-apiv1organizationsorganization_idnotification-webhooks) | `notifications:manage` | `sensitive` |
| [`DELETE /api/v1/organizations/{organization_id}/notification-webhooks/{endpoint_id}`](#delete-apiv1organizationsorganization_idnotification-webhooksendpoint_id) | `notifications:manage` | `sensitive` |
| [`GET /api/v1/organizations/{organization_id}/notification-deliveries`](#get-apiv1organizationsorganization_idnotification-deliveries) | `notifications:manage` | `read` |

## Access rules

None of these routes declares a permission in the route table. The notification
service performs every check:

- **Organization endpoints.** The caller must have a role in the organization,
  reached through an installation GitHub listed at sign-in. Otherwise the answer
  is `404 NOT_FOUND`. A member whose role lacks the permission gets
  `403 FORBIDDEN`. Every role holds `notifications:read`; only `admin` and
  `owner` hold `notifications:manage`.
- **Inbox endpoints.** Each notification belongs to one user and is checked
  again on every read and state change. It is visible only when all of these
  hold:
  - it is in the caller's inbox;
  - the caller's current role grants the permission of the notification's type
    (see [Notification types](#notification-types));
  - its installation is one GitHub listed for this session;
  - its repository, if any, is one GitHub listed for this session.

  Anything else is left out of lists, and fetching or changing it directly
  answers `404 NOT_FOUND`. For example, losing GitHub access to a repository
  hides that repository's notifications at the next sign-in.

## Resource models

**NotificationView**

| Field | Type | Description |
|---|---|---|
| `id` | string | 32 hexadecimal characters |
| `type` | string | A [notification type](#notification-types) |
| `category` | string | `violations`, `policy`, `github` or `scans` |
| `severity` | string | `info`, `low`, `medium`, `high` or `critical` |
| `state` | string | `unread`, `read` or `archived` |
| `title` | string | |
| `body` | string | |
| `organization_id` | integer | |
| `repository` | object or null | `{id, installation_id, full_name}` |
| `resource_type` | string | The kind of resource the notification concerns |
| `resource_id` | string | |
| `link` | string or null | Dashboard path, for example `/violations/<id>`; `null` when there is no page for the resource |
| `occurrences` | integer | Number of deduplicated occurrences |
| `created_at` | string (date-time) | |
| `last_occurred_at` | string (date-time) | |
| `read_at` | string (date-time) or null | |

**NotificationCounts**: `unread` (integer), `unread_critical` (integer),
`capped` (boolean). Both counts stop at 1,000. `capped` is `true` when `unread`
reached that limit.

**OrganizationNotificationSettingsView**

| Field | Type | Description |
|---|---|---|
| `organization` | object | `{id, login, type}` |
| `version` | integer | `0` while the organization uses the built-in defaults |
| `updated_at` | string (date-time) or null | |
| `updated_by` | string or null | |
| `channels` | object | `{in_app, email, webhook, mode}`. `in_app` is always `true`. `email` and `webhook` say whether the server is configured for them. `mode` is `off`, `deliver` or `test`. |
| `types` | array | One entry per notification type; see below |
| `email_recipients` | array of strings | Empty unless the caller has `notifications:manage` |
| `webhooks` | array | `WebhookEndpointView` items; empty unless the caller has `notifications:manage` |
| `can_manage` | boolean | Whether the caller has `notifications:manage` |

Each `types` entry has these fields:

| Field | Type | Description |
|---|---|---|
| `type` | string | |
| `label` | string | |
| `description` | string | |
| `category` | string | |
| `mandatory_in_app` | boolean | |
| `organization` | object | `{in_app, email, webhook}` booleans |
| `personal_in_app` | boolean | `false` only when the caller muted a non-mandatory type |
| `receives_in_app` | boolean | Whether the caller's role holds the type's permission |

**WebhookEndpointView**: `id` (string), `url` (string), `created_at`
(date-time), `created_by` (string or null).

**NotificationDeliveryView**

| Field | Type | Description |
|---|---|---|
| `id` | string | |
| `notification_type` | string | |
| `title` | string | |
| `channel` | string | `email` or `webhook` |
| `destination` | string | The recipient's e-mail address (not masked), or the webhook endpoint ID |
| `status` | string | `pending`, `sent`, `failed` or `cancelled` |
| `attempts` | integer | |
| `failure_code` | string or null | |
| `last_attempt_at` | string (date-time) or null | |
| `next_retry_at` | string (date-time) or null | |
| `created_at` | string (date-time) | |

## Notification types

| Type | Category | Required permission | Mandatory in-app | Default e-mail | Default webhook |
|---|---|---|---|---|---|
| `critical_violation` | violations | `violations:read` | yes | on | on |
| `high_violation` | violations | `violations:read` | no | off | off |
| `violation_digest` | violations | `violations:read` | no | on | on |
| `policy_changed` | policy | `audit:read` | yes | on | on |
| `policy_rolled_back` | policy | `audit:read` | yes | on | on |
| `policy_emergency_published` | policy | `audit:read` | yes | on | on |
| `policy_approval_requested` | policy | `policies:approve` | no | on | off |
| `policy_rollout_failed` | policy | `policies:publish` | yes | on | on |
| `policy_propagation_failed` | policy | `policies:publish` | yes | on | on |
| `exception_requested` | policy | `exceptions:approve` | no | on | off |
| `exception_approved` | policy | `exceptions:read` | no | on | on |
| `exception_expiring` | policy | `exceptions:read` | no | off | off |
| `exception_ended` | policy | `exceptions:read` | no | off | on |
| `organization_settings_changed` | policy | `audit:read` | no | on | on |
| `installation_disconnected` | github | `github:manage` | yes | on | on |
| `installation_reconnected` | github | `github:manage` | no | on | on |
| `repository_unprotected` | github | `repositories:manage` | yes | on | on |
| `merge_queue_failure` | scans | `scans:read` | no | off | on |
| `check_rerun_failed` | scans | `scans:read` | no | off | off |

In-app delivery is on by default for every type. Mandatory types cannot be muted
by a user or turned off in-app by an organization. Their e-mail and webhook
delivery can still be configured.

## GET /api/v1/notifications

Lists the caller's visible notifications, newest first.

| Query parameter | Values | Default |
|---|---|---|
| `state` | `unread`, `read`, `archived`, `all` | every state except `archived`; `all` means the same |
| `category` | `all`, `critical`, `violations`, `policy`, `github`, `scans` | no filter. `critical` filters by `severity = critical`, not by category. |
| `organization` | positive integer | no filter. An organization the caller cannot see gives an empty list, not `404`. |
| `cursor`, `limit` | see [pagination.md](pagination.md) | `limit` 25 |

Response `200`. `data` is an array of `NotificationView`. `meta` is
`{next_cursor, limit, counts}`, where `counts` is a `NotificationCounts` for the
whole inbox and ignores the filters above.

Pagination: keyset cursor, ordered by occurrence time, then ID, descending.

Errors: `400 VALIDATION_ERROR` with `field` set to `state`, `category`,
`organization`, `limit` or `cursor`.

```json
{
  "data": [
    {
      "id": "8c1d0f4e2b7a49d6a3e5f10b9c2d7e61",
      "type": "critical_violation",
      "category": "violations",
      "severity": "critical",
      "state": "unread",
      "title": "Critical violation in example-org/api",
      "body": "...",
      "organization_id": 5001,
      "repository": { "id": 7001, "installation_id": 42, "full_name": "example-org/api" },
      "resource_type": "violation",
      "resource_id": "4b2e9a7c0d1f43e8b6a5c9d2e7f01a3b",
      "link": "/violations/4b2e9a7c0d1f43e8b6a5c9d2e7f01a3b",
      "occurrences": 1,
      "created_at": "2026-09-17T09:30:00Z",
      "last_occurred_at": "2026-09-17T09:30:00Z",
      "read_at": null
    }
  ],
  "meta": {
    "next_cursor": null,
    "limit": 25,
    "counts": { "unread": 1, "unread_critical": 1, "capped": false }
  }
}
```

## GET /api/v1/notifications/counts

Response `200`. `data` is a `NotificationCounts`. This endpoint takes no query
parameters.

## POST /api/v1/notifications/read-all

Marks the caller's visible unread notifications as read: at most 10,000 in
one call.

| Body field | Type | Required | Description |
|---|---|---|---|
| `organization_id` | integer | no | Only this organization. An organization the caller cannot see updates nothing. |

The body may be empty.

Response `200`: `{"data": {"updated": <integer>}, "meta": {}}`.

Errors: `400 VALIDATION_ERROR` ("organization_id must be an integer", `field`
`organization_id`).

Side effect: a `notification_read` audit event for each affected organization.

## GET /api/v1/notifications/{notification_id}

`notification_id` is 32 lowercase hexadecimal characters.

Response `200`. `data` is a `NotificationView`.

Errors: `404 NOT_FOUND` when the notification does not exist, is not visible, or
belongs to another user.

## POST /api/v1/notifications/{notification_id}/read, /unread, /archive

Changes the state of one of the caller's notifications. These endpoints take
no body.

| Action | Effect |
|---|---|
| `read` | `state` becomes `read`; `read_at` is set (an existing value is kept). An archived notification is un-archived. |
| `unread` | `state` becomes `unread`; `read_at` is cleared. An archived notification is un-archived. |
| `archive` | `state` becomes `archived`; `read_at` is set if it was empty. |

Repeating an action is harmless.

Response `200`. `data` is the updated `NotificationView`.

Errors: `404 NOT_FOUND` when the notification is not visible to the caller.

Side effect: a `notification_read` audit event when an unread notification
becomes read or archived.

## GET /api/v1/notification-preferences

Returns the notification settings of every organization in which the caller has
a role, sorted by organization login. The endpoint does not check a separate
permission.

Response `200`. `data` is an array of `OrganizationNotificationSettingsView`.

## PATCH /api/v1/notification-preferences

Mutes or unmutes notification types in the caller's own inbox for one
organization. This changes in-app delivery only, and it does not remove
notifications already delivered.

| Body field | Type | Required | Description |
|---|---|---|---|
| `organization_id` | integer | yes | |
| `in_app` | object | yes | Non-empty map of notification type to boolean. `false` mutes the type. |

Checks run in this order:

| Status | Code | `field` | When |
|---|---|---|---|
| 400 | `VALIDATION_ERROR` | `organization_id` | missing or not an integer |
| 404 | `NOT_FOUND` | | the caller has no role in the organization |
| 400 | `VALIDATION_ERROR` | `in_app` | not an object, empty, or contains an unknown type |
| 400 | `VALIDATION_ERROR` | `in_app.<type>` | a value is not a boolean, or is `false` for a mandatory type |

Response `200`. `data` is the organization's
`OrganizationNotificationSettingsView`.

Side effect: a `notification_preferences_changed` audit event.

```bash
curl -s -X PATCH https://commitguard.example.com/api/v1/notification-preferences \
  -H 'Cookie: __Host-commitguard_session=<session-token>' \
  -H 'Origin: https://commitguard.example.com' \
  -H 'X-CSRF-Token: <csrf-token>' \
  -H 'Content-Type: application/json' \
  -d '{"organization_id": 5001, "in_app": {"high_violation": false}}'
```

## GET /api/v1/organizations/{organization_id}/notification-settings

Requires `notifications:read`.

Response `200`. `data` is an `OrganizationNotificationSettingsView`. For callers
without `notifications:manage`, `email_recipients` and `webhooks` are empty and
`can_manage` is `false`.

Errors: `404 NOT_FOUND` (not a member).

## PUT /api/v1/organizations/{organization_id}/notification-settings

Replaces the organization's notification settings. Requires
`notifications:manage`. No recent sign-in is required.

| Body field | Type | Required | Description |
|---|---|---|---|
| `expected_version` | integer ≥ 0 | yes | The `version` you read |
| `types` | object | no | Map of type to `{in_app?, email?, webhook?}` booleans |
| `email_recipients` | array of strings | no | At most 20 plain e-mail addresses, each at most 254 characters. They are trimmed, lower-cased and de-duplicated. If omitted, the current list is kept. |
| `confirm` | boolean | when deliveries are turned off | Must be `true` |

**The `types` document replaces the stored one.** Any type or channel left out
falls back to its **built-in default**, not to the current organization
setting. Send the full document you read.

Checks run in this order:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not a member / role lacks `notifications:manage` |
| 400 | `VALIDATION_ERROR` | `expected_version` invalid; `types` not an object, unknown type or channel, non-boolean value, or `in_app: false` for a mandatory type; invalid `email_recipients` |
| 409 | `CONFLICT` | `expected_version` is not the current version ("Notification settings were changed by someone else (now version N). Reload before saving.") |
| 409 | `CONFIRMATION_REQUIRED` | a delivery that was on is turned off, or a recipient is removed, and `confirm` is not `true`. The message lists up to 10 affected deliveries. |

Response `200`. `data` is the updated `OrganizationNotificationSettingsView`, with
`version` = `expected_version + 1`.

Side effect: a `notification_settings_changed` audit event. Added recipients
are masked in the audit event.

## POST /api/v1/organizations/{organization_id}/notification-webhooks

Adds an HTTPS webhook endpoint. Requires `notifications:manage`.

| Body field | Type | Required | Description |
|---|---|---|---|
| `url` | string | yes | `https://` URL, at most 2,048 characters, without credentials or fragment |
| `confirm` | boolean | yes | Must be `true` |

Checks run in this order:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not a member / role lacks `notifications:manage` |
| 409 | `CONFLICT` | webhook delivery is not configured on this server |
| 400 | `VALIDATION_ERROR` (`field` `url`) | not an `https` URL, invalid characters, credentials or fragment, invalid port. In `production`, a `localhost` name or a non-public IP address literal is also rejected. Outside production, `http` to `localhost`, `127.0.0.1` or `::1` is accepted. |
| 409 | `CONFIRMATION_REQUIRED` | `confirm` is not `true` |
| 401 | `REAUTHENTICATION_REQUIRED` | last sign-in more than 15 minutes ago |
| 409 | `CONFLICT` | the organization already has 10 webhooks |

Host names are not resolved when the endpoint is added. Addresses are checked
when a delivery is sent.

Response `201`. `data` is:

| Field | Type | Description |
|---|---|---|
| `endpoint` | object | `WebhookEndpointView` |
| `signing_secret` | string | `whsec_` followed by 64 hexadecimal characters. No API endpoint returns it again. |

Side effect: a `notification_webhook_added` audit event (it records the host only).

```json
{
  "data": {
    "endpoint": {
      "id": "d2a1c7e94b3f40e8a9b6c5d4e3f2a1b0",
      "url": "https://hooks.example.com/commitguard",
      "created_at": "2026-09-17T09:40:00Z",
      "created_by": "octo-admin"
    },
    "signing_secret": "whsec_<signing-secret>"
  },
  "meta": {}
}
```

## DELETE /api/v1/organizations/{organization_id}/notification-webhooks/{endpoint_id}

Removes a webhook endpoint. Requires `notifications:manage`. No body,
confirmation or recent sign-in is needed. Pending deliveries to the endpoint are
cancelled (`status` `cancelled`, `failure_code` `webhook_endpoint_removed`).

Response `200`: `{"data": {"removed": true}, "meta": {}}`.

Errors: `404 NOT_FOUND` when the endpoint does not exist, was already removed,
or belongs to another organization.

Side effect: a `notification_webhook_removed` audit event.

## GET /api/v1/organizations/{organization_id}/notification-deliveries

Lists e-mail and webhook delivery records, newest first. Requires
`notifications:manage`.

| Query parameter | Description |
|---|---|
| `cursor`, `limit` | See [pagination.md](pagination.md). There are no filters. |

Response `200`. `data` is an array of `NotificationDeliveryView`. `meta` is
`{next_cursor, limit}`. Pagination uses a keyset cursor.
