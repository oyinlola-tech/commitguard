# Self-hosting the GitHub App service

> Status: **Experimental.** Self-hosting is the only way the CommitGuard GitHub
> App runs: there is no hosted service. The service is tested against an offline
> model of GitHub, not against github.com. Read [production.md](production.md)
> before relying on it.

This page covers running the App service and the dashboard it serves. Creating
and configuring the App on GitHub is in [../github-app.md](../github-app.md);
using the dashboard is in [../dashboard.md](../dashboard.md).

## Architecture

```text
                         GitHub
                           │
                     Webhook HTTPS
                           │
                           ▼
              ┌──────────────────────────┐
              │ TLS reverse proxy        │  nginx, Caddy, a cloud load balancer…
              └────────────┬─────────────┘
                           │ http://127.0.0.1:8080
                           ▼
              ┌──────────────────────────┐
              │ CommitGuard App process  │
              │  WSGI: /webhooks/github  │──▶ GitHub REST API (HTTPS, installation tokens)
              │        /health /ready    │
              │        /api/v1/...       │──▶ github.com OAuth (sign-in only)
              │        /  (web/dist)     │    dashboard, same origin
              │  GitHub client           │
              │  event queue (in-process)│
              │  scan workers (threads)  │──▶ git fetch (HTTPS, metadata only)
              │  maintenance thread      │    recovery, retention, governance tasks
              │  notification thread     │──▶ SMTP relay, notification webhooks (optional)
              │  CommitGuard core        │
              └────────────┬─────────────┘
                           │
                           ▼
              COMMITGUARD_APP_DATA_DIR
              ├── commitguard-app.sqlite3   deliveries · installations · scans · findings ·
              │                             violations · policy versions · members ·
              │                             sessions · governance · notifications · audit
              └── mirrors/<installation id>/<repository id>.git
```

There is **no separate worker process**. `GitHubAppService.start()` starts, in
the same process as the web server:

| Thread | Name | Interval | Work |
|---|---|---|---|
| scan workers | `commitguard-worker-<n>` | continuous | take job IDs from the in-process queue, scan, publish the Check Run |
| maintenance | `commitguard-maintenance` | 60 s | re-enqueue queued jobs older than 5 minutes and running jobs whose 30-minute lease expired; mark deliveries stuck in `processing` for 10 minutes as `failed`; schedule automatic retries; governance tasks; hourly retention purge |
| notifications | `commitguard-notifications` | 5 s | dispatch the notification outbox, attempt due deliveries |

The queue is only a wake-up signal. Every scan job is stored in `scan_jobs`
before its ID is queued, so a full queue, a crashed thread or a restart loses
nothing: queued and abandoned jobs are found again in the database.

No Redis, message broker, separate database server or Kubernetes is used. The
queue and storage sit behind interfaces (`EventQueue`; `SqliteStateStore`
implements the repositories), so a broker or another database could be added
later. **Planned**, not implemented: neither exists today.

## Requirements

- Linux. The App service has been tested on Linux only.
- Python 3.12+.
- Git **2.45 or newer** on the host (the service relies on `GIT_NO_LAZY_FETCH`
  and partial-clone fetches). `commitguard github validate` checks it.
