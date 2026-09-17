# Errors

Every `/api/v1` error is JSON with one shape. The status code and `code` tell a
client what happened. `message` is written for people and is safe to display.

Source: `ApiError` and `error_response` in `src/commitguard/api/http.py`;
`ControlPlaneError` and its subclasses in
`src/commitguard/controlplane/errors.py`; dispatch in
`src/commitguard/api/app.py`.

The webhook and health endpoints use a different format; see
[webhooks.md](webhooks.md).

## Error format

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "limit must be between 1 and 100",
    "request_id": "9f0c1d2e3b4a45968778695a4b3c2d1e",
    "field": "limit"
  }
}
```

| Field | Present | Description |
|---|---|---|
| `code` | always | Stable, machine-readable code (table below) |
| `message` | always | Human-readable explanation. The text may change; do not parse it. |
| `request_id` | always | 32 hexadecimal characters; also sent as the `X-Request-ID` header of every response |
| `field` | sometimes | The query parameter or body field at fault, for example `limit`, `floors.bot_identity` or `settings.timezone` |
| `details` | sometimes | Extra structured data. Today only `{"changes": [...]}` exists: the list of relaxed controls when organization settings answer `CONFIRMATION_REQUIRED`. See [organizations.md](organizations.md#put-apiv1organizationsorganization_idsettings). |

Messages never contain secrets, SQL, stack traces or data from another tenant.
A `500` response carries no details. Use `request_id` to find the request in the
server logs.

Some responses are not JSON errors:

- The sign-in callback always redirects; see
  [authentication.md](authentication.md#sign-in-flow).
- A successful report download is a file; see
  [compliance-exports.md](compliance-exports.md).

## Codes

| Status | Code | When |
|---|---|---|
| 400 | `VALIDATION_ERROR` | Invalid query parameter, body field, cursor, JSON document or query string. It also covers some state-independent rule violations, for example "The new policy is identical to the current version." |
| 401 | `UNAUTHENTICATED` | No valid session cookie |
| 401 | `SESSION_EXPIRED` | The session reached its lifetime or idle timeout; the response clears the cookie |
| 401 | `REAUTHENTICATION_REQUIRED` | The operation needs a sign-in within the last 15 minutes |
| 403 | `FORBIDDEN` | The caller can see the resource, but their role lacks the permission, or a rule forbids it (for example approving one's own change) |
| 403 | `CSRF_FAILED` | Write request without an allowed `Origin` or a valid `X-CSRF-Token` |
| 403 | `CORS_REJECTED` | `OPTIONS` preflight from an origin that is not allowed |
| 404 | `NOT_FOUND` | Unknown route, a path parameter in the wrong format, a missing resource, **or a resource outside the caller's access** |
| 405 | `METHOD_NOT_ALLOWED` | Known path, wrong method; the `Allow` header lists the allowed methods |
| 409 | `CONFLICT` | Optimistic concurrency failure (`expected_version`, `expected_revision`), an invalid state transition, a duplicate, or a limit (for example 10 webhooks per organization). A GitHub authorization refusal during a sync or enforcement refresh also answers `CONFLICT`. |
| 409 | `CONFIRMATION_REQUIRED` | A change that weakens enforcement or stops deliveries was sent without the required confirmation flag |
| 409 | `APPROVAL_REQUIRED` | The organization requires approval: `PUT /api/v1/policies/{organization_id}` is refused, or a draft that is not approved is published |
| 411 | `LENGTH_REQUIRED` | `Content-Length` of a `POST`, `PUT`, `PATCH` or `DELETE` is not a number. A missing header is treated as an empty body. |
| 413 | `PAYLOAD_TOO_LARGE` | Body larger than 64 KiB (65,536 bytes) |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | Non-empty body whose `Content-Type` is not `application/json` |
| 422 | `POLICY_VERSION_INVALID` | A stored policy version failed its integrity check and cannot be restored |
| 429 | `RATE_LIMITED` | Too many requests; `Retry-After: 60`. See [rate-limits.md](rate-limits.md). |
| 500 | `INTERNAL_ERROR` | Unexpected failure: "Something went wrong. Try again." |
| 502 | `GITHUB_UNAVAILABLE` | GitHub could not be reached or failed |

The API does not return `SIGN_IN_FAILED` as JSON. That internal code becomes a
redirect to `/login?error=sign_in_failed`.

## 404 versus 403

- A resource the caller cannot see answers `404 NOT_FOUND`, exactly like one that
  does not exist. This covers another tenant's resource, a repository GitHub did
  not list for the session, and an organization where the caller has no role.
- `403 FORBIDDEN` means the caller can see the resource but may not perform the
  operation.
- Routes that declare a permission in the route table answer `403` when the
  caller holds that permission in **no** organization. They answer `404` when
  the `organization` query parameter names an organization where the caller
  lacks it.

See [authorization.md](authorization.md).

## Order of checks

A request is checked in this order. The first failure determines the response.

1. **Request parsing.**
   - Body: `411` or `413`.
   - Query string: at most 4,096 characters and 32 parameters. A parameter may
     not be repeated, and control characters are not allowed. Failures are
     `400`; a repeated parameter sets `field` to its name.
2. **Route matching.** `404` or `405`.
3. **Authentication.** `401`; skipped for `auth/login` and `auth/callback`.
4. **Rate limiting.** `429`.
5. **CSRF.** For write methods: `403 CSRF_FAILED`.
6. **Route permission.** `403`, or `404` for the `organization` query parameter.
7. **Handler and service.**
   - Body JSON: `415`, or `400` for invalid JSON, duplicate keys, a non-object
     body or nesting deeper than 16 levels.
   - Then field validation, resource access (`404`/`403`), state (`409`),
     re-authentication (`401`) and GitHub (`502`).

The order inside step 7 varies by endpoint. Some handlers validate the body before
resource access, so a malformed request can answer `400` where a well-formed one
would answer `404`. The endpoint pages note the orders that matter.

## Response headers

Every `/api/v1` response, success or error, carries these headers:

| Header | Value |
|---|---|
| `X-Request-ID` | Request ID |
| `Cache-Control` | `no-store` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `no-referrer` |
| `X-Frame-Options` | `DENY` |
| `Content-Security-Policy` | `default-src 'none'; frame-ancestors 'none'; base-uri 'none'` |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Resource-Policy` | `same-origin` |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains`, only when `COMMITGUARD_ENV=production` and the dashboard URL is `https://` |
| `Vary` | `Origin` |

CORS headers are added for allowed cross-origin callers; see
[authentication.md](authentication.md#cors).
