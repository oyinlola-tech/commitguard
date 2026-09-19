# Running the App service in production

> Status: **Experimental.** CommitGuard is pre-alpha (0.1.1).
> No external production deployments have been recorded yet.
> The App service and organization governance have been validated only by this
> repository's test suites, against an offline model of GitHub. There are no
> uptime figures, SLAs or support commitments.

This page states what an operator has to provide, the constraints the code
imposes, how data is kept and restored, and what has and has not been
validated. Setup is in [self-hosted.md](self-hosted.md).

## What the operator must provide

CommitGuard provides a single process that speaks plain HTTP. Everything else is
yours:

| Responsibility | What CommitGuard does | What you provide |
|---|---|---|
| TLS | nothing; the service never terminates TLS | a TLS-terminating reverse proxy or load balancer; certificates and renewal |
| Exposure | binds `127.0.0.1:8080` by default | forward only `/webhooks/github`, `/health`, `/ready`, and (with the dashboard) `/api/v1/` and `/`; body limit ≥ 25 MB |
| Process supervision | no daemonisation, no self-restart | systemd (or equivalent) with restart on failure, and a check that restarts or alerts when `GET /ready` returns `503` |
| Background work | worker, maintenance and notification threads in the same process | nothing extra, but see the [single-instance constraint](#single-instance-constraint) |
| Secrets | reads keys and secrets from files or variables, wraps and redacts them, never stores them | files readable only by the service user (mode `600`), or your secret manager's file mount; rotation procedures |
| Database | SQLite in WAL mode, created `0600` in a `0700` directory; migrates the schema at start-up | local persistent disk, regular backups, restore tests |
| Monitoring | `/health`, `/ready`, JSON logs on stderr, in-memory counters (not exposed over HTTP) | log collection with access control (logs contain repository names and SHAs), alerting |
| Time | uses the system clock for JWTs, token expiry, leases and retention | accurate time synchronisation |
| Outbound network | HTTPS to `api.github.com` and `github.com`; optional SMTP and notification webhooks | egress rules or an `HTTPS_PROXY` |
| Merge enforcement | publishes Check Runs | branch protection or rulesets that require `commitguard-app` |
| Upgrades | versioned schema migrations; refuses a database from a newer version | pinned source installs, pre-upgrade backups, a rollback plan |

## Single-instance constraint

Verified in the code:

- State lives in one SQLite file (`SqliteStateStore`), opened from
  `COMMITGUARD_APP_DATA_DIR` with a 10-second busy timeout. There is no network
  database implementation.
- The event queue is `InProcessEventQueue`, a Python `queue.Queue` inside the
  process. Other processes cannot see it.
- Every process that calls `start()` runs its own scan workers, maintenance
  loop (governance tasks, recovery, retention) and notification loop.
- Rate limits are counted per process.

Consequences:

- **Run exactly one service process per database**, on one host. The
  organization governance background tasks assume a single writer.
- Several hosts cannot share the service. Putting the SQLite file on a network
  filesystem is not supported. A shared database or external queue is
  **Planned**, not implemented.
- Scans, job claims and notification deliveries use leases and conditional
  updates in the database, so an accidental second process on the same host
  should not double-scan or double-send, but it duplicates maintenance work and
  receives webhooks into its own queue. Running several processes is not
  covered by a dedicated test.
- Capacity is bounded by one host: `COMMITGUARD_APP_WORKERS` (1-64 threads) and
  a queue of 10,000 job IDs (`/ready` reports `saturated` at 90%).
- Availability equals the availability of that one process. While it is down,
  GitHub's webhook deliveries fail and are **not** redelivered automatically;
  see [../operations/runbook.md](../operations/runbook.md#webhook-failures-signature-failures-and-redelivery).

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
| Organization policy versions (floors, author, reason, rollback lineage) | SQLite | kept, to explain historical scans; immutable (database triggers refuse updates and deletes) |
| Merge groups (SHAs, target branch, queued pull request numbers, state) | SQLite | destroyed groups purged after retention |
| Organization settings, repository onboarding and mode, repository groups and memberships | SQLite | current state, until changed; changes are audited |
| Group and repository policy versions, policy drafts, approvals (approver, fingerprint, note) | SQLite | kept; versions immutable (triggers) |
| Policy exceptions (rule, scope, action, reason, requester, approver, expiry) | SQLite | kept; rows cannot be deleted (trigger) |
| Staged rollouts and enrolled repository IDs | SQLite | kept |
| Organization rule versions (identity names, name prefixes, e-mail addresses and GitHub logins an administrator entered) | SQLite | kept; immutable (triggers) |
| Effective policy cache (resolved document and provenance per repository) | SQLite | replaced on every resolution |
| Policy simulations (draft document, parameters, aggregate result) | SQLite | finished simulations purged after retention |
| Bulk operations and items, scheduled scan runs | SQLite | finished ones purged after retention |
| Scan schedules | SQLite | until disabled (kept for history) |
| Installation synchronisation status, daily security metric snapshots (counts only) | SQLite | kept |
| Acknowledgements of security events (who, when, note) | SQLite | with their notification event |
| Notification events and inbox entries (type, severity, title and summary text, repository, occurrence count) | SQLite | `COMMITGUARD_NOTIFICATION_RETENTION_DAYS` (default 90) |
| Notification delivery records (channel, destination address or endpoint ID, status, attempts, failure code) | SQLite | with their notification event |
| Notification settings: organization e-mail recipients and webhook endpoint URLs | SQLite | until removed by an administrator |
| Members (GitHub user ID, login, role) | SQLite | until removed |
| Sessions (token hash, user agent, times) and the repositories GitHub reported at sign-in | SQLite | deleted at sign-out, revocation or expiry |
| Repository settings and enforcement evidence | SQLite | until the installation is purged |
| Repository mirrors (commits, trees, config blobs) | `mirrors/` | removed when a repository or installation is removed, or when unused for the retention period |

- **Never stored:** commit messages, file contents (apart from the CommitGuard
  configuration blobs inside mirrors), GitHub installation or user tokens,
  JWTs, keys, the webhook secret, the client secret, notification webhook
  signing secrets (derived on demand from the signing key), SMTP credentials,
  session tokens (only their hashes), or request headers. Notification e-mail
  goes to organization addresses an administrator entered; members' personal
  e-mail addresses are never read from GitHub or stored. Author and committer
  identities are stored only for commits that produced a finding, to explain
  the violation.
- **Purging:** the maintenance thread runs retention purging hourly.
- **Compliance evidence:** audit events and scan provenance follow
  `COMMITGUARD_APP_RETENTION_DAYS` (default 30). Organizations that need a
  longer audit trail must raise it; see
  [../compliance-reporting.md](../compliance-reporting.md#retention-decides-how-far-back-evidence-goes).

### Backups

The database is `$COMMITGUARD_APP_DATA_DIR/commitguard-app.sqlite3` in WAL mode
(with `-wal` and `-shm` files next to it while it is open). Copying only the
main file of a running database can produce an inconsistent copy. Use SQLite's
online backup, or stop the service first:

```bash
# online, with the sqlite3 command-line tool
sqlite3 /var/lib/commitguard/commitguard-app.sqlite3 \
    ".backup '/backup/commitguard-app-$(date +%Y%m%dT%H%M%S).sqlite3'"

# online, with Python's sqlite3 module only
python3 - <<'EOF'
import sqlite3, time
src = sqlite3.connect("/var/lib/commitguard/commitguard-app.sqlite3")
dst = sqlite3.connect(time.strftime("/backup/commitguard-app-%Y%m%dT%H%M%S.sqlite3"))
src.backup(dst)
dst.close()
src.close()
EOF

# verify a backup
sqlite3 /backup/commitguard-app-<timestamp>.sqlite3 "PRAGMA integrity_check;"   # ok
sqlite3 /backup/commitguard-app-<timestamp>.sqlite3 "SELECT value FROM meta WHERE key = 'schema_version';"
```

Backups contain repository names, identities of commits with findings, member
logins and audit history: store them with the same access control as the
database. Mirrors do not need backups; they are fetched again as needed.

### Restore

1. Stop the service.
2. Move the current `commitguard-app.sqlite3`, `commitguard-app.sqlite3-wal`
   and `commitguard-app.sqlite3-shm` aside (do not leave an old `-wal` file next
   to a restored database).
3. Copy the backup to `commitguard-app.sqlite3`, owned by the service user,
   mode `600`.
4. Start the service with a CommitGuard version whose schema version is equal
   to or newer than the backup's (an older version refuses the file).
5. Check `GET /ready`, then redeliver webhooks GitHub sent after the backup was
   taken ([../operations/runbook.md](../operations/runbook.md#database-unavailable)).

## Reliability behaviour

| Situation | Behaviour |
|---|---|
| Duplicate or replayed webhook | acknowledged; no second scan, notification or audit event |
| Webhook processing failed (database error, crash) | the event is recorded as failed; GitHub's redelivery of the same ID is processed again instead of being dropped |
| Merge group destroyed before its scan | the queued scan is cancelled; nothing is published |
| Re-run of an outdated commit's check | refused and audited; the newest execution keeps the check |
| Notification provider unavailable | the security decision and in-app notification stand; delivery is retried (1 m, 5 m, 30 m, 2 h), then marked failed and audited |
| Out-of-order events | a newer job owns the check; older scans cannot overwrite it |
| Process crash or restart | queued jobs and jobs whose 30-minute lease expired are re-queued; each job gets at most 3 attempts, then `error` |
| GitHub 5xx, network errors or timeouts | at most 3 attempts with exponential backoff, then the check fails closed |
| Rate limits | waits up to 60 s as instructed by GitHub, at most 3 attempts; longer waits fail the check instead of blocking a worker |
| Repository removed or App uninstalled | queued jobs cancelled, mirrors and cached tokens discarded, later events ignored |
| Permissions reduced | token requests fail; no check is published as success |

GitHub does not redeliver failed webhooks automatically. If the service was
down, redeliver the events from the App's delivery log, push again, or reopen
affected pull requests. After an infrastructure failure CommitGuard also
schedules up to two automatic retry executions (after 5 and 20 minutes) for
scans that are still current. [../recovery.md](../recovery.md) lists every
failure and its behaviour.

## Upgrades and rollback

There are no releases or tags; you upgrade from one pinned commit to another.

1. Read the commit log between your current and target SHA.
2. Back up the database ([Backups](#backups)).
3. Stop the service.
4. Install the target commit into the virtual environment:

   ```bash
   /opt/commitguard/venv/bin/python -m pip install --force-reinstall \
       "commitguard[app] @ git+https://github.com/oyinlola-tech/commitguard@<new-commit-sha>"
   ```

   Rebuild `web/dist` from the same commit if the dashboard is enabled.
5. Run `commitguard github validate`, start the service, check `GET /ready`.

Schema migrations:

- The database schema is versioned (`meta.schema_version`, currently 4) and
  migrated automatically at start-up inside one transaction. Migrations only add
  data; no policy version, scan or audit event is deleted.
- Schema 1 (Phase 5) → 2 adds the dashboard tables (existing repositories and
  audit events are backfilled with their organization); 2 → 3 adds scan
  executions, event processing status, merge groups, policy version lineage and
  immutability triggers, and notification tables (existing scans are backfilled
  as execution 1 of their logical scan, manual re-scans as later executions).
- Schema 3 → 4 (Phase 8) adds the organization governance tables (settings,
  onboarding, groups, scoped policy versions, drafts and approvals, exceptions,
  rollouts, simulations, bulk operations, scan schedules, the effective policy
  cache, organization rules, synchronisation status, metric snapshots,
  acknowledgements), `private`/`archived` repository flags, the governance
  record of each scan and an immutability trigger on audit events. Existing
  repositories are backfilled as `onboarded` in `enforce` mode, so enforcement is
  unchanged after the upgrade; organizations without Phase 8 policies resolve
  exactly the Phase 6/7 effective policy.
- **A database from a newer CommitGuard is refused, not modified**
  (`state store has an unsupported schema version`). Rolling back to a commit
  with a lower schema version therefore requires restoring the pre-upgrade
  backup, which loses changes made since. Compare before rolling back:

  ```bash
  git -C /opt/commitguard/src show <commit-sha>:src/commitguard/github/storage.py | grep '^SCHEMA_VERSION'
  ```

- Detection rules ship with the package, and the rules version (a hash) is
  recorded with every scan.

Procedure for a bad upgrade: [../operations/runbook.md](../operations/runbook.md#bad-release).

## Security checklist

- [ ] CommitGuard installed from a pinned commit of `oyinlola-tech/commitguard`, not from PyPI
- [ ] Private key file mode `600`, owned by the service user; not in the image or repository
- [ ] Webhook secret: long and random, stored as a file
- [ ] App permissions exactly: Checks write; Contents, Metadata and Pull requests read (plus Merge queues read only if used)
- [ ] Service listens on `127.0.0.1` behind TLS
- [ ] Data directory is not world-readable (CommitGuard creates it `0700`)
- [ ] `commitguard github validate` reports READY
- [ ] Branch protection or rulesets require `commitguard-app` on protected branches
- [ ] Logs are shipped somewhere access-controlled (they contain repository names and SHAs)
- [ ] Database backups taken, access-controlled, and a restore tested
- [ ] Dashboard: `COMMITGUARD_DASHBOARD_URL` is `https://` and `COMMITGUARD_ENV` is `production` (the default)
- [ ] Dashboard: client secret stored as a file, mode `600`; rotated if exposed
- [ ] Dashboard: first owner granted by numeric user ID; members reviewed in **Settings**
- [ ] Dashboard: `COMMITGUARD_DASHBOARD_ALLOWED_ORIGINS` unset unless a second origin is required
- [ ] Notifications: `COMMITGUARD_NOTIFICATIONS_MODE` is `deliver` only where real delivery is intended (staging uses `test`)
- [ ] Notifications: SMTP password and signing key stored as files, mode `600`
- [ ] Notifications: webhook endpoints reviewed in **Settings → Notifications**; each receiver verifies the signature and timestamp
- [ ] Notifications: organization e-mail recipients reviewed (they receive repository names and rule IDs)

## What has been validated, and what has not

Covered by this repository's tests (run them yourself; results depend on your
environment):

| Area | Tests |
|---|---|
| Webhook verification, deduplication, HTTP handling, CLI (`validate`, `webhook-test`) | `tests/integration/github/app/test_app_http.py`, `test_app_security.py`, `test_app_cli.py` |
| End-to-end scans with real Git repositories and an offline GitHub API model | `tests/integration/github/app/test_app_end_to_end.py` |
| Stale scans, replays, concurrent webhooks and workers | `tests/integration/github/app/test_app_concurrency.py` |
| Merge queues, re-runs, failed and abandoned event processing | `tests/integration/github/app/test_app_merge_queue_reruns.py` |
| Same decisions as the GitHub Action | `tests/integration/github/app/test_app_action_consistency.py` |
| Dashboard API, authorization, recovery, notifications, governance, tenant isolation, performance targets | `tests/integration/github/app/dashboard/` |
| Browser tests of the full stack against the offline GitHub model | `web` (`npm run e2e`) with `tests/e2e/dashboard_harness.py` |

Not validated by the project:

- operation against github.com (real webhooks, Checks API limits, fetch by SHA
  over HTTPS);
- WSGI servers such as gunicorn, reverse proxies and TLS configurations;
- real SMTP relays and notification receivers;
- several processes or hosts against one database;
- long-running operation, load beyond the test data sets, disk growth of
  mirrors for large repositories;
- platforms other than Linux for the App service;
- any external production deployment. No external production deployments have
  been recorded yet.
