# GitHub webhook and health endpoints

The GitHub App service also serves three endpoints outside `/api/v1`. They do
not use sessions or the `/api/v1` envelope, and they are served whether or not
the dashboard is enabled.

Source: `create_wsgi_app` and `GitHubAppService.handle_webhook` in
`src/commitguard/github/app.py`; `src/commitguard/github/webhooks.py`;
routing in `src/commitguard/api/hosting.py`.

| Method and path | Caller | Authentication |
|---|---|---|
| [`POST /webhooks/github`](#post-webhooksgithub) | GitHub | `X-Hub-Signature-256` (HMAC-SHA256 with the webhook secret) |
| [`GET /health`](#get-health) | load balancer, orchestrator | none |
| [`GET /ready`](#get-ready) | load balancer, orchestrator | none |

`HEAD` is also accepted for `/health` and `/ready`.

Responses are JSON (`Content-Type: application/json`) with sorted keys and these
headers: `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` and
`Content-Security-Policy: default-src 'none'`. The body is either
`{"status": "..."}` or `{"error": "<message>"}`.

When the dashboard is not enabled, requests under `/api/` are also answered by
this application: `404 {"error": "not found"}`.

## POST /webhooks/github

Receives GitHub App webhook deliveries. Configure it as the App's webhook URL;
see [../github-app.md](../github-app.md).

Processing order. Nothing in the body is parsed before the signature is verified.

1. Method must be `POST`; otherwise `405` with `Allow: POST`.
2. `Content-Type` must be `application/json`; otherwise `415`.
3. `Content-Length` must be present and numeric; otherwise `411`.
4. The body may be at most 25 MB; otherwise `413`.
5. Rate limit: 600 deliveries per minute per client address (`REMOTE_ADDR`);
   otherwise `429`.
6. `X-Hub-Signature-256` must be `sha256=<64 hex>` and match the body (compared
   in constant time); otherwise `401`.
7. `X-GitHub-Event` must match `[a-z_]{1,64}` and `X-GitHub-Delivery` must match
   `[0-9A-Za-z-]{8,72}`; otherwise `400`.
8. The body must be a JSON object: UTF-8, no duplicate keys, at most 64 levels
   deep. Otherwise the answer is `400`.
9. The payload of a supported event must contain its mandatory fields;
   otherwise `400`, with a `webhook_rejected` audit event.
10. The delivery ID is recorded for replay protection (see below).

| Status | Body | Meaning |
|---|---|---|
| 200 | `{"status": "processed"}` | Handled (installation changes, pull request closed or merged, ...) |
| 200 | `{"status": "duplicate"}` | This delivery ID was already processed with the same body |
| 202 | `{"status": "queued"}` | A scan or re-run was queued |
| 202 | `{"status": "ignored"}` | Event not used, or not applicable. Examples: `ping`, unsupported events, tags, paused monitoring, an installation that is not allowed, another App's check. |
| 202 | `{"status": "duplicate"}` | An equivalent scan is already queued or done |
| 400 | `{"error": "..."}` | Invalid headers, body or payload |
| 401 | `{"error": "missing webhook signature"}` (or `malformed` / `invalid`) | Authentication failed |
| 405, 411, 413, 415 | `{"error": "..."}` | See the processing order |
| 409 | `{"error": "delivery already received with different content"}` | A delivery ID was reused with a different body; a `webhook_rejected` audit event is written |
| 429 | `{"error": "too many requests"}` | Rate limit exceeded; no `Retry-After` header |
| 500 | `{"error": "internal error"}` | Processing failed; a redelivery of the same delivery ID is processed again |

Supported events: `installation`, `installation_repositories`, `pull_request`,
`push`, `merge_group`, `check_run`, `check_suite`. Any other event, including
`ping`, is acknowledged with `202 {"status": "ignored"}`, so GitHub does not
retry it.

**Replay protection.** Each verified delivery ID is stored with a SHA-256 of
its body:

- The same ID with the same body is answered as `duplicate`. The exception is a
  delivery whose earlier processing failed or was abandoned, which is processed
  again.
- The same ID with a different body or event name is answered with `409`.

## GET /health

Liveness. It does not check dependencies.

Response `200`: `{"status": "ok"}`. Other methods get `405`.

## GET /ready

Readiness.

| Check | `ok` when | Otherwise |
|---|---|---|
| `configuration` | always (credentials were parsed at start-up) | |
| `store` | the state database answers | `unavailable` |
| `workers` | all worker threads are alive | `not running` |
| `queue` | the job queue is below 90 % of its capacity | `saturated` |

Response `200` when every check is `ok`, otherwise `503`:

```json
{ "checks": { "configuration": "ok", "queue": "ok", "store": "ok", "workers": "ok" }, "status": "ready" }
```

When not ready, `status` is `not_ready`. Other methods get `405`.
