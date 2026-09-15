# Deploying CommitGuard: GitHub App service and dashboard

This page covers running the App service and the dashboard it serves.
Creating and configuring the App on GitHub is in [github-app.md](github-app.md);
using the dashboard is in [dashboard.md](dashboard.md).

## Architecture

Production:

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
              │  CommitGuard core        │
              └────────────┬─────────────┘
                           │
                           ▼
              COMMITGUARD_APP_DATA_DIR
              ├── commitguard-app.sqlite3   deliveries · installations · scans · findings ·
              │                             violations · policy versions · members ·
              │                             sessions · audit
              └── mirrors/<installation>/<repository>.git
```

Development:

```text
Webhook server ─▶ in-process queue ─▶ worker      (commitguard github serve)
```

There are no other services: no Redis, no message broker, no separate database
server, no Kubernetes requirement. The queue and storage sit behind interfaces
(`EventQueue`; `DeliveryRepository`, `InstallationRepository`, `ScanRepository`,
`AuditStorage`), so a broker or PostgreSQL can be added later without changing
the services. Neither is included today.

## Requirements

- Python 3.12+ and `pip install 'commitguard[app]'`.
- Git 2.45 or newer on the host. The service relies on `GIT_NO_LAZY_FETCH` and
  partial-clone fetches.
- Outbound HTTPS to `api.github.com` and `github.com`, directly or through an
  `HTTPS_PROXY` you set.
- Inbound HTTPS from GitHub's webhook addresses to your reverse proxy. GitHub
  publishes its IP ranges through its meta API if you want to restrict them.
- A persistent, backed-up directory for `COMMITGUARD_APP_DATA_DIR`, owned by a
  dedicated unprivileged user.
- SQLite with JSON functions (3.38 or newer; the version bundled with current
  Python releases qualifies).
- For the dashboard: Node.js 20.19+ **at build time only** (`web/`), the App's
  client ID and client secret, and a public HTTPS origin.

## Dashboard

The dashboard is optional. It is enabled when `COMMITGUARD_DASHBOARD_URL` is
set, and the service then refuses to start unless the client ID and secret
are valid.

1. On GitHub, in the App settings: set the **Callback URL** to
   `https://<host>/api/v1/auth/callback` and generate a client secret.
2. Build the static files: `cd web && npm ci && npm run build`. Copy `web/dist`
   to the host; it contains no configuration and no secrets.
3. Add to the service environment:

   ```ini
   Environment=COMMITGUARD_DASHBOARD_URL=https://commitguard.example.com
   Environment=COMMITGUARD_GITHUB_CLIENT_ID=Iv23li...
   Environment=COMMITGUARD_GITHUB_CLIENT_SECRET_FILE=/etc/commitguard/client-secret
   Environment=COMMITGUARD_DASHBOARD_STATIC_DIR=/opt/commitguard/web/dist
   ```

4. Grant the first organization owner:
   `commitguard dashboard members grant --organization <login> --user-id <id> --role owner`.

Frontend and API share one origin by design: the session cookie stays
first-party, `SameSite` protects writes, and no CORS is needed. To serve the
static files from another host, keep it on the same **site** (for example
`app.example.com` with the API on `api.example.com`), set
`COMMITGUARD_DASHBOARD_URL` to the origin users visit, and list any other
calling origin in `COMMITGUARD_DASHBOARD_ALLOWED_ORIGINS`. A different site
cannot use the `SameSite=Lax` session cookie and is not supported.

## Option 1: built-in server behind a reverse proxy

Suitable for a single host with moderate traffic.

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

The reverse proxy should:

- terminate TLS;
- forward `POST /webhooks/github`; `GET /health` and `GET /ready` from your
  monitoring; and, when the dashboard is enabled, `/api/v1/` and the dashboard
  paths (everything else under `/`);
- pass the `Origin`, `Cookie` and `X-CSRF-Token` headers through unchanged;
- set a request body limit of at least 25 MB, GitHub's maximum payload size;
- apply timeouts (about 10 seconds is ample, because the service answers
  webhooks without scanning).

## Option 2: a production WSGI server

The service is a standard WSGI application. With a WSGI server installed
separately (it is not a CommitGuard dependency, and CommitGuard's tests do not
exercise one):

```bash
gunicorn --workers 1 --threads 8 --bind 127.0.0.1:8080 \
    'commitguard.github.app:wsgi_app_from_environment()'
```

Use **one worker process**. Each process starts its own scan workers and
maintenance thread. Several processes on one host can share the SQLite database
safely, but duplicated maintenance work is wasteful. Do not use server options
that fork after the application is loaded (such as preloading), because the
background threads would not survive the fork.

## Docker

The project does not ship container images or a Dockerfile yet. If you build
your own image:

