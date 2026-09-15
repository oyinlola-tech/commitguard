# Recovery: what happens when something fails

CommitGuard's rule for every failure: **never report success for something it
could not verify**, and never lose a security decision it already made.

## States operators see

| State | Meaning | GitHub check |
|---|---|---|
| `PASS` | the scan completed and policy allowed every commit | success |
| `BLOCKED` | the scan completed and policy blocked a commit (a policy violation) | failure |
| `ERROR` | CommitGuard could not complete the validation (scan error) | failure / timed out when it can be published; otherwise absent, so a required check keeps blocking |
| `STALE` | superseded by a newer execution or commit; the newer one publishes | not updated |
| `UNKNOWN` | the evidence needed to state something (branch protection, merge queue) is not available | — |
| `AT RISK` | the GitHub App lost access to a repository: checks no longer run, whatever the last result was | — |

`BLOCKED` (policy violation), `ERROR` (scan error) and a failed notification
(`DELIVERY_ERROR`, shown as delivery status `failed`) are distinct everywhere:
in stored state, the API, the dashboard, audit events and metrics.

## Failure matrix

| Failure | What CommitGuard does | Operator action |
|---|---|---|
| **Webhook handling fails** (database error, crash mid-event) | the event record is marked `failed` and GitHub gets a 5xx. GitHub's redelivery of the same delivery ID is **processed again**, not dropped as a duplicate. A record stuck in `processing` for 10 minutes (process killed) is marked `failed` by maintenance. | redeliver from the App's *Advanced → Recent deliveries* page |
| **Same webhook delivered twice** | the second delivery is a duplicate: one scan, one notification, one set of audit events | none |
| **GitHub API unavailable or rate limited** during a scan | bounded retries with backoff inside the request; then the execution is `error` (failure kind `infrastructure` or `timeout`), and the check fails closed where it can be published. Maintenance schedules up to **2 automatic retry executions** (after 5 and 20 minutes) while the scan is still the newest for its pull request, branch or merge group, the installation is active and monitoring is on. | if GitHub stays down, re-run the check once it is back |
| **Scan crashes on every attempt** (worker killed, lease expires 3 times) | the execution becomes `error` ("scan abandoned after repeated attempts"), is audited (`scan_error`), and its check is completed as a failure when it exists | investigate logs by `job_id` |
| **Authorization or configuration error** | `error`, check fails closed; **not** retried automatically | fix permissions or `.commitguard.yaml`, then re-run |
| **Database unavailable** | the store raises; nothing is published as success. A scan that cannot store its result ends without a success check (fail closed); webhook handling returns 5xx and the event is processed on redelivery | restore the database, then redeliver or re-run |
| **Notification provider unavailable** | the scan result, violation, check and in-app notification already exist. The delivery is retried (1 m, 5 m, 30 m, 2 h) up to 5 attempts, then `failed`; every attempt is audited | fix the provider; later deliveries succeed; failed ones are visible in Settings → Notifications |
| **Installation removed or suspended** | state change, audit event and `installation_disconnected` notification in one transaction; queued scans are cancelled; repositories are **AT RISK** | reinstall or unsuspend; the reconnection is notified and audited |
| **Check re-run fails** | the new execution is `error`, the earlier executions are unchanged, `check_rerun_failed` is notified | see the execution's failure message |
| **Re-run of an outdated commit** | rejected (`check_rerun_rejected`): a newer commit has been scanned for that pull request or branch | re-run the newest commit's check |
| **Merge group becomes stale** (destroyed before or during scanning) | queued scan cancelled; a scan that has not started is `stale` and publishes nothing | none |
| **Older scan finishes after a newer one** | stored as history; it does not change violation state or the check (the newer execution owns it) | none |
| **Policy rollback fails** | one transaction: the active version is unchanged | retry; a `409` means someone else changed the policy |

## Idempotency keys

| Operation | Key |
|---|---|
| GitHub event | `X-GitHub-Delivery` (unique per event, reused by redelivery) plus the payload hash |
| Webhook-triggered scan | a fingerprint of event, pull request / ref, base and head |
| Re-run execution | the logical scan and the GitHub delivery ID (unique index); an execution already queued or running is returned instead of a new one |
| Manual scan / automatic retry | at most one queued or running execution per logical scan |
| Merge group | installation, repository and merge group SHA |
| Check Run publication | installation, repository, SHA and check name; the newest execution owns the slot |
| Notification | organization, domain key and coalescing window |
| Notification delivery | event, channel and destination |

## Correlation

Every log line and audit event carries the IDs that connect a security event
end to end:

```text
X-GitHub-Delivery (delivery_id) ─► job_id ─► scan_id ─► notification_event_id ─► audit events
dashboard X-Request-ID (request_id) ─► audit event ─► notification event (source_request)
```

Notification events store the `request_id`, `delivery_id` and `job_id` of the
change that produced them, and the `notification_created` audit event repeats
them.

## Background work

| Loop | Interval | Work |
|---|---|---|
| scan workers | continuous | queue → scan → Check Run |
| notifications | 5 s | dispatch the outbox, attempt due deliveries |
| maintenance | 60 s | re-enqueue abandoned jobs, mark stuck events failed, schedule automatic retries |
| retention | hourly | deliveries, jobs, check slots, audit events, merge groups (`COMMITGUARD_APP_RETENTION_DAYS`); notifications (`COMMITGUARD_NOTIFICATION_RETENTION_DAYS`) |

All loops use the database as the source of truth (claims, leases,
conditional updates), so a second process on the same host cannot double-send
or double-scan.

## Timeouts

| External operation | Bound |
|---|---|
| GitHub API request | 10 s per request, bounded retries |
| Git fetch into a metadata mirror | per-fetch timeout (`FetchTimeoutError` → `timeout`) |
| SMTP | 15 s |
| Webhook POST | 10 s, no redirects |
| SQLite busy wait | 10 s |
| Scan job lease | 30 minutes, 3 attempts |

## Metrics

`github_events_received`, `github_events_failed`, `github_events_replayed`,
`check_reruns`, `scan_retries`, `merge_groups_scanned`, `merge_groups_failed`,
`policy_rollbacks`, `policy_rollback_failures`, `notifications_created`,
`notifications_sent`, `notifications_failed`, `notification_retries`, plus the
Phase 5 scan and webhook counters. Labels never contain commit data, tokens or
destinations.
