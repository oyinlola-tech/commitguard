# Troubleshooting

Messages below are quoted from the code. CLI errors are printed as
`commitguard: error: <message>`. Exit codes are always `0` (allowed), `1`
(blocked by policy) or `2` (error); an error never exits `1`.

Incident procedures for a running App service are in
[../operations/runbook.md](../operations/runbook.md).

## Diagnostic tools

| Tool | Use it for | Output |
|---|---|---|
| `commitguard doctor` | a local repository: Python, Git, configuration, detection self-test, rules, hooks, workflow files | ends with `Status: HEALTHY`, `DEGRADED` (exit 0) or `UNHEALTHY` (exit 2), and an `Enforcement:` line |
| `commitguard github setup` | a repository's workflow files and the required check name (no network) | exits 2 if no workflow runs CommitGuard or a workflow has a failing issue |
| `commitguard github validate [--offline] [--installation-id N]` | App configuration, Git version, credentials, permissions, events, installations | ends with `READY`, `CONFIGURATION VALID (GitHub not contacted)` or `NOT READY` (exit 2) |
| `commitguard github webhook-test payload.json --event <name> [--signature sha256=...]` | whether a payload and signature verify and how the App would treat the event (no network, no scan) | a checklist; exit 2 on failure |
| `GET /health`, `GET /ready` | whether the running service is alive and ready | `/ready` names failing checks with `503` |
| service logs | JSON lines on stderr; field `event` names what happened | see [Logs](#logs) |

`doctor` never repairs anything and cannot see branch protection: it always
reports "branch protection cannot be verified locally".

## Installation

**`pip install commitguard` installed a different tool, or `commitguard` has
unexpected commands.** The PyPI project `commitguard` is unrelated to this
repository. Uninstall it and install from source, pinned to a commit:

```bash
python -m pip uninstall commitguard
pipx install "git+https://github.com/oyinlola-tech/commitguard@<commit-sha>"
commitguard --version      # commitguard 0.1.0
```

**`the GitHub App needs the optional dependencies: pip install 'commitguard[app]'`**
(from `commitguard github serve` or `validate`, reported as the private key
check). `cryptography` is missing. The message names the extra; install it from
source, not from PyPI:

```bash
python -m pip install "commitguard[app] @ git+https://github.com/oyinlola-tech/commitguard@<commit-sha>"
```

## Local hooks and the CLI

| Message | Cause | Fix |
|---|---|---|
| `CommitGuard is not available.` / `The repository's pre-push security hook could not execute.` | the interpreter embedded in the hook no longer exists and `commitguard` is not on `PATH` (moved or deleted virtual environment, reinstalled pipx) | `commitguard install` from the current installation; `commitguard doctor` |
| `CommitGuard could not verify repository policy.` `Reason: ...` `Push blocked because the security check could not be completed.` | any error during a hook; the reason line says which | fix the reason; errors always block |
| `.commitguard.yaml: invalid YAML: ...`, `unknown policy id(s): ...`, `version must be the integer 1` | invalid configuration (unknown keys, policies and actions are errors, never warnings) | fix the file; `commitguard doctor` shows the same error |
| `multiple configuration files found: .commitguard.yaml, .commitguard.yml` | both names exist | keep one |
| `not inside a Git work tree: ...` | command run outside a repository (bare repositories are not supported) | run it inside a work tree |
| `git executable not found on PATH` | Git missing in the hook's environment | install Git 2.31+ or fix `PATH` for GUI clients |
| `git could not determine GIT_AUTHOR_IDENT (is user.name/user.email set?)` | no identity configured | `git config user.name` / `user.email` |
| `more than 10000 commits would need to be analysed` | the push exceeds `enforcement.max_push_commits` | push in smaller steps, or raise the limit deliberately |
| `core.hooksPath points outside this repository's Git directory (...)` | shared or tracked hooks path | unset `core.hooksPath`, or `commitguard install --allow-shared-hooks-path` if intended |
| `... is not managed by CommitGuard and pre-push.pre-commitguard already exists; refusing to overwrite either.` | a foreign hook and a preserved hook both exist | merge them manually, then `commitguard install` |
| `global init.templateDir is already set to '...'; not changing it.` | `install --global` with an existing template directory | add the hooks to that template manually, or install per repository |
| doctor: `pre-push hook appears to have been modified` | the managed block's checksum no longer matches | `commitguard install` restores it |
| doctor: `pre-push enforcement disabled in configuration` | `enforcement.pre_push: false` | set it to `true` |
| doctor: `pre-push hook interpreter cannot run CommitGuard (...)` | the embedded interpreter cannot import CommitGuard | `commitguard install` |

A commit or push that was not checked at all usually means `--no-verify` was
used, or hooks are missing (`commitguard doctor`). This is expected: hooks are
bypassable by design ([local.md](local.md)).

## GitHub Actions

The check prints, on every error:

```text
CommitGuard could not verify repository policy.
Reason: <message>
Security validation could not be completed.
Result: FAILED
```

| Reason | Cause | Fix |
|---|---|---|
| `base commit <sha> is not available in this clone. Fetch the full history (for actions/checkout: fetch-depth: 0).` (also `head`, `pushed`) | shallow checkout | `fetch-depth: 0` on `actions/checkout` |
| `pull_request_target is not supported: use the pull_request event (CommitGuard needs no secrets or write access)` | wrong trigger | use `pull_request` |
| `unsupported GitHub event '...' (supported: pull_request, push, merge_group)` | workflow triggered by another event | limit the workflow's `on:` |
| `GitHub event name missing (GITHUB_EVENT_NAME / --event-name)`, `GitHub event payload missing (GITHUB_EVENT_PATH / --event-path)` | `commitguard ci github` run outside Actions | pass `--event-name` and `--event-path` |
| `configuration file <path> does not exist at trusted revision <sha>` | the `config` input names a file that exists only in the pull request | policy is read from the trusted commit; merge the file first |
| `configuration file <path> requested but no trusted revision exists` | initial push with a `config` input | push without the input first, or accept built-in defaults |
| `revision range selects more than <n> commits` / `more than <n> commits would need to be analysed` | more commits than `max-commits` | raise `max-commits` deliberately |
| `--fail-on must be block or warn` | `fail-on: allow` | use `block` or `warn` |

Other symptoms:

- **The check fails but the pull request can still be merged.** Branch
  protection or a ruleset does not require `commitguard`. CommitGuard cannot
  configure this ([github-actions.md](github-actions.md#set-up-in-a-repository)).
- **`commitguard` is not offered as a required check.** GitHub lists a check
  only after it has run once; the name is the job's `name:`.
- **A push check failed but the commits are on GitHub.** Push checks run after
  the push. Prevention needs required pull requests and the required check.
- **`commitguard github setup` reports `... is not pinned to a commit SHA`,
  `checkout without fetch-depth: 0`, `grants write access`, `references secrets`
  or `concurrency cancel-in-progress ...`.** Fix the workflow as the message
  says; each is a real weakness of that workflow.

## GitHub App: service does not start

`commitguard github serve` and the WSGI entry point read all settings at
start-up and exit with an error if one is wrong. Errors name the variable,
never its value.

| Message | Fix |
|---|---|
| `COMMITGUARD_APP_DATA_DIR is not set` / `COMMITGUARD_APP_DATA_DIR must be an absolute path` | set an absolute path |
| `COMMITGUARD_GITHUB_APP_ID is not set` / `COMMITGUARD_GITHUB_APP_ID must be a positive integer` | the numeric App ID from the App settings page |
| `COMMITGUARD_GITHUB_PRIVATE_KEY (or COMMITGUARD_GITHUB_PRIVATE_KEY_FILE) is not set` | set one of them |
| `set only one of COMMITGUARD_GITHUB_PRIVATE_KEY and COMMITGUARD_GITHUB_PRIVATE_KEY_FILE` (same for the webhook and client secrets) | remove one |
| `COMMITGUARD_GITHUB_PRIVATE_KEY_FILE points to a file that does not exist` / `... could not be read as a text file` | path or permissions for the service user |
| `GitHub App private key is malformed or encrypted (expected an unencrypted PEM RSA private key)` | use the `.pem` downloaded from the App settings, unmodified |
| `GitHub App private key must be an RSA key` / `must be at least 2048 bits` | generate a new key in the App settings |
| `the webhook secret must be at least 16 characters (use a long random value)` | set a longer secret on GitHub and in the file |
| `COMMITGUARD_APP_WORKERS must be an integer between 1 and 64` (similarly `_RETENTION_DAYS` 1-3650, `_MAX_COMMITS`) | fix the value |
| `state store unavailable (OperationalError)` | the database cannot be opened: permissions, full disk, missing directory, read-only filesystem (for systemd: `ReadWritePaths`) | see [runbook: database unavailable](../operations/runbook.md#database-unavailable) |
| `state store has an unsupported schema version` | the database was written by a newer CommitGuard | run the newer commit, or restore a backup taken before the upgrade ([production.md](production.md#upgrades-and-rollback)) |
| `COMMITGUARD_DASHBOARD_URL must use https (http is only accepted for localhost outside production)` | production requires `https://` | fix the URL; `COMMITGUARD_ENV=development` only for local work |
| `COMMITGUARD_DASHBOARD_URL must be an origin without a path` | a path was included | use `https://host[:port]` |
| `COMMITGUARD_GITHUB_CLIENT_ID is not set or invalid` | dashboard enabled without a client ID | copy the Client ID from the App settings |
| `COMMITGUARD_GITHUB_CLIENT_SECRET (or COMMITGUARD_GITHUB_CLIENT_SECRET_FILE) is not set or too short` | missing or shorter than 16 characters | generate a client secret in the App settings |
| `COMMITGUARD_DASHBOARD_STATIC_DIR must be an absolute path to a built dashboard (index.html)` | `web/dist` not built or wrong path | `npm ci && npm run build` in `web/` |
| `COMMITGUARD_NOTIFICATIONS_MODE must be off, deliver or test` | typo (WSGI entry point only) | fix the value |
| `COMMITGUARD_SMTP_SECURITY=none is only allowed for localhost outside production` | plain SMTP to a remote host | use `starttls` or `tls` |
| `COMMITGUARD_NOTIFICATION_SIGNING_KEY must be at least 32 characters` | short signing key | generate a longer random key |
| `<file>: a mandatory policy can only enforce policies; enabled: false is not allowed for ...` / `<file>: a mandatory policy cannot configure local hook enforcement` (or any configuration error prefixed with the file) | invalid `COMMITGUARD_APP_MANDATORY_POLICY_FILE` | fix the file; `commitguard github validate --offline` shows the same message |

## GitHub App: `commitguard github validate`

| Line | Cause | Fix |
|---|---|---|
| `✗ Git version: the GitHub App needs Git 2.45 or newer (found 2.39.2)` | old Git on the host | install Git 2.45+ |
| `✗ Git version: git is not available` | Git not on the service user's `PATH` | install Git or fix `PATH` |
| `! Private key: the key file is readable by other users (chmod 600)` | loose file mode | `chmod 600`, owned by the service user |
| `✗ GitHub authentication: GitHub API ... failed: HTTP 401 (unauthorized)` | App ID and key do not belong together, or the key was revoked; also a badly wrong system clock | check the App ID, generate a new key, check time synchronisation |
| `✗ GitHub authentication: ... no response (unavailable)` | no outbound HTTPS to `api.github.com` | firewall, DNS, `HTTPS_PROXY` |
| `✗ Required permissions: missing checks: write` (or others) | App permissions incomplete | edit the App's permissions |
| `! Least privilege: not needed by CommitGuard: ...` | extra permissions granted | remove them |
| `✗ Webhook events: not subscribed: pull_request, push` | App event subscriptions incomplete | subscribe to the events in [../github-app.md](../github-app.md#3-subscribe-to-events) |
| `! Re-runs and merge queue: not subscribed: ... (GitHub re-run requests and merge queue validation are off)` | optional events or **Merge queues: read** missing | add them if you need these features |
| `✗ Installation access: the App is not installed on any account` | not installed | install the App |
| `✗ Installation permissions: installation <id> (...) has not granted ...` | the account has not accepted updated permissions | an owner accepts the new permissions on GitHub |
| `✗ Installation access: installation <id> not found` | wrong `--installation-id` | take the ID from the installation's settings URL |
| `✗ Installation access: GitHub refused an installation token (installation suspended?)` | the installation is suspended | unsuspend it on GitHub |
| `- GitHub authentication: not attempted (fix the configuration above)` | an earlier line failed | fix that first |

## GitHub App: webhooks

Check GitHub's side in the App settings: **Advanced → Recent deliveries** shows
each delivery's response status and body. The service answers:

| Status and body | Cause | Fix |
|---|---|---|
| `401 {"error": "invalid webhook signature"}` | the secret configured on GitHub differs from the service's secret (also a trailing newline pasted into the GitHub field) | set the same value in both places, then redeliver |
| `401 {"error": "missing webhook signature"}` / `malformed webhook signature` | no or malformed `X-Hub-Signature-256` header: the App has no webhook secret on GitHub, or a proxy strips headers | set a secret on GitHub; forward headers unchanged |
| `400 {"error": "missing or invalid X-GitHub-Event header"}` (or `X-GitHub-Delivery`) | a proxy strips headers, or the request did not come from GitHub | forward headers unchanged |
| `415 {"error": "expected application/json"}` | a proxy changed `Content-Type`, or the request is not a GitHub App webhook | forward the body and headers unchanged |
| `411 {"error": "content length required"}` | chunked request without `Content-Length` after the proxy | configure the proxy to buffer request bodies |
| `413 {"error": "payload too large"}` | body over 25 MB, or the proxy's own limit | raise the proxy limit to at least 25 MB |
| `409 {"error": "delivery already received with different content"}` | the same delivery ID with a different body | a proxy or tool modified the body; investigate, it is audited as `webhook_rejected` |
| `429 {"error": "too many requests"}` | more than 600 webhook requests in a minute from one client address; behind a proxy all deliveries share the proxy's address | redeliver after the burst |
| `200 {"status": "duplicate"}` | the delivery ID was already processed | none; to force a new scan, push, reopen the pull request, or use **Re-run** / **Scan again** |
| `202 {"status": "queued"}` | accepted, scan queued | none |
| `500 {"error": "internal error"}` or `5xx` from the proxy | processing failed (for example database error) or the service is down; the delivery is recorded as `failed` | fix the cause, then redeliver the same delivery (it is processed again) |
| no response / timeout | service down, proxy misrouted, firewall | [runbook](../operations/runbook.md#webhook-failures-signature-failures-and-redelivery) |

`commitguard github webhook-test payload.json --event pull_request --signature
"sha256=..."` reproduces signature and payload validation locally with a
payload copied from **Recent deliveries**.

## GitHub App: scans and Check Runs

The Check Run's failure output reads "CommitGuard could not verify repository
policy" for errors. The scan's message (dashboard scan page, `scan_jobs.message`)
is one of:

| Message | Cause | Fix |
|---|---|---|
| `GitHub App installation suspended` / `GitHub App installation removed` | the stored installation state is not active | unsuspend or reinstall; **Sync** in the dashboard if GitHub is already correct |
| `GitHub rejected the App credentials (check the App ID and private key)` | key revoked or replaced | install the current key, restart |
| `GitHub App installation not found (the App may have been uninstalled)` | uninstalled | reinstall |
| `the installation cannot access this repository with the required permissions` / `the installation cannot access this repository` | repository not selected for the installation, or permissions not accepted | add the repository to the installation; accept permissions |
| `GitHub App installation is missing required permissions (...)` | token granted fewer permissions than required | accept the App's permissions on the installation |
| `fetching commits from GitHub timed out` | fetch exceeded 10 minutes (large repository, network) | check connectivity; the scan is retried automatically up to twice |
| `git fetch failed: ...` / `could not fetch commits from GitHub (...)` | network or GitHub failure | automatic retries; then re-run |
| `a commit to scan is not available on GitHub (...)` | the commit was force-pushed away or the ref deleted | push again |
| `the GitHub App needs Git 2.45 or newer (found ...)` | Git downgraded on the host | install Git 2.45+ |
| `CommitGuard monitoring is paused for this repository` | monitoring paused in the dashboard | resume monitoring if intended |
| `the pull request is no longer open` | closed before the scan ran | none |
| `scan abandoned after repeated attempts` | the job's 30-minute lease expired three times (process killed or hung during a scan) | check logs by `job_id`; then re-run |
| `.commitguard.yaml: invalid configuration: ...` | invalid configuration at the trusted commit; not retried automatically | fix it on the base branch, then re-run |

No check appears at all:

- the App is not subscribed to the event, or the repository is not in the
  installation (`commitguard github validate`);
- the delivery failed (see [webhooks](#github-app-webhooks));
- the event is ignored by design: tag pushes, branch deletions, `ping`;
- monitoring is paused for the repository;
- the scan is still queued: `/ready` shows `queue: saturated` or `workers: not running`.

## Dashboard

| Symptom | Cause | Fix |
|---|---|---|
| `/login?error=sign_in_failed` | the callback URL on GitHub does not match `COMMITGUARD_DASHBOARD_URL` + `/api/v1/auth/callback`, the sign-in expired, or it started in another browser (logged as `sign_in_failed` with a code) | fix the callback URL; start again |
| `/login?error=github_unavailable` | GitHub could not complete the sign-in | retry; check outbound HTTPS |
| `403 CSRF_FAILED` "The request origin is not allowed." | the proxy rewrites `Origin`, or the page is served from an origin not in `COMMITGUARD_DASHBOARD_URL` / `COMMITGUARD_DASHBOARD_ALLOWED_ORIGINS` | forward `Origin` unchanged; fix the origin settings |
| `403 CSRF_FAILED` "The request is missing a valid CSRF token." | the proxy strips `X-CSRF-Token` | forward it unchanged |
| `401 UNAUTHENTICATED` "Sign in to continue." | no or expired session; cookies blocked by a cross-site setup | sign in; serve the dashboard and API on the same site |
| a signed-in user sees no organization | not a member, or no GitHub access to the installation | `commitguard dashboard members grant ...`; the user signs in again |
| `no GitHub App installation is known for organization '...' (install the App, or let the service receive its installation event first)` | `members grant` before the service knows the installation | install the App and wait for the event, or redeliver it |
| `no CommitGuard state database in COMMITGUARD_APP_DATA_DIR` | wrong data directory for the CLI | use the service's `COMMITGUARD_APP_DATA_DIR` and user |
| newly granted repositories are missing | repository visibility is read from GitHub at sign-in | sign out and in again |

## Notifications

| Symptom | Cause | Fix |
|---|---|---|
| in-app notifications work, e-mail and webhooks never send; the organization notification settings report the channels as unavailable | `COMMITGUARD_NOTIFICATIONS_MODE` is not `deliver`, the SMTP host or signing key is missing, **or the service runs with `commitguard github serve`, which does not read notification settings** | run the WSGI entry point with the notification variables ([self-hosted.md](self-hosted.md#notifications)) |
| delivery `failed` with `smtp_authentication_failed`, `smtp_timeout`, `smtp_unavailable_...`, `smtp_<code>` | SMTP relay problem | fix credentials or connectivity; retryable failures are retried 1 m, 5 m, 30 m, 2 h |
| `recipient_refused` | the relay refused the address (permanent) | correct the recipient in **Settings → Notifications** |
| `webhook_dns_failed`, `webhook_timeout`, `webhook_unreachable_...`, `webhook_http_<status>` | receiver unavailable or rejecting | fix the receiver; 408, 425, 429 and 5xx are retried, other 4xx are permanent |
| `webhook_address_refused` | the endpoint resolves to a non-public address (permanent) | use a public HTTPS endpoint |
| `webhook_redirect_refused` | the endpoint redirected (permanent; redirects are never followed) | register the final URL |
| deliveries in `test` mode never arrive | `COMMITGUARD_NOTIFICATIONS_MODE=test` or `COMMITGUARD_ENV=test` records deliveries without sending | use `deliver` in production |

Failed deliveries are not re-sent automatically after the fifth attempt, and
there is no manual resend; later notifications are delivered once the channel
works. See [../notifications.md](../notifications.md#retries-and-failures).

## Logs

The service writes one JSON object per line to stderr with `ts`, `level`,
`logger`, `event` and correlation fields (`delivery_id`, `job_id`, `scan_id`,
`installation_id`, `repository`, `request_id`). Secrets are redacted.

With a systemd unit (here named `commitguard.service`):

```bash
journalctl -u commitguard.service -o cat | grep '"event": "webhook_rejected"'
journalctl -u commitguard.service -o cat | grep '"job_id": "<job id>"'
```

Useful `event` values: `service_started`, `webhook_rejected`, `webhook_invalid`,
`webhook_duplicate`, `webhook_redelivery_processed_again`,
`webhook_delivery_id_conflict`, `webhook_deliveries_abandoned`,
`worker_crashed_on_job`, `maintenance_failed`, `notifications_failed`,
`governance_task_failed`, `policy_propagation_failed`, `github_api_unavailable`,
`github_rate_limited`, `github_api_server_error`, `notification_delivery_failed`,
`retention_purge`, `http_internal_error`, `api_internal_error`, `sign_in_failed`.
