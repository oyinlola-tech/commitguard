# Deploying the CommitGuard GitHub App

This page covers running the App service. Creating and configuring the App on
GitHub is in [github-app.md](github-app.md).

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
              │  GitHub client           │
              │  event queue (in-process)│
              │  scan workers (threads)  │──▶ git fetch (HTTPS, metadata only)
              │  CommitGuard core        │
              └────────────┬─────────────┘
                           │
                           ▼
              COMMITGUARD_APP_DATA_DIR
              ├── commitguard-app.sqlite3   deliveries · installations · jobs · audit
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
- forward only `POST /webhooks/github`, and `GET /health` and `GET /ready`
  from your monitoring;
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
| Scan jobs (SHAs, states, counts, rule IDs, safe error text) | SQLite | finished jobs purged after retention |
| Check Run ownership (repository, SHA, check name, run ID) | SQLite | retention |
| Audit events | SQLite | retention |
| Repository mirrors (commits, trees, config blobs) | `mirrors/` | removed when a repository or installation is removed, or when unused for the retention period |

- **Never stored:** commit messages, author names or e-mail addresses, file
  contents (apart from the CommitGuard configuration blobs inside mirrors),
  tokens, JWTs, keys, the webhook secret, or request headers.
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
- The database schema is versioned. An unknown schema version fails at start-up
  rather than being silently migrated.
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
