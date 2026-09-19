# CommitGuard HTTP API

The GitHub App service serves a JSON API under `/api/v1`. The CommitGuard
dashboard is built on it. It exposes repositories, scans, violations, policies
and their governance, exceptions, audit events, notifications and GitHub
installations. The same process also receives GitHub webhooks.

> **Stability.** CommitGuard is pre-alpha (`0.1.1`, "Development Status :: 2
> - Pre-Alpha"). The API is versioned `v1` in its path, but no compatibility
> promise exists yet. Endpoints, fields and error codes can change between
> commits. There is no OpenAPI document; these pages, the view models in
> `src/commitguard/controlplane/views.py` and the governance services are the
> contract.

## Contents

Conventions:

- [Authentication](authentication.md): GitHub sign-in, session cookie, CSRF, CORS, re-authentication
- [Authorization](authorization.md): roles, permissions, tenant isolation, repository visibility
- [Errors](errors.md): error format, codes, order of checks, response headers
- [Pagination, filtering and sorting](pagination.md)
- [Rate limits](rate-limits.md)

Resources:

| Page | Endpoints |
|---|---|
| [authentication.md](authentication.md#endpoints) | `/auth/login`, `/auth/callback`, `/auth/session`, `/auth/logout`, `/auth/sessions` (6) |
| [organizations.md](organizations.md) | organizations, organization settings, members (7) |
| [security-posture.md](security-posture.md) | dashboard overview, security posture, repository matrix, trends, security events, search (9) |
| [compliance-exports.md](compliance-exports.md) | compliance reports as JSON or CSV (1) |
| [repositories.md](repositories.md) | repositories, merge queue, monitoring, enforcement refresh, onboarding and mode (7) |
| [repository-groups.md](repository-groups.md) | repository groups and their members (7) |
| [scans.md](scans.md) | scans, findings, comparison, executions, re-scan (5) |
| [violations.md](violations.md) | violations and acknowledgements (4) |
| [rules.md](rules.md) | bundled rules and organization rules (5) |
| [policies.md](policies.md) | organization policy, versions, diff, rollback, policy targets, effective policy, propagation (13) |
| [policy-drafts.md](policy-drafts.md) | drafts, approval, publication, emergency publication (10) |
| [simulations.md](simulations.md) | policy simulations (3) |
| [rollouts.md](rollouts.md) | staged rollouts (6) |
| [exceptions.md](exceptions.md) | policy exceptions (7) |
| [bulk-operations.md](bulk-operations.md) | bulk repository operations (5) |
| [scan-schedules.md](scan-schedules.md) | scheduled default-branch scans (5) |
| [audit.md](audit.md) | audit log (2) |
| [github-installations.md](github-installations.md) | GitHub App installations and sync (4) |
| [notifications.md](notifications.md) | inbox, preferences, organization notification settings, webhook endpoints, deliveries (14) |
| [webhooks.md](webhooks.md) | `POST /webhooks/github`, `GET /health`, `GET /ready` (outside `/api/v1`) |

In total, 120 routes are under `/api/v1`, plus the 3 service endpoints.

## Base URL and versioning

The API is served from the dashboard origin configured in
`COMMITGUARD_DASHBOARD_URL`, for example `https://commitguard.example.com`.

- Every route starts with `/api/v1`, the only version.
- The API exists only when `COMMITGUARD_DASHBOARD_URL` is set. Otherwise
  requests under `/api/` answer `404 {"error": "not found"}`.
- The same process serves `/webhooks/github`, `/health` and `/ready`, and
  optionally the built dashboard (`COMMITGUARD_DASHBOARD_STATIC_DIR`).

Configuration is described in [../dashboard.md](../dashboard.md#running-the-dashboard)
and [../deployment.md](../deployment.md). In `production` (the default
`COMMITGUARD_ENV`), the dashboard URL must be `https://`.

## Conventions

**Authentication.** Every route except `GET /api/v1/auth/login` and
`GET /api/v1/auth/callback` requires the `__Host-commitguard_session` cookie.
There are no API tokens. Write requests (`POST`, `PUT`, `PATCH`, `DELETE`) also
need an allowed `Origin` header and `X-CSRF-Token`. See
[authentication.md](authentication.md).

**Requests.**

- Bodies are JSON objects sent with `Content-Type: application/json`, at most
  64 KiB and 16 levels deep, with no duplicate keys.
- An empty body is treated as `{}`. Unknown top-level fields are ignored unless
  an endpoint says otherwise.
- Query parameters may not be repeated.
- `HEAD` is accepted wherever `GET` is.

**Responses.** A success has this shape:

```json
{ "data": {}, "meta": {} }
```

- `data` is an object or an array.
- `meta` is `{}` unless the endpoint adds something: pagination
  (`next_cursor`, `limit`) or extra results such as `changes`.
- An error has the shape `{"error": {...}}`; see [errors.md](errors.md).
- Every response has an `X-Request-ID` header.
- Compliance report downloads are the only files; see
  [compliance-exports.md](compliance-exports.md).

**Types.**

- IDs of GitHub objects (organizations, users, installations, repositories)
  are integers.
- IDs of CommitGuard resources (scans, violations, drafts, exceptions and so
  on) are 32 lowercase hexadecimal characters.
- A path parameter in the wrong format does not match the route and answers
  `404`.
- Date-times are ISO 8601 in UTC, for example `2026-09-17T09:30:00Z`.
- Actions are `allow`, `warn` or `block`. Severities are `info`, `low`,
  `medium`, `high` or `critical`.

**Writes.**

- **Optimistic concurrency.** Endpoints that change versioned documents require
  `expected_version` or `expected_revision`. A stale value answers
  `409 CONFLICT`.
- **Confirmation.** Changes that weaken enforcement require an explicit
  confirmation flag (`confirm` or `confirm_weakening`). Without it, the answer is
  `409 CONFIRMATION_REQUIRED`.
- **Audit.** Every change that affects security state writes an audit event in
  the same transaction; see [audit.md](audit.md).

**Git metadata is untrusted text.** Author names, commit messages and evidence
values come from Git and are returned as plain text. Clients must render them as
text, never as HTML.

## Example

```bash
# 1. Sign in with a browser at https://commitguard.example.com/login, then read the session.
curl -s https://commitguard.example.com/api/v1/auth/session \
  -H 'Cookie: __Host-commitguard_session=<session-token>'

# 2. List open critical violations.
curl -s 'https://commitguard.example.com/api/v1/violations?status=open&severity=critical' \
  -H 'Cookie: __Host-commitguard_session=<session-token>'

# 3. Acknowledge one (writes need Origin and the CSRF token from step 1).
curl -s -X PUT https://commitguard.example.com/api/v1/violations/<violation-id>/acknowledgement \
  -H 'Cookie: __Host-commitguard_session=<session-token>' \
  -H 'Origin: https://commitguard.example.com' \
  -H 'X-CSRF-Token: <csrf-token>' \
  -H 'Content-Type: application/json' \
  -d '{"note": "Tracked in the incident review"}'
```
