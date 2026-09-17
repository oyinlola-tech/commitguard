# Operations runbook: CommitGuard GitHub App service

> Scope: the self-hosted GitHub App service and dashboard
> ([../deployment/self-hosted.md](../deployment/self-hosted.md)), which are
> **Experimental**. No external production deployments have been recorded yet,
> so these procedures are derived from the code and its tests, not from
> incident history. Local hooks and the GitHub Action have no service to
> operate; see [../deployment/troubleshooting.md](../deployment/troubleshooting.md).

Every command, endpoint, table, log event and audit type named here exists in
the code at the time of writing. Nothing in CommitGuard repairs branch
protection, re-sends failed notifications by hand, or redelivers GitHub
webhooks for you.

## Conventions

The examples assume the systemd unit from the self-hosted guide, named
`commitguard.service`, and this database path:

```bash
export COMMITGUARD_APP_DATA_DIR=/var/lib/commitguard
DB="$COMMITGUARD_APP_DATA_DIR/commitguard-app.sqlite3"
```

- **Database queries are read-only** (`sqlite3 -readonly`). Do not write to the
  database by hand: state changes carry audit events, notifications and
  invariants enforced by the service. Timestamps are Unix seconds; the queries
  convert them with `datetime(..., 'unixepoch')` (UTC).
- **Logs** are JSON lines on stderr; the `event` field names what happened:
  `journalctl -u commitguard.service -o cat | grep '"event": "<name>"'`.
- **Readiness:** `curl -fsS http://127.0.0.1:8080/ready` returns `200` or `503`
  with the checks `configuration`, `store`, `workers`, `queue`.
