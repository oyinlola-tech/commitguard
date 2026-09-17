# 04 - Cybersecurity contribution

## Defence in depth, with honest labels on each layer

**IMPLEMENTED** and **TESTED**:

| Layer | Stops | Bypassable by the contributor? |
|---|---|---|
| Git hooks | accidents, immediately | **Yes** - seven ways, each measured |
| GitHub Actions check | every commit a pull request introduces | No (but only blocks merges when required) |
| GitHub App | the same, centrally, with a policy repositories cannot weaken | No |
| Branch protection | makes a failing check a merge blocker | GitHub's setting, outside CommitGuard |

The value is not that the top layer is unbreakable - it is breakable, and that is
published. It is that **a local bypass cannot reach a protected branch unnoticed**.

## Threat model and its evidence

**IMPLEMENTED**: a threat model with assets, actors, trust boundaries and a STRIDE
register, where **each mitigation names the test that covers it**
(`docs/security/threat-model.md`).

## Attacks actually attempted

**TESTED / OBSERVED** - 13 recorded experiments, each stating what was expected and
what was observed:

| Attack | Result |
|---|---|
| Forged, tampered and unsigned webhooks | HTTP 401; a correctly signed control accepted |
| Replayed and out-of-order deliveries | duplicate detected; newest result stands |
| Stale check re-run on an outdated commit | ignored; the failing check remains current |
| Merge queue: clean PR queued behind a dirty one | merge group check fails |
| Policy tampering in a pull request (disable, allow, delete) | still blocked |
| Mandatory policy versus a repository opt-out | still blocked |
| GitHub unreachable during a scan | `error`, never `success` |
| Permissions revoked mid-scan | `error`, 0 check runs |
| Database failure during a webhook | HTTP 500, 0 scans, redelivery processed later |
| Installation suspended | audit entry and critical notification |
| Local hooks: `--no-verify`, deleted, modified, redirected, fresh clone | succeed locally; caught server-side |

## Vulnerabilities found in the project's own code

**MEASURED.** Eight security-relevant defects, all found by this project's own
benchmarks and fuzzers before any user encountered them:

| Defect | Class | Found by |
|---|---|---|
| Symbol before a trailer key hid attribution | detection bypass | labelled dataset |
| Unicode letters/numbers before a key hid attribution | detection bypass | property-based fuzzing |
| Default-ignorable characters hid a key or alias | detection bypass | property-based fuzzing |
| Quadratic JWT redaction (4.6 s on 80 KB) | denial of service | ReDoS suite |
| Quadratic workflow `secrets.` scan (2.7 s on 60 KB) | denial of service | ReDoS suite |
| Slow normalisation (4.8 s for a 10 MB message) | denial of service | performance benchmark |
| Notifications never delivered under `github serve` | silent security-path failure | documentation review against code |
| Documentation pointing at an unrelated PyPI package | dependency confusion | Phase 10 review |

Each has a regression test; each detection bypass also has a new dataset version
and a **published failing benchmark run** recorded before the fix existed.

## Security practices

**IMPLEMENTED**: private vulnerability reporting, a documented response process
with severity definitions, incident response procedures, a supply-chain page that
lists its own gaps, and a CI pipeline review covering fork pull request safety
(no `pull_request_target`, no secrets, SHA-pinned actions, no script injection -
several asserted by tests).
