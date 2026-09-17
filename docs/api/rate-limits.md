# Rate limits

CommitGuard limits requests in the application. Every `/api/v1` route belongs
to one rate-limit category, and the webhook endpoint has its own limit.

Source: `RATE_LIMITS` and `DashboardApi._dispatch` in
`src/commitguard/api/app.py`; `RequestRateLimiter` in
`src/commitguard/security/rate_limit.py`; `DEFAULT_RATE_LIMIT_PER_MINUTE` in
`src/commitguard/github/app.py`.

## API categories

| Category | Requests per minute | Routes |
|---|---|---|
| `auth` | 20 | `GET /api/v1/auth/login`, `GET /api/v1/auth/callback` |
| `read` | 600 | Most `GET` routes, plus `POST /api/v1/policies/{organization_id}/preview` |
| `search` | 120 | A `read` route called with a non-empty `q` parameter; always for `GET .../reports/{kind}` and `GET .../search` |
| `write` | 30 | Most `POST`, `PUT`, `PATCH` and `DELETE` routes |
| `github` | 10 | Routes that call GitHub: `POST .../enforcement/refresh`, `POST .../rescan`, `POST .../installations/{installation_id}/sync` |
| `sensitive` | 10 | Policy rollbacks, draft approval and publication, rollout advance, resume and rollback, exception requests, approval and revocation, organization settings and rules, repository mode, group archival, bulk operations, notification settings and webhooks |

Each endpoint page lists the category of every route.

## How the limit is applied

- **Counter.** Each category has its own counter with a fixed one-minute window.
  The counter is keyed by the signed-in user ID. For the `auth` routes, which
  run before sign-in, it is keyed by the client address (`REMOTE_ADDR`).
- **Order.** The check runs after authentication and before the CSRF and
  permission checks. Requests without a valid session are answered `401`
  before they are counted.
- **Rejection.** A request over the limit answers:

  ```http
  HTTP/1.1 429 Too Many Requests
  Retry-After: 60
  Content-Type: application/json; charset=utf-8

  {"error":{"code":"RATE_LIMITED","message":"Too many requests. Wait a minute and try again.","request_id":"<request-id>"}}
  ```

  Rejected requests still count.
- **Headers.** No `X-RateLimit-*` headers are sent. Clients cannot see how many
  requests remain.

## Webhook endpoint

`POST /webhooks/github` accepts 600 deliveries per minute per client address. It
answers `429 {"error": "too many requests"}` without a `Retry-After` header.
The limit is checked before the signature. See [webhooks.md](webhooks.md).

## Limitations operators should know

- **Per process.** Counters are kept in memory in each process. They reset on
  restart and are not shared between processes or hosts. With N processes, a
  user can make up to N times the limit. The example in the docstring of
  `commitguard.github.app.wsgi_app_from_environment` runs one worker process
  with several threads (`gunicorn --workers 1 --threads 8`).
- **Not configurable.** The API limits are constants in the code, and no
  environment variable changes them. The webhook limit is a constructor argument
  of `GitHubAppService` with a default of 600 per minute. The standard entry
  point does not expose it.
- **Client address.** The server uses `REMOTE_ADDR` and ignores
  `X-Forwarded-For`. Behind a reverse proxy, every sign-in request and every
  webhook delivery shares the proxy's address. The 20-per-minute sign-in limit
  then applies to all users together, and the webhook limit to all deliveries.
- **Bounded memory.** A limiter tracks at most 10,000 keys. When more appear, it
  forgets all current counts.

Operators who need stronger or distributed limits should also enforce them in
front of the service. Examples are per-client-IP limits at the reverse proxy or
load balancer (with the real client address), connection limits, and request
body size limits. The application limits then act as a second layer.
