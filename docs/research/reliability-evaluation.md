# Reliability evaluation

The question here is not "does it work" but "what does it say when it cannot
work". A security tool that reports success when it has verified nothing is worse
than no tool.

**Recorded 2026-09-17.** Four failure modes, all `prevented`.

| Failure | Observed | What was *not* observed |
|---|---|---|
| GitHub API unreachable during a scan | job ends **error (infrastructure)**; check conclusions `[]` | no success conclusion |
| Installation permissions revoked | job ends **error (authorization)**; **0** check runs | no success conclusion |
| Database fails while a webhook arrives | **HTTP 500** `{"error": "internal error"}`; **0** scans processed; redelivery later `{"status": "queued"}`; after recovery the check is **success** | no check created while the database was down |
| Installation suspended | audit `installation_suspended`, notification `installation_disconnected`, **0** scans | no silent gap |

## The rule these enforce

**Fail closed, and say so.** Every path that cannot complete an evaluation ends in
`error` or a non-zero exit code. There is no code path that converts "we could not
check" into "this is fine":

- the CLI and hooks exit **2** on any error, and hooks block on exit 2;
- the GitHub Action fails the job rather than passing it;
- the App publishes a check run with conclusion `error`, never `success`;
- the dashboard shows explicit states (`PASS`, `BLOCKED`, `FAIL`, `ERROR`,
  `UNKNOWN`, `STALE`, `NOT CONFIGURED`, `DISCONNECTED`) instead of collapsing
  them into a green tick.

## Recovery

- **Events are recorded before they are processed**, so a crash between receiving
  and scanning does not lose the event; GitHub's redelivery is accepted and
  processed (`{"status": "queued"}` above).
- **Duplicate deliveries are detected** by delivery ID and payload digest, so a
  redelivery cannot produce two scans or two notifications.
- **Jobs are persisted before being queued**, so a restart resumes them rather
  than dropping them.

## Not tested

- **Real GitHub outages** (rate limiting, partial API degradation, 5xx storms):
  modelled in the fake GitHub, not observed against github.com.
- **Long-running stability**: no soak test exists. Memory behaviour is measured
  for a benchmark run (82 MiB peak), not over days.
- **Multi-instance behaviour**: the store is SQLite and the queue is in-process,
  so a single instance per database is the supported configuration. Running two
  is not tested and not recommended.
- **Disaster recovery**: backup and restore are documented
  ([../deployment/production.md](../deployment/production.md)) but a restore
  drill has not been recorded as evidence.