- **Dashboard API routes** are listed for reference. They require a signed-in
  session; writes also require an allowed `Origin` and the `X-CSRF-Token`
  header, so perform writes in the dashboard. Permissions per route:
  [../dashboard.md](../dashboard.md#api-reference).
- **CLI:** `commitguard github validate`, `commitguard github webhook-test`,
  `commitguard dashboard members list|grant|revoke`. Run them as the service
  user with the service's environment.
- Metrics counters exist only in process memory and are not exposed; use logs,
  the database and the dashboard.

Background work runs as threads inside the service process (scan workers,
maintenance every 60 s, notifications every 5 s). There is no separate worker
process and no external queue.

---

## GitHub App disconnected or installation suspended

**Symptoms**

- Dashboard overview shows **GitHub enforcement at risk**; repositories are
  `AT RISK`; an `installation_disconnected` notification was sent.
- New pull requests get no `commitguard-app` check. A required check stays
  pending, so merges are blocked rather than silently allowed.
- Scan messages `GitHub App installation suspended`, `GitHub App installation
  removed`, or `GitHub refused an installation token (installation suspended?)`.
- Webhooks for the installation are answered `202 {"status": "ignored"}`.

**Confirm**

```bash
sqlite3 -readonly "$DB" "SELECT installation_id, account_login, state,
  datetime(updated_at, 'unixepoch') FROM installations ORDER BY updated_at DESC;"

sqlite3 -readonly "$DB" "SELECT type, installation_id, datetime(occurred_at, 'unixepoch')
  FROM audit_events WHERE type IN ('installation_created', 'installation_removed',
  'installation_suspended', 'installation_unsuspended', 'installation_permissions_updated')
  ORDER BY occurred_at DESC LIMIT 20;"

sqlite3 -readonly "$DB" "SELECT installation_id, state, error,
  datetime(last_success_at, 'unixepoch') FROM installation_sync_status;"

commitguard github validate --installation-id <installation id>
```

Dashboard: the installations page (`GET /api/v1/github/installations`,
`GET /api/v1/github/installations/{installation_id}`). Installation `state` is
`active`, `suspended` or `deleted`; synchronisation `state` is `healthy`,
`syncing`, `degraded` or `failed` and is tracked separately.

**Actions**

1. Find out on GitHub who suspended or uninstalled the App and why (the
   organization's audit log on GitHub). Treat an unexplained change as a
   possible security incident ([Security incident](#security-incident)).
2. **Suspended:** an organization owner unsuspends the App in the
   organization's GitHub App settings.
   **Uninstalled** (`deleted`): install the App again. GitHub assigns a new
   installation ID; repository history stays attached through immutable
   repository IDs.
3. If the service missed the `installation` event (it was down, or the delivery
   failed), either redeliver that event from the App's **Advanced → Recent
   deliveries** page, or press **Sync repositories** on the installation
   (`POST /api/v1/github/installations/{installation_id}/sync`, `github:manage`).
   Sync reads the installation's current state and repositories from GitHub. It
   refuses an installation stored as `deleted` ("GitHub App installation removed").
4. Events received while the installation was suspended were ignored and did not
   queue scans. Re-scan what changed during the outage: push again, **Re-run**
   the check on GitHub, **Scan again** in the dashboard, or a bulk
   `schedule_scan` operation for many repositories.
5. If permissions were reduced, an owner accepts the App's requested permissions
   on GitHub; `commitguard github validate` names the missing ones.

**Verify**

- `installations.state` is `active`; an `installation_unsuspended` or
  `installation_created` audit event exists.
- `commitguard github validate --installation-id <id>` ends with `READY`.
- A test pull request receives `commitguard-app`; the overview no longer shows
  enforcement at risk.

---

## Worker unavailable

There is no separate worker service to restart. Scan workers are threads
(`commitguard-worker-<n>`) in the service process, fed by an in-process queue of
job IDs. Every job is stored in `scan_jobs` before its ID is queued, and the
maintenance thread re-queues jobs queued for more than 5 minutes and running
jobs whose 30-minute lease expired.

**Symptoms**

- `commitguard-app` checks stay `queued` or `in progress`.
- `/ready` returns `503` with `"workers": "not running"` or `"queue": "saturated"`.
- Logs show `worker_crashed_on_job`, `maintenance_failed` or no
  `scan_completed` events.

**Confirm**

```bash
systemctl status commitguard.service
curl -sS http://127.0.0.1:8080/ready

sqlite3 -readonly "$DB" "SELECT state, COUNT(*), datetime(MIN(updated_at), 'unixepoch')
  FROM scan_jobs WHERE state IN ('queued', 'running') GROUP BY state;"

# running jobs whose lease expired (abandoned by a crash or hang)
sqlite3 -readonly "$DB" "SELECT job_id, owner || '/' || name, attempts,
  datetime(lease_expires_at, 'unixepoch') FROM scan_jobs
  WHERE state = 'running' AND lease_expires_at < CAST(strftime('%s', 'now') AS REAL);"

journalctl -u commitguard.service -o cat --since '-1h' | grep -E '"event": "(worker_crashed_on_job|maintenance_failed|github_rate_limited|github_api_unavailable)"'
```

**Actions**

1. **`workers: not running`:** a background thread has exited. The service does
   not restart threads; restart the process:
   `systemctl restart commitguard.service`. At start-up the service re-queues
   every queued job and every running job with an expired lease.
2. **`queue: saturated`** (9,000 or more queued IDs) or a growing `queued`
   count with healthy threads: check the logs for `github_rate_limited` and
   fetch timeouts, which slow each scan. If the host has capacity, raise
   `COMMITGUARD_APP_WORKERS` (1-64) and restart. Jobs whose ID did not fit in
   the queue are still stored and are picked up by recovery.
3. **A job keeps crashing or hanging:** each job is claimed at most 3 times; it
   then ends as `error` with "scan abandoned after repeated attempts" and its
   check is completed as a failure. Look up its logs by `job_id`.
4. **More than one service process** against the same database (for example a
   WSGI server with several workers): reduce to one process
   ([../deployment/production.md](../deployment/production.md#single-instance-constraint)).

**Verify**

- `/ready` returns `200`.
- The `queued` count falls and `running` jobs complete; `scan_completed` log
  events appear.
- Stuck checks on GitHub reach `completed` (re-run any that remain, see
  [Stale scans](#stale-scans)).

---

## Database unavailable

**Symptoms**

- `/ready` returns `503` with `"store": "unavailable"`.
- Webhooks are answered `500 {"error": "internal error"}` (the event is
  processed again on redelivery); dashboard API calls fail.
- Start-up fails with `state store unavailable (<error type>)` or
  `state store has an unsupported schema version`.
- Logs show `http_internal_error`, `api_internal_error`, `maintenance_failed`.

Nothing is published as success while the database is unavailable: scans that
cannot store their result do not produce a passing check.

**Confirm**

```bash
curl -sS http://127.0.0.1:8080/ready
ls -la "$COMMITGUARD_APP_DATA_DIR"            # owner = service user; database mode 600
df -h "$COMMITGUARD_APP_DATA_DIR"
sqlite3 -readonly "$DB" "PRAGMA quick_check;"                                    # ok
sqlite3 -readonly "$DB" "SELECT value FROM meta WHERE key = 'schema_version';"
journalctl -u commitguard.service -o cat --since '-1h' | grep -E 'state store|internal_error'
```

**Actions**

| Cause | Action |
|---|---|
| disk full | free space. Repository mirrors under `mirrors/` are re-fetched when needed; with the service stopped they can be removed to reclaim space. Then restart. |
| wrong owner or mode, read-only mount, systemd `ReadWritePaths` missing the data directory | fix ownership (service user), mode (`700` directory, `600` file) or the unit, then restart |
| `unsupported schema version` | the database was upgraded by a newer commit: run that commit again, or restore the backup taken before the upgrade ([Bad release](#bad-release)) |
| `quick_check` not `ok` (corruption) | stop the service, keep the damaged files for analysis, restore the latest good backup ([../deployment/production.md](../deployment/production.md#restore)) |
| transient lock contention (a long manual query, a second process) | stop the other writer; SQLite waits up to 10 seconds for a lock |

After the database is back:

1. Find deliveries that failed while it was unavailable:

   ```bash
   sqlite3 -readonly "$DB" "SELECT delivery_id, event, action, status, detail,
     datetime(updated_at, 'unixepoch') FROM deliveries
     WHERE status IN ('failed', 'processing') ORDER BY updated_at DESC LIMIT 100;"
   ```

   Deliveries that never reached the database (service down) are not listed;
   use the time window in GitHub's **Recent deliveries**.
2. Redeliver them from GitHub ([Webhook failures](#webhook-failures-signature-failures-and-redelivery)).
3. After a restore, everything since the backup is lost: redeliver or re-scan
   that period, and re-check policy changes, exceptions and member changes made
   since the backup in the dashboard audit log.

**Verify**

- `/ready` returns `200`; `PRAGMA quick_check` returns `ok`.
- Redelivered events show `202` on GitHub and `processed` in `deliveries`.
- A test pull request receives `commitguard-app`.

---

## Policy propagation failure

**Symptoms**

- A `policy_propagation_failed` notification and audit event.
- **Policies → propagation** does not reach complete; repositories show `error`.
- Logs: `policy_propagation_failed` (with `repository_id` and `error_type`), or
  `governance_task_failed` with `task` `propagation`.

Enforcement stays fail-closed: a scan whose cached effective policy is not
`up_to_date` resolves the policy directly, and fails (no success check) if that
fails too.

**Confirm**

```bash
sqlite3 -readonly "$DB" "SELECT account_id, state, COUNT(*) FROM repository_effective_policies
  GROUP BY account_id, state;"

sqlite3 -readonly "$DB" "SELECT account_id, repository_id, state, attempts, error,
  datetime(invalidated_at, 'unixepoch') FROM repository_effective_policies
  WHERE state = 'error' OR (state IN ('stale', 'syncing') AND attempts > 0)
  ORDER BY invalidated_at LIMIT 50;"

journalctl -u commitguard.service -o cat --since '-1h' | grep -E '"event": "(policy_propagation_failed|governance_task_failed)"'
```

Dashboard API: `GET /api/v1/organizations/{organization_id}/policy-propagation`
(counts per state, repositories in error) and
`GET /api/v1/repositories/{repository_id}/effective-policy`.

**Actions**

1. Use `error_type` and the row's `error` (`resolution failed (<type>)`) to
   find the cause. A database error follows
   [Database unavailable](#database-unavailable).
2. If a recent policy change is the cause, roll it back
   ([Policy rollback](#policy-rollback)).
3. The maintenance task retries a repository at most 5 times (`attempts`).
   After the cause is fixed, rows that reached 5 attempts are not picked up by
   the background task again. Resolve them directly: open the repository's
   **Effective policy** (the endpoint above) or scan the repository
   (**Scan again**). A successful resolution stores the row as `up_to_date` and
   resets `attempts` to 0.
4. Propagation only updates the effective policy. Existing GitHub checks change
   after the next scan: push, re-run, **Scan again**, a bulk `schedule_scan`
   operation, or a scan schedule.

**Verify**

- `policy-propagation` reports `complete: true` (every repository `up_to_date`).
- No `policy_propagation_failed` log events in the next maintenance passes.
- The repository's effective policy page shows the latest scan used the current
  effective policy.

---

## Webhook failures, signature failures and redelivery

**Symptoms**

- In the GitHub App settings, **Advanced → Recent deliveries** shows failures:
  `401`, `4xx`, `5xx` or timeouts.
- No checks on new pull requests or pushes.
- Logs: `webhook_rejected` (with `status` and `reason`), `webhook_invalid`,
  `webhook_delivery_id_conflict`, `webhook_deliveries_abandoned`.

**Confirm**

```bash
journalctl -u commitguard.service -o cat --since '-1h' | grep -E '"event": "webhook_(rejected|invalid|delivery_id_conflict|deliveries_abandoned)"'

sqlite3 -readonly "$DB" "SELECT status, COUNT(*) FROM deliveries
  WHERE received_at > CAST(strftime('%s', 'now', '-1 day') AS REAL) GROUP BY status;"

# reproduce verification with a payload and header copied from Recent deliveries
commitguard github webhook-test payload.json --event pull_request --signature "sha256=<X-Hub-Signature-256>"
```

Signature, header and size failures are rejected before anything is stored:
they appear only in logs and on GitHub, not in `deliveries`. A verified delivery
is stored with `status` `processing`, then `processed`, `ignored` or `failed`.

**Actions**

| Finding | Action |
|---|---|
| `401 invalid webhook signature` for GitHub's deliveries | the secret on GitHub and the service's secret differ. Set the same value in both places; the service reads the secret at start-up, so restart it. Then redeliver. |
| `401` or `400` from addresses that are not GitHub's | treat as probing: restrict the webhook path at the proxy to GitHub's published hook addresses (GitHub meta API) and follow [Security incident](#security-incident) if it persists |
| `400 missing or invalid X-GitHub-Event header`, `411`, `413`, `415` | the reverse proxy alters requests; forward headers and body unchanged, body limit at least 25 MB |
| `429 too many requests` | more than 600 webhook requests per minute reached the process through one address (all traffic behind a proxy shares it); redeliver after the burst |
| `409 delivery already received with different content` | the body changed in transit or a delivery ID was reused; investigate (audited as `webhook_rejected`) |
| `500` / proxy `502`, `504`, timeouts | service or database unavailable: fix that first ([Worker unavailable](#worker-unavailable), [Database unavailable](#database-unavailable)) |
| status `processing` for a long time | the process died while handling it; maintenance marks it `failed` after 10 minutes (`webhook_deliveries_abandoned`) |

**Redelivery**

- GitHub does **not** redeliver failed webhooks automatically, and CommitGuard
  has no command that fetches missed events.
- Redeliver from **Advanced → Recent deliveries → Redeliver** in the App
  settings. A redelivery keeps the delivery ID. A delivery recorded as `failed`
  (or abandoned) is processed again; one recorded as `processed` or `ignored`
  is answered `200 {"status": "duplicate"}` and does not scan again.
- GitHub also offers REST endpoints to list and redeliver App webhook deliveries,
  authenticated as the App (JWT); they are GitHub features, not part of
  CommitGuard.
- Alternatives that produce a new event: push to the branch, close and reopen
  the pull request, **Re-run** the check (needs the **Check run** subscription),
  or **Scan again** in the dashboard.
- Delivery IDs older than `COMMITGUARD_APP_RETENTION_DAYS` are forgotten; a
  redelivery that old runs the scan again with the same result for the same SHA.

**Verify**

- Recent deliveries show `202` (or `200 duplicate` for already processed events).
- The rows are `processed` or `ignored` in `deliveries`.
- The expected `commitguard-app` checks exist.

---

## Stale scans

"Stale" covers three different situations. Only the first two need action.

| Situation | Meaning |
|---|---|
| check stuck in `queued` / `in progress` | the job has not finished |
| check result predates a policy change | the result was computed with an earlier effective policy |
| scan shown as `stale` in the dashboard | superseded by a newer commit or execution; the newer one owns the check. Expected. |

**Confirm**

```bash
# jobs waiting or running for more than 30 minutes
sqlite3 -readonly "$DB" "SELECT job_id, owner || '/' || name, check_name, state, attempts,
  datetime(updated_at, 'unixepoch') FROM scan_jobs WHERE state IN ('queued', 'running')
  AND updated_at < CAST(strftime('%s', 'now', '-30 minutes') AS REAL) ORDER BY sequence LIMIT 50;"

# recent errors and whether they are eligible for automatic retry
sqlite3 -readonly "$DB" "SELECT job_id, owner || '/' || name, failure_kind, message,
  trigger_kind, execution, datetime(completed_at, 'unixepoch') FROM scan_jobs
  WHERE state = 'error' ORDER BY completed_at DESC LIMIT 50;"

journalctl -u commitguard.service -o cat --since '-1h' | grep -E '"event": "(check_run_failure_not_published|check_rerun_rejected|scan_error)"'
```

Dashboard: the scan page and `GET /api/v1/scans/{scan_id}/executions`; the
repository's **Effective policy** shows whether the latest scan used the current
effective policy.

**Actions**

1. **Stuck queued or running:** follow [Worker unavailable](#worker-unavailable).
   A running job whose process died is recovered after its 30-minute lease.
2. **`error` with `failure_kind` `infrastructure` or `timeout`:** the service
   schedules up to 2 automatic retry executions (5 and 20 minutes after the
   failure, within 24 hours) while the scan is still the newest for its pull
   request, branch or merge group, the installation is active and monitoring is
   on (audited as `scan_retry_scheduled`). Wait, or re-run.
3. **`error` with `authorization` or `configuration`:** not retried
   automatically. Fix permissions or `.commitguard.yaml` on the base branch, then
   re-run.
4. **Re-scan:** **Re-run** on GitHub (a re-run of an outdated commit is refused,
   `check_rerun_rejected`), **Scan again** in the dashboard
   (`POST /api/v1/scans/{scan_id}/rescan`, `scans:trigger`), or for many
   repositories a bulk `schedule_scan` operation
   (`POST /api/v1/organizations/{organization_id}/bulk-operations`) or a scan
   schedule for default branches.
5. **Check left `in progress` on GitHub** with `check_run_failure_not_published`
   in the logs: the scan had already failed closed, but publishing its failure
   to GitHub also failed (the log's `error_type` says why). Fix the cause (for
   example GitHub connectivity or installation access), then re-run.

**Verify**

- The newest execution for the commit is `passed` or `failed`, and the GitHub
  check on the pull request's head SHA is `completed`.
- For policy changes: the scan records the current effective policy.

---

## Notification delivery failure

In-app notifications, scan results, violations and checks are stored before any
delivery is attempted; a delivery failure never changes security state.

**Symptoms**

- E-mail or webhook receivers get nothing; the notification delivery log in
  **Settings → Notifications** shows `failed` or long-`pending` deliveries.
- Logs: `notification_delivery_failed`, `notifications_failed` (the loop itself
  failed).

**Confirm**

```bash
# the notification loop is one of the threads counted by "workers"
curl -sS http://127.0.0.1:8080/ready

sqlite3 -readonly "$DB" "SELECT channel, status, failure_code, attempt_count,
  datetime(next_retry_at, 'unixepoch'), datetime(updated_at, 'unixepoch')
  FROM notification_deliveries WHERE status IN ('pending', 'failed')
  ORDER BY updated_at DESC LIMIT 50;"

journalctl -u commitguard.service -o cat --since '-1h' | grep -E '"event": "(notification_delivery_failed|notifications_failed)"'
```

`notification_deliveries.destination` holds e-mail addresses or endpoint IDs;
avoid copying it into tickets. Dashboard API:
`GET /api/v1/organizations/{organization_id}/notification-deliveries`
(`notifications:manage`).

**Actions**

1. **No deliveries are created at all:** check how the service runs. Only the
   WSGI entry point `commitguard.github.app:wsgi_app_from_environment()` reads
   `COMMITGUARD_NOTIFICATIONS_MODE` and the SMTP and signing key variables;
   `commitguard github serve` always runs with notifications `off`. Also check
   that the mode is `deliver` (not `off` or `test`, and `COMMITGUARD_ENV` is not
   `test`) and that recipients or endpoints are configured.
2. **Retryable failures** (`smtp_timeout`, `smtp_authentication_failed`,
   `smtp_unavailable_...`, `webhook_timeout`, `webhook_dns_failed`,
   `webhook_unreachable_...`, `webhook_http_408/425/429/5xx`): fix the relay or
   receiver. Deliveries are retried 1 minute, 5 minutes, 30 minutes and 2 hours
   after failures, at most 5 attempts.
3. **Permanent failures** (`recipient_refused`, `webhook_address_refused`,
   `webhook_redirect_refused`, other `webhook_http_4xx`, permanent `smtp_<code>`):
   correct the recipient or endpoint in **Settings → Notifications**.
4. Failed deliveries are not re-sent after the fifth attempt and there is no
   manual resend. Tell affected people out of band if a critical event was
   missed; the event remains in the in-app inbox.
5. Rotating the notification signing key invalidates every receiver's secret;
   rotate only with the receivers' owners ready.

**Verify**

- New deliveries reach `sent`.
- The receiver verifies the signature and timestamp
  ([../notifications.md](../notifications.md#webhook-format-and-verification)).

---

## Policy rollback

Use when a published policy change blocks legitimate work or weakens
enforcement. A rollback never deletes a version: it publishes a new version
whose document is the target version's, and historical scans keep the versions
they recorded.

**Symptoms**

- A spike of blocked checks or violations after a policy publication, or an
  unexpected weakening in the audit log.
- A staged rollout paused automatically by its safety thresholds.

**Confirm**

Dashboard: **Policies** → version history, diff and rollouts. API:

- `GET /api/v1/policies/{organization_id}/versions` and
  `GET /api/v1/policies/{organization_id}/diff`;
- `GET /api/v1/organizations/{organization_id}/policy-targets/{target_type}/versions?target_id=<id>`
  for `group` and `repository` targets;
- `GET /api/v1/organizations/{organization_id}/rollouts` and
  `GET /api/v1/rollouts/{rollout_id}`.

```bash
sqlite3 -readonly "$DB" "SELECT type, datetime(occurred_at, 'unixepoch') FROM audit_events
  WHERE type LIKE 'policy%' OR type LIKE 'organization_policy%'
  ORDER BY occurred_at DESC LIMIT 30;"
```

**Actions** (all need `policies:rollback`, a `reason` and `confirm: true`; a
rollback that weakens a floor also needs a sign-in within the last 15 minutes)

| What changed | Roll back with |
|---|---|
| a staged rollout in progress | **Rollback** on the rollout (`POST /api/v1/rollouts/{rollout_id}/rollback`), or **Pause** first (`POST /api/v1/rollouts/{rollout_id}/pause`, `policies:publish`) |
| the organization policy | **Version history and rollback** (`POST /api/v1/policies/{organization_id}/rollback` with `target_version`, `expected_current_version`, `reason`, `confirm`) |
| a group or repository policy | `POST /api/v1/organizations/{organization_id}/policy-targets/{target_type}/rollback?target_id=<id>` with `target_version`, `expected_current_version`, `reason`, `confirm` |
| the service mandatory policy file (`COMMITGUARD_APP_MANDATORY_POLICY_FILE`) | restore the previous file, run `commitguard github validate --offline`, restart the service (the file is read at start-up) |
| an exception | revoke it (`POST /api/v1/exceptions/{exception_id}/revoke`, `exceptions:revoke`) |

A `409 CONFLICT` means the active version changed since you loaded it: reload,
review the new state, and retry. If the rollback fails for any reason, the
active version is unchanged.

**Verify**

- A new version with `kind` `rollback` is active and names the restored version.
- Propagation completes ([Policy propagation failure](#policy-propagation-failure)).
- Re-scan affected pull requests so their checks reflect the restored policy
  ([Stale scans](#stale-scans), step 4).

---

## Security incident

Follow [../security/incident-response.md](../security/incident-response.md) for
roles, communication and disclosure. CommitGuard-specific containment steps:

| Exposure | Containment |
|---|---|
| App private key | generate a new private key in the GitHub App settings, install it at `COMMITGUARD_GITHUB_PRIVATE_KEY_FILE` (mode `600`), restart, confirm `commitguard github validate` is `READY`, then delete the old key on GitHub |
| webhook secret | set a new secret on GitHub and in `COMMITGUARD_GITHUB_WEBHOOK_SECRET_FILE`, restart; redeliver deliveries rejected during the switch |
| client secret (dashboard) | generate a new client secret on GitHub, replace `COMMITGUARD_GITHUB_CLIENT_SECRET_FILE`, restart, delete the old secret |
| notification signing key or SMTP password | replace the file and restart (WSGI entry point); a new signing key invalidates every receiver's secret |
| a member account | `commitguard dashboard members revoke --organization <org> --user-id <id>` (removing a user's last membership also ends their sessions); review `audit_events` for their actions |
| the host or the database | suspend the App installation on GitHub to stop all API access (required checks then keep merges blocked), rotate every credential above, restore from a known-good backup onto a clean host |
| unauthorised policy weakening | [Policy rollback](#policy-rollback), then review exceptions and member roles |

Preserve evidence before it ages out: audit events, deliveries and finished
scans are purged after `COMMITGUARD_APP_RETENTION_DAYS` (default 30) by the
hourly maintenance. Take a database backup
([../deployment/production.md](../deployment/production.md#backups)) and export
the service logs immediately.

```bash
sqlite3 -readonly "$DB" "SELECT event_id, type, installation_id, repository_id,
  datetime(occurred_at, 'unixepoch') FROM audit_events
  WHERE occurred_at > CAST(strftime('%s', 'now', '-7 days') AS REAL) ORDER BY occurred_at;"
```

---

## Bad release

There are no releases: a "release" is the commit SHA you installed. Rolling back
means reinstalling the previous pinned commit.

**Symptoms**

- Start-up failures, `/ready` failures, new errors or changed decisions right
  after an upgrade.

**Confirm**

```bash
/opt/commitguard/venv/bin/python -m pip freeze | grep -i commitguard   # shows the installed git URL and commit
sqlite3 -readonly "$DB" "SELECT value FROM meta WHERE key = 'schema_version';"
git -C /opt/commitguard/src show <previous-sha>:src/commitguard/github/storage.py | grep '^SCHEMA_VERSION'
```

**Actions**

1. Stop the service: `systemctl stop commitguard.service`.
2. **Same schema version** in both commits: reinstall the previous commit.

   ```bash
   /opt/commitguard/venv/bin/python -m pip install --force-reinstall \
       "commitguard[app] @ git+https://github.com/oyinlola-tech/commitguard@<previous-sha>"
   ```

3. **The upgrade raised the schema version:** the previous commit refuses the
   database (`state store has an unsupported schema version`). Restore the backup
   taken before the upgrade ([../deployment/production.md](../deployment/production.md#restore)),
   then reinstall the previous commit as above. Changes made after the upgrade
   are lost: redeliver or re-scan that period and re-apply policy, exception and
   member changes from the audit log.
4. If the dashboard is enabled, rebuild `web/dist` from the previous commit
   (`git -C /opt/commitguard/src checkout <previous-sha>`, then
   `npm ci && npm run build` in `web/`).
5. `commitguard github validate`, then `systemctl start commitguard.service`.

For the other deployment modes:

- **GitHub Action:** set `uses: oyinlola-tech/commitguard@<previous-sha>` back in
  each workflow.
- **Local hooks:** `pipx install --force "git+https://github.com/oyinlola-tech/commitguard@<previous-sha>"`,
  then `commitguard install` in each repository and `commitguard doctor`.

**Verify**

- `pip freeze` shows the previous commit; `/ready` returns `200`.
- `meta.schema_version` matches the installed commit's `SCHEMA_VERSION`.
- A test pull request receives the expected `commitguard-app` result.