- CommitGuard with the `app` extra, **installed from source** (see
  [Install](#install)).
- Outbound HTTPS to `api.github.com` and `github.com`, directly or through an
  `HTTPS_PROXY` you set. For notifications (optional): outbound SMTP to your
  relay and outbound HTTPS to the webhook endpoints administrators register.
- Inbound HTTPS from GitHub's webhook addresses to your reverse proxy. GitHub
  publishes its IP ranges through its meta API if you want to restrict them.
- A persistent, backed-up directory for `COMMITGUARD_APP_DATA_DIR` on a local
  filesystem, owned by a dedicated unprivileged user.
- SQLite with JSON functions (3.38 or newer; the version bundled with current
  Python releases qualifies).
- For the dashboard: Node.js 20.19+ **at build time only** (`web/`), the App's
  client ID and client secret, and a public HTTPS origin.

## Install

CommitGuard is **not published to PyPI**. The PyPI project named `commitguard`
is an unrelated project; `pip install 'commitguard[app]'` would install its code,
not this service. Install from this repository, pinned to a full commit SHA:

```bash
sudo useradd --system --home-dir /var/lib/commitguard --shell /usr/sbin/nologin commitguard
sudo install -d -m 700 -o commitguard -g commitguard /var/lib/commitguard
sudo install -d -m 750 -o root -g commitguard /etc/commitguard
sudo python3 -m venv /opt/commitguard/venv
sudo /opt/commitguard/venv/bin/python -m pip install \
    "commitguard[app] @ git+https://github.com/oyinlola-tech/commitguard@<commit-sha>"
/opt/commitguard/venv/bin/commitguard --version
```

The `app` extra adds only `cryptography` (JWT signing). Record the commit SHA
you installed; you need it to roll back ([production.md](production.md#upgrades-and-rollback)).

To build the dashboard, use a checkout of the **same** commit:

```bash
git clone https://github.com/oyinlola-tech/commitguard /opt/commitguard/src
git -C /opt/commitguard/src checkout <commit-sha>
cd /opt/commitguard/src/web && npm ci && npm run build     # writes web/dist
```

## Configuration

Every setting is an environment variable. Errors name the variable, never its
value. Prefer the `..._FILE` variants for secrets.

### App

| Variable | Required | Meaning |
|---|---|---|
| `COMMITGUARD_GITHUB_APP_ID` | yes | numeric App ID |
| `COMMITGUARD_GITHUB_PRIVATE_KEY_FILE` / `COMMITGUARD_GITHUB_PRIVATE_KEY` | one of them | PEM private key (file preferred); unencrypted RSA, 2048 bits or more |
| `COMMITGUARD_GITHUB_WEBHOOK_SECRET_FILE` / `COMMITGUARD_GITHUB_WEBHOOK_SECRET` | one of them | webhook secret, at least 16 characters |
| `COMMITGUARD_APP_DATA_DIR` | yes | absolute path for the database and mirrors (created `0700`; the database file `0600`) |
| `COMMITGUARD_APP_MANDATORY_POLICY_FILE` | no | mandatory policy applied to every repository; read at start-up |
| `COMMITGUARD_APP_WORKERS` | no | scan worker threads, 1-64 (default 2) |
| `COMMITGUARD_APP_RETENTION_DAYS` | no | retention of deliveries, finished jobs, audit events and unused mirrors, 1-3650 (default 30) |
| `COMMITGUARD_APP_MAX_COMMITS` | no | commits per scan before failing closed (default 10000) |

### Dashboard

Enabled when `COMMITGUARD_DASHBOARD_URL` is set; the service then refuses to
start unless the other dashboard settings are valid.

| Variable | Required | Meaning |
|---|---|---|
| `COMMITGUARD_DASHBOARD_URL` | to enable | public origin, `https://` in production |
| `COMMITGUARD_GITHUB_CLIENT_ID` | yes | the App's client ID |
| `COMMITGUARD_GITHUB_CLIENT_SECRET_FILE` / `COMMITGUARD_GITHUB_CLIENT_SECRET` | yes | client secret (16+ characters) |
| `COMMITGUARD_DASHBOARD_STATIC_DIR` | no | absolute path to the built `web/dist`; without it only the API is served |
| `COMMITGUARD_DASHBOARD_ALLOWED_ORIGINS` | no | comma-separated extra origins (explicit, never `*`) |
| `COMMITGUARD_ENV` | no | `production` (default), `development` or `test` |

### Notifications

| Variable | Default | Meaning |
|---|---|---|
| `COMMITGUARD_NOTIFICATIONS_MODE` | `off` | `off`: in-app only; `deliver`: send e-mail and webhooks; `test`: record, never send |
| `COMMITGUARD_SMTP_HOST`, `_PORT` (587), `_SECURITY` (`starttls`), `_USERNAME`, `_PASSWORD` / `_PASSWORD_FILE`, `_FROM` | - | SMTP relay |
| `COMMITGUARD_NOTIFICATION_SIGNING_KEY_FILE` / `COMMITGUARD_NOTIFICATION_SIGNING_KEY` | - | 32+ characters; derives each webhook endpoint's signing secret |
| `COMMITGUARD_NOTIFICATION_RETENTION_DAYS` | 90 | notification and delivery retention |

> **Known gap.** The notification variables are read only by the WSGI entry
> point `commitguard.github.app:wsgi_app_from_environment()`.
> `commitguard github serve` does not read them today and always runs with
> notifications `off` (in-app notifications still work; e-mail and webhook
> delivery do not). To deliver e-mail or webhooks, use
> [Option 2](#option-2-a-wsgi-server).

See [../notifications.md](../notifications.md) for channels, signing and retries.

## Verify before starting

Run as the service user with the same environment:

```bash
commitguard github validate --offline                     # local configuration only
commitguard github validate                               # authenticates to GitHub
commitguard github validate --installation-id 12345678    # also mints a token for one installation
```

`validate` exits 0 and prints `READY` (or `CONFIGURATION VALID (GitHub not
contacted)` with `--offline`) when everything required is in place, and exits
2 with `NOT READY` otherwise. It never prints the key, the secret, JWTs or
tokens.

## Option 1: built-in server behind a reverse proxy

`commitguard github serve` runs a threaded `wsgiref` server. Its docstring
describes it as intended for development and single-host deployments.

```bash
commitguard github serve --host 127.0.0.1 --port 8080
```

An example systemd unit (adapt the paths):

```ini
[Unit]
Description=CommitGuard GitHub App
After=network-online.target

[Service]
User=commitguard
Group=commitguard
Environment=COMMITGUARD_GITHUB_APP_ID=123456
Environment=COMMITGUARD_GITHUB_PRIVATE_KEY_FILE=/etc/commitguard/app.pem
Environment=COMMITGUARD_GITHUB_WEBHOOK_SECRET_FILE=/etc/commitguard/webhook-secret
Environment=COMMITGUARD_APP_DATA_DIR=/var/lib/commitguard
ExecStart=/opt/commitguard/venv/bin/commitguard github serve --host 127.0.0.1 --port 8080
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/commitguard
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Logs are JSON lines on stderr, so with this unit they go to the journal
(`journalctl -u <unit name>`).

## Option 2: a WSGI server

The service is a standard WSGI application. With a WSGI server installed
separately (it is not a CommitGuard dependency, and CommitGuard's tests do not
exercise one):

```bash
gunicorn --workers 1 --threads 8 --bind 127.0.0.1:8080 \
    'commitguard.github.app:wsgi_app_from_environment()'
```

This entry point loads the App, dashboard and notification settings and starts
the background threads.

Use **one worker process**. Each process starts its own scan workers,
maintenance thread and notification thread, and has its own in-process queue:
a webhook received by one process is scanned by that process's workers (another
process would only pick the job up through recovery after 5 minutes). Do not use
options that fork after the application is loaded (such as preloading), because
the background threads would not survive the fork.

## Reverse proxy

The service does not terminate TLS. The proxy should:

- terminate TLS;
- forward `POST /webhooks/github`; `GET /health` and `GET /ready` from your
  monitoring; and, when the dashboard is enabled, `/api/v1/` and every other
  path under `/` (the dashboard files);
- pass the `Origin`, `Cookie` and `X-CSRF-Token` headers through unchanged, and
  not rewrite `Content-Type` or `Content-Length` of webhook requests (the
  service requires `application/json` and a `Content-Length`);
- allow request bodies of at least 25 MB, GitHub's maximum payload size;
- apply timeouts (about 10 seconds is ample, because the service answers
  webhooks without scanning).

Rate limits are per process and keyed by the client address the service sees.
The service does not read `X-Forwarded-For`, so behind a proxy every webhook
counts against one address: at most 600 webhook requests per minute in total,
after which the service answers `429`.

## Dashboard

1. In the GitHub App settings, set the **Callback URL** to
   `https://<host>/api/v1/auth/callback` and generate a client secret.
2. Build `web/dist` from the same commit as the installed package (see
   [Install](#install)). It contains no configuration and no secrets.
3. Add to the service environment:

   ```ini
   Environment=COMMITGUARD_DASHBOARD_URL=https://commitguard.example.com
   Environment=COMMITGUARD_GITHUB_CLIENT_ID=Iv23li...
   Environment=COMMITGUARD_GITHUB_CLIENT_SECRET_FILE=/etc/commitguard/client-secret
   Environment=COMMITGUARD_DASHBOARD_STATIC_DIR=/opt/commitguard/src/web/dist
   ```

4. Grant the first organization owner:
   `commitguard dashboard members grant --organization <login> --user-id <id> --role owner`.
5. Optional: enable notification delivery (requires
   [Option 2](#option-2-a-wsgi-server), see the known gap above):

   ```ini
   Environment=COMMITGUARD_NOTIFICATIONS_MODE=deliver
   Environment=COMMITGUARD_SMTP_HOST=smtp.example.com
   Environment=COMMITGUARD_SMTP_FROM=commitguard@example.com
   Environment=COMMITGUARD_SMTP_PASSWORD_FILE=/etc/commitguard/smtp-password
   Environment=COMMITGUARD_NOTIFICATION_SIGNING_KEY_FILE=/etc/commitguard/notification-key
   ```

   Keep the signing key as stable as the endpoints: rotating it invalidates
   every receiver's secret. Administrators then add e-mail recipients and
   endpoints in **Settings → Notifications**.
6. Optional: for merge queue support, grant the App **Merge queues: read** and
   subscribe to **Merge group**; for GitHub's re-run buttons, subscribe to
   **Check run** and **Check suite**
   ([../merge-queue.md](../merge-queue.md),
   [../github-app.md](../github-app.md#3-subscribe-to-events)).

Frontend and API share one origin by design: the session cookie stays
first-party, `SameSite` protects writes, and no CORS is needed. To serve the
static files from another host, keep it on the same **site** (for example
`app.example.com` with the API on `api.example.com`), set
`COMMITGUARD_DASHBOARD_URL` to the origin users visit, and list any other
calling origin in `COMMITGUARD_DASHBOARD_ALLOWED_ORIGINS`. A different site
cannot use the `SameSite=Lax` session cookie and is not supported.

## Containers

The project does not ship container images or a Dockerfile (**Planned**, not
available). If you build your own image:

- run as a non-root user;
- mount the data directory as a volume on local storage;
- provide the private key and webhook secret as secret files (the `…_FILE`
  variables), not as build arguments or image layers;
- install CommitGuard from a pinned commit as above, never by package name.

## Health checks

| Endpoint | Healthy response | Checks |
|---|---|---|
| `GET /health` | `200 {"status": "ok"}` | the process answers |
| `GET /ready` | `200 {"status": "ready", "checks": {…}}` | `configuration`, `store` (database answers `SELECT 1`), `workers` (all background threads alive), `queue` (below 90% of 10,000 entries) |

`/ready` returns `503` with `{"status": "not_ready", "checks": {...}}`, where a
failing check reads `unavailable`, `not running` or `saturated`. Neither
endpoint reveals secrets, repository names or configuration values.

```bash
curl -fsS http://127.0.0.1:8080/ready
```

## Local development

```text
GitHub ──webhook──▶ HTTPS tunnel of your choice ──▶ commitguard github serve (127.0.0.1:8080)
```

Use a separate development App, a throwaway data directory and
`COMMITGUARD_ENV=development`. Details:
[../github-app.md](../github-app.md#local-development) and
[../dashboard.md](../dashboard.md#running-the-dashboard).

## Next

- [production.md](production.md): backups, retention, upgrades, rollback,
  security checklist, what has been validated.
- [troubleshooting.md](troubleshooting.md): error messages and fixes.
- [../operations/runbook.md](../operations/runbook.md): incident procedures.