- run as a non-root user;
- mount the data directory as a volume;
- provide the private key and webhook secret as secret files (the `…_FILE`
  variables), not as build arguments or image layers.

## Health checks

| Endpoint | Healthy response | Checks |
|---|---|---|
| `GET /health` | `200 {"status": "ok"}` | the process answers |
| `GET /ready` | `200 {"status": "ready", "checks": {…}}` | configuration loaded, database reachable, worker threads alive, queue below 90% |

`/ready` returns `503` with the failing check names. Neither endpoint reveals
secrets, repository names or configuration values.

## Data, backups and retention

| Data | Location | Retention |
|---|---|---|
| Webhook delivery IDs and payload digests | SQLite | `COMMITGUARD_APP_RETENTION_DAYS` |
| Installations and granted repositories (IDs, names) | SQLite | until removed; deleted installations are purged after retention |
| Scan jobs and results (SHAs, states, counts, rules and policy versions, effective policy, safe error text) | SQLite | finished scans purged after retention |
| Check Run ownership (repository, SHA, check name, run ID) | SQLite | retention |
| Audit events | SQLite | retention |
| Findings: rule, evidence value, commit SHA, author and committer of that commit | SQLite | retention, except findings of still-open violations |
| Violations and where they were detected | SQLite | resolved violations purged after retention; open ones kept |
| Organization policy versions (floors, author, reason) | SQLite | kept, to explain historical scans |
| Members (GitHub user ID, login, role) | SQLite | until removed |
| Sessions (token hash, user agent, times) and the repositories GitHub reported at sign-in | SQLite | deleted at sign-out, revocation or expiry |
| Repository settings and enforcement evidence | SQLite | until the installation is purged |
| Repository mirrors (commits, trees, config blobs) | `mirrors/` | removed when a repository or installation is removed, or when unused for the retention period |

- **Never stored:** commit messages, file contents (apart from the CommitGuard
  configuration blobs inside mirrors), GitHub installation or user tokens,
  JWTs, keys, the webhook secret, the client secret, session tokens (only their
  hashes), or request headers. Author and committer identities are stored only
  for commits that produced a finding, to explain the violation.
- **Purging:** the maintenance thread runs retention purging hourly.
- **Backups:** back up the SQLite file (WAL mode; use `sqlite3 .backup` or stop
  the service first). Mirrors do not need backups: they are fetched again as
  needed.

## Reliability behaviour

| Situation | Behaviour |
|---|---|
| Duplicate or replayed webhook | acknowledged; no second scan |
| Out-of-order events | a newer job owns the check; older scans cannot overwrite it |
| Process crash or restart | queued jobs and jobs whose 30-minute lease expired are re-queued; each job gets at most 3 attempts, then `error` |
| GitHub 5xx, network errors or timeouts | at most 3 attempts with exponential backoff, then the check fails closed |
| Rate limits | waits up to 60 s as instructed by GitHub, at most 3 attempts; longer waits fail the check instead of blocking a worker |
| Repository removed or App uninstalled | queued jobs cancelled, mirrors and cached tokens discarded, later events ignored |
| Permissions reduced | token requests fail; no check is published as success |

GitHub does not redeliver failed webhooks automatically. If the service was
down, redeliver the events from the App's delivery log, push again, or reopen
affected pull requests.

## Upgrades

- Stop the service, upgrade the package, and start it again.
- The database schema is versioned and migrated automatically at start-up
  inside one transaction. Phase 5 databases (schema 1) are upgraded to schema 2
  (dashboard tables; existing repositories and audit events are backfilled
  with their organization). A database from a newer CommitGuard is refused
  rather than modified. Back up the database before upgrading.
- Detection rules ship with the package, and the rules version (a hash) is
  recorded with every scan.

## Security checklist

- [ ] Private key file mode `600`, owned by the service user; not in the image or repository
- [ ] Webhook secret: long and random, stored as a file
- [ ] App permissions exactly: Checks write; Contents, Metadata and Pull requests read
- [ ] Service listens on `127.0.0.1` behind TLS
- [ ] Data directory is not world-readable (CommitGuard creates it `0700`)
- [ ] `commitguard github validate` reports READY
- [ ] Branch protection or rulesets require `commitguard-app` on protected branches
- [ ] Logs are shipped somewhere access-controlled (they contain repository names and SHAs)
- [ ] Dashboard: `COMMITGUARD_DASHBOARD_URL` is `https://` and `COMMITGUARD_ENV` is `production` (the default)
- [ ] Dashboard: client secret stored as a file, mode `600`; rotated if exposed
- [ ] Dashboard: first owner granted by numeric user ID; members reviewed in **Settings**
- [ ] Dashboard: `COMMITGUARD_DASHBOARD_ALLOWED_ORIGINS` unset unless a second origin is required
