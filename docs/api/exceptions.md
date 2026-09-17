# Policy exceptions

A policy exception lowers the enforcement of one rule (`warn` or `allow`) for one
scope: the organization, a repository group or a repository. Exceptions are
temporary unless the organization allows permanent ones. They are never deleted
through the API. They end by rejection, cancellation, revocation or expiry, and
their history is kept. For the concepts, see
[../policy-exceptions.md](../policy-exceptions.md).

Source: `src/commitguard/governance/exceptions.py`; handlers in
`src/commitguard/api/governance.py`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| [`GET /api/v1/organizations/{organization_id}/exceptions`](#get-apiv1organizationsorganization_idexceptions) | `exceptions:read` | `read` |
| [`POST /api/v1/organizations/{organization_id}/exceptions`](#post-apiv1organizationsorganization_idexceptions) | `exceptions:create` | `sensitive` |
| [`GET /api/v1/exceptions/{exception_id}`](#get-apiv1exceptionsexception_id) | `exceptions:read` | `read` |
| [`POST /api/v1/exceptions/{exception_id}/approve`](#post-apiv1exceptionsexception_idapprove) | `exceptions:approve`, not the requester | `sensitive` |
| [`POST /api/v1/exceptions/{exception_id}/reject`](#post-apiv1exceptionsexception_idreject) | `exceptions:approve` | `write` |
| [`POST /api/v1/exceptions/{exception_id}/revoke`](#post-apiv1exceptionsexception_idrevoke) | `exceptions:revoke` | `sensitive` |
| [`POST /api/v1/exceptions/{exception_id}/cancel`](#post-apiv1exceptionsexception_idcancel) | the requester, or `exceptions:revoke` | `write` |

Organization-level summaries of exceptions are also available from
[`GET /api/v1/organizations/{organization_id}/security/exceptions`](security-posture.md#get-apiv1organizationsorganization_idsecurityexceptions).

## Access rules

- A caller without a role in the organization gets `404 NOT_FOUND`. A member
  whose role lacks the permission gets `403 FORBIDDEN`.
- For `/exceptions/{exception_id}` routes, the organization comes from the
  stored exception, never from the request. The service checks, in this order:
  the exception exists, the caller has `exceptions:read` in its organization,
  and (for repository scopes) GitHub listed the repository for the session.
  Each failure is `404`. Only then does it check the operation's permission (`403`).
- None of these endpoints requires a recent sign-in.

## Statuses

| Status | Meaning |
|---|---|
| `requested` | Waiting for approval |
| `active` | In effect until `expires_at` (or permanently) |
| `rejected` | An approver declined it |
| `cancelled` | Withdrawn before approval |
| `revoked` | Ended by an administrator while active |
| `expired` | Reached `expires_at` (set by a background task) |

```text
requested ──approve──► active ──revoke──► revoked
    │                    └──(expiry)──► expired
    ├──reject──► rejected
    └──cancel──► cancelled
```

A new exception is created `active`, with no approval, only when all of these hold:

- the rule's severity is below the organization setting
  `exception_approval_min_severity` (default `high`);
- the scope is a repository;
- the exception is not permanent.

Every other exception is created `requested`.

## PolicyExceptionView

| Field | Type | Description |
|---|---|---|
| `id` | string | 32 hexadecimal characters |
| `organization_id` | integer | |
| `rule_id` | string | |
| `rule_name` | string | |
| `severity` | string | Severity of the rule |
| `scope` | object | `{type, id, label}`. `type` is `organization`, `group` or `repository`. `id` is `""` for the organization, a group ID, or a repository ID as a string. `label` is `(removed)` when the target no longer exists. |
| `action` | string | `warn` or `allow` |
| `reason` | string | |
| `status` | string | See [Statuses](#statuses) |
| `requires_approval` | boolean | |
| `permanent` | boolean | |
| `expires_at` | string (date-time) or null | |
| `expiring_soon` | boolean | Active and expiring within 7 days |
| `requested_at` | string (date-time) | |
| `requested_by` | string or null | |
| `decided_at`, `decided_by`, `decision_note` | nullable | |
| `activated_at`, `revoked_at`, `revoked_by`, `revoke_reason`, `expired_at` | nullable | |
| `can_approve` | boolean | Requested, caller has `exceptions:approve` and is not the requester |
| `can_revoke` | boolean | Active and caller has `exceptions:revoke` |
| `can_cancel` | boolean | Requested, and caller is the requester or has `exceptions:revoke` |

## GET /api/v1/organizations/{organization_id}/exceptions

| Query parameter | Values |
|---|---|
| `status` | `requested`, `active`, `rejected`, `cancelled`, `revoked`, `expired` |
| `rule` | `ai_coauthor`, `ai_identity`, `ai_trailer`, `malformed_trailer`, `bot_identity` |
| `repository` | repository ID (digits, at most 16); repository-scoped exceptions only |
| `group` | group ID (32 hexadecimal characters); group-scoped exceptions only |
| `cursor`, `limit` | See [pagination.md](pagination.md); offset cursor |

An empty value (for example `status=`) is rejected, not ignored.

Order: `requested` first, then `active`, then all others; within each,
newest `requested_at` first. Repository-scoped exceptions for repositories not
listed for the session are left out. The service reads at most 5,000
exceptions before that filter, so older rows beyond that cannot be paged to.

Response `200`. `data` is an array of `PolicyExceptionView`. `meta` is
`{next_cursor, limit}`.

Errors: `400 VALIDATION_ERROR` (`field` `repository`, `cursor`, `limit`,
`status`, `rule` or `group`); `404`/`403` as described above. `repository`,
`cursor` and `limit` are validated before the permission check.

## POST /api/v1/organizations/{organization_id}/exceptions

Requests an exception.

| Body field | Type | Required | Description |
|---|---|---|---|
| `rule_id` | string | yes | A known rule ID |
| `scope_type` | string | yes | `organization`, `group` or `repository` |
| `scope_id` | string or integer | for `group` and `repository` | Omitted, `null` or `""` for `organization`. A group ID (active group of this organization) for `group`. A repository ID (integer or digit string) for `repository`. |
| `action` | string | yes | `warn` or `allow` |
| `reason` | string | yes | At most 500 characters |
| `expires_at` | string | unless `permanent` | ISO 8601 date or date-time (naive values are UTC). More than 5 minutes from now, and at most `exception_max_days` (organization setting, default 90) from now. |
| `permanent` | boolean | no | Default `false`. Only when the organization setting `allow_permanent_exceptions` is on, and only for callers with `exceptions:approve`. `expires_at` must then be omitted. |

Checks, in this order:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not a member / lacks `exceptions:create` |
| 400 | `VALIDATION_ERROR` | invalid `rule_id`, `scope_type`, `action`, `reason` or `permanent`; malformed `scope_id` |
| 404 | `NOT_FOUND` | "The group was not found in this organization." / "The repository was not found in this organization." (also for a repository not listed for the session) |
| 403 | `FORBIDDEN` | permanent requested but "This organization does not allow permanent exceptions.", or the caller lacks `exceptions:approve` |
| 400 | `VALIDATION_ERROR` (`field` `expires_at`) | permanent with `expires_at`; temporary without `expires_at`; not in the future; longer than allowed ("An exception may last at most N days.") |
| 409 | `CONFLICT` | an exception for the same rule and scope is already `requested` or `active` |

Response `201`. `data` is a `PolicyExceptionView`.

Side effects:

- An `exception_requested` audit event.
- If approval is required, an `exception_requested` notification to holders of
  `exceptions:approve`.
- If the exception is active immediately, the effective policy of the affected
  repositories is recomputed.

```bash
curl -s -X POST https://commitguard.example.com/api/v1/organizations/5001/exceptions \
  -H 'Cookie: __Host-commitguard_session=<session-token>' \
  -H 'Origin: https://commitguard.example.com' \
  -H 'X-CSRF-Token: <csrf-token>' \
  -H 'Content-Type: application/json' \
  -d '{"rule_id": "bot_identity", "scope_type": "repository", "scope_id": 7001,
       "action": "allow", "reason": "Release bot migration", "expires_at": "2026-10-01"}'
```

## GET /api/v1/exceptions/{exception_id}

Response `200`. `data` is a `PolicyExceptionView`.

Errors: `404 NOT_FOUND`.

## POST /api/v1/exceptions/{exception_id}/approve

| Body field | Type | Required | Description |
|---|---|---|---|
| `note` | string | no | At most 500 characters |

Checks after the [access rules](#access-rules):

| Status | Code | When |
|---|---|---|
| 403 | `FORBIDDEN` | lacks `exceptions:approve` |
| 409 | `CONFLICT` | not `requested` ("The exception is <status>, not waiting for approval.") |
| 403 | `FORBIDDEN` | "An exception must be approved by someone else." (caller is the requester) |
| 400 | `VALIDATION_ERROR` | invalid `note` |
| 409 | `CONFLICT` | the expiry has already passed; changed concurrently |

Response `200`. `data` is the `PolicyExceptionView`, now `active`.

Side effects: an `exception_approved` audit event; an `exception_approved`
notification; the effective policy of the affected repositories is recomputed.

## POST /api/v1/exceptions/{exception_id}/reject

| Body field | Type | Required | Description |
|---|---|---|---|
| `note` | string | yes | At most 500 characters |

Checks after the access rules: `403` without `exceptions:approve`; `409 CONFLICT`
when the exception is not `requested`; `400` for a missing or invalid `note`.
The code does not stop a requester who holds `exceptions:approve` from rejecting
their own request.

Response `200`. `data` is the `PolicyExceptionView`, now `rejected`.

Side effect: an `exception_rejected` audit event.

## POST /api/v1/exceptions/{exception_id}/revoke

| Body field | Type | Required | Description |
|---|---|---|---|
| `reason` | string | yes | At most 500 characters |

Checks after the access rules: `403` without `exceptions:revoke`; `409 CONFLICT`
when the exception is not `active` ("only active ones are revoked"); `400` for a
missing or invalid `reason`.

Response `200`. `data` is the `PolicyExceptionView`, now `revoked`.

Side effects: an `exception_revoked` audit event; an `exception_ended`
notification; the effective policy of the affected repositories is recomputed.

## POST /api/v1/exceptions/{exception_id}/cancel

Withdraws a request. This endpoint takes no body.

Checks after the access rules, in this order: `409 CONFLICT` when the exception
is not `requested` ("only requests can be cancelled"); `403 FORBIDDEN` when the
caller is neither the requester nor holds `exceptions:revoke`.

Response `200`. `data` is the `PolicyExceptionView`, now `cancelled`.

Side effect: an `exception_cancelled` audit event.
