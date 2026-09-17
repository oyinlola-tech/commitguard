# Notifications

CommitGuard tells people when enforcement needs attention: a scan blocked
commits, organization policy changed or was rolled back, the GitHub App lost
access, a merge group was blocked, or a check re-run could not complete.

Notifications never make or change a security decision. They are produced
*after* a domain service has decided and stored something, and delivering them
can fail without affecting that decision.

```text
scan result / policy version / installation state
  └─ same database transaction ─► notification outbox (notification_events)
                                        │  dispatcher (background, every 5 s)
                                        ▼
                     in-app inbox rows ─┼─ e-mail delivery records ─┼─ webhook delivery records
                                        │  delivery worker (bounded retries)
                                        ▼
                                  SMTP relay        signed HTTPS POST
                                        │
                                        ▼
                                    audit log
```

Contents: [Types](#types) · [Channels](#channels) · [Preferences](#preferences) ·
[Deduplication](#deduplication) · [Retries and failures](#retries-and-failures) ·
[Webhook format and verification](#webhook-format-and-verification) ·
[Security](#security) · [Configuration](#configuration) · [Retention](#retention)

## Types

| Type | When | Severity | In-app recipients | Mandatory in-app | Default e-mail / webhook |
|---|---|---|---|:-:|---|
| `critical_violation` | a scan **blocked** a newly opened or reopened violation with severity `critical` | critical | `violations:read` | ✓ | on / on |
| `high_violation` | the same for severity `high` (AI attribution rules) | high | `violations:read` | | off / off |
| `policy_changed` | an administrator published an organization policy version | high (critical if it weakens) | `audit:read` | ✓ | on / on |
| `policy_rolled_back` | an administrator restored an earlier version | high (critical if it weakens) | `audit:read` | ✓ | on / on |
| `installation_disconnected` | an active installation was uninstalled or suspended | critical | `github:manage` | ✓ | on / on |
| `installation_reconnected` | a suspended installation was unsuspended, or the account installed the App again after removing it | low | `github:manage` | | on / on |
| `merge_queue_failure` | a merge group was blocked, or could not be validated (failed closed) | high | `scans:read` | | off / on |
| `check_rerun_failed` | a GitHub "Re-run" execution ended in `error` | medium | `scans:read` | | off / off |
| `violation_digest` | organizations with `aggregate_violation_alerts`: blocked violations of one rule across repositories, one per rule and hour | the violation's | `violations:read` | | on / on |
| `policy_approval_requested` | a policy draft was submitted for approval | high | `policies:approve` | | on / off |
| `policy_emergency_published` | a policy was published without the approval workflow | critical | `audit:read` | ✓ | on / on |
| `policy_rollout_failed` | a staged rollout was paused or rolled back by its safety thresholds | high | `policies:publish` | ✓ | on / on |
| `policy_propagation_failed` | the effective policy of repositories could not be resolved | high | `policies:publish` | ✓ | on / on |
| `exception_requested` | an exception needs approval | the rule's | `exceptions:approve` | | on / off |
| `exception_approved` | an exception became active | the rule's | `exceptions:read` | | on / on |
| `exception_expiring` | an active exception reaches a warning threshold (default 7, 3 and 1 days) | the rule's | `exceptions:read` | | off / off |
| `exception_ended` | an exception expired or was revoked | the rule's | `exceptions:read` | | off / on |
| `repository_unprotected` | GitHub stopped requiring the CommitGuard check on a repository that required it | high | `repositories:manage` | ✓ | on / on |
| `organization_settings_changed` | an administrator changed organization security settings | critical if a control was relaxed, otherwise medium | `audit:read` | | on / on |

Severity reuses CommitGuard's severity model. The bundled detectors classify
AI attribution as `high`, so with bundled rules violations produce
`high_violation`; `critical_violation` is produced only for findings a rule
reports as `critical`.

Phase 8 types are described with the features that produce them:
[policy-exceptions.md](policy-exceptions.md), [policy-rollouts.md](policy-rollouts.md),
[policy-inheritance.md](policy-inheritance.md) and
[security-posture.md](security-posture.md#alert-aggregation). With
`aggregate_violation_alerts`, per-pull-request violation notifications stay in
the dashboard inbox while e-mail and webhooks receive the hourly
`violation_digest` instead; mandatory types are never aggregated.

Only newly opened (or reopened) **blocked** violations notify. Re-scanning the
same commits, acknowledging a violation, `warn` findings and resolutions do not.

## Channels

| Channel | Recipients | Availability |
|---|---|---|
| **In-app** | eligible members of the organization, per user | always |
| **E-mail** | the organization's e-mail recipients, set by administrators (for example a security team list) | `COMMITGUARD_NOTIFICATIONS_MODE=deliver` and SMTP configured |
| **Webhook** | the organization's registered HTTPS endpoints | `deliver` and `COMMITGUARD_NOTIFICATION_SIGNING_KEY` configured |

E-mail is sent through `SmtpEmailProvider`, which works with any SMTP relay
(including those of SES, SendGrid or Resend). The notification domain depends
only on the `EmailProvider` interface.

CommitGuard does not store members' personal e-mail addresses: e-mail goes to
addresses an administrator deliberately added for the organization.

## Preferences

```text
built-in defaults (per type)
  + organization settings   notifications:manage, versioned, confirmation to turn anything off
  + personal preferences    each member, own inbox only
  = effective delivery
```

- **Organization settings** (Settings → Notifications, `notifications:manage`):
  per type, in-app on/off (not for mandatory types), e-mail on/off, webhook
  on/off; e-mail recipients; webhook endpoints. Saving names the version it was
  based on (`409 CONFLICT` if someone saved first). Turning a delivery off or
  removing recipients requires `confirm: true`, and the dialog lists exactly
  what stops.
- **Personal preferences** (every member): mute a non-mandatory type in your
  own inbox. They cannot change e-mail, webhooks or anyone else's inbox, so a
  viewer cannot silence security notifications for the organization.
- **Mandatory** in-app types reach every eligible member whatever either
  setting says.

All changes are audited (`notification_settings_changed`,
`notification_preferences_changed`; e-mail addresses are masked in audit data).

## Deduplication

One underlying event produces one notification.

1. **Idempotent emission.** Each event has a domain key. The outbox is unique
   on `(organization, key + coalescing window)`. A repeat of the same key
   inside the window updates the existing notification (occurrence count,
   newest text, back to unread and to the top of the inbox) and creates **no**
   new e-mail or webhook delivery.

   | Type | Domain key | Window |
   |---|---|---|
   | violations | installation, repository, pull request / branch / merge group, rule | 1 hour |
   | policy changed / rolled back | organization, new version | none |
   | installation disconnected / reconnected | installation / account | 1 hour |
   | merge queue failure | repository, merge group commit | none |
   | check re-run failed | execution | none |

   A pull request with twenty AI-attributed commits is one notification
   ("blocked 20 commit(s)"). Flapping suspend/unsuspend within an hour does not
   repeat alerts.
2. **Replay protection upstream.** A redelivered GitHub webhook is recognised
   by its delivery ID and never scanned twice (see [recovery.md](recovery.md)).
3. **Idempotent delivery.** Each delivery record has an idempotency key
   (event, channel, destination), unique in the database and sent with every
   attempt: `Message-ID` for e-mail, `X-CommitGuard-Delivery` for webhooks.

## Retries and failures

```text
pending ──claim (2 min lease)──► attempt ──► sent
                                   ├─ retryable failure (5 attempts max) ──► pending, retry after 1 m, 5 m, 30 m, 2 h
                                   └─ permanent failure or 5th attempt ──► failed
```

| Outcome | Examples | Result |
|---|---|---|
| retryable | SMTP unavailable or timeout, authentication failure, webhook timeout, HTTP 408/425/429/5xx | `pending` with `next_retry_at` |
| permanent | recipient refused, SMTP 5xx, webhook HTTP 3xx (redirects are never followed) or other 4xx, endpoint resolves to a non-public address | `failed` |
| cancelled | the endpoint was removed, or the channel is no longer configured | `cancelled` |

- Every attempt is recorded (`attempt_count`, `failure_code`,
  `last_attempt_at`, `next_retry_at`) and audited
  (`notification_delivered`, `notification_delivery_failed` with
  `retry_scheduled`). Administrators see the log in Settings.
- A delivery is claimed with a lease through a conditional update, so two
  workers or processes never send it at the same time. If a process dies after
  the provider accepted a message but before it was marked sent, the lease
  expires and the message is sent again **with the same idempotency key**:
  delivery is at-least-once.
- **An outage never changes security state.** The scan result, violation,
  GitHub check, policy version and in-app notification are already stored when
  delivery begins.

## Webhook format and verification

```http
POST /your/endpoint HTTP/1.1
Content-Type: application/json
User-Agent: CommitGuard-Notifications
X-CommitGuard-Event: policy_rolled_back
X-CommitGuard-Delivery: 3f0b...          (same value on every retry)
X-CommitGuard-Timestamp: 1788264000      (Unix seconds)
X-CommitGuard-Signature: v1=5c1e...      (hex HMAC-SHA256)

{"created_at":"...","details":{"new_version":14,"previous_version":13,"reason":"...","target_version":12},
 "id":"...","last_occurred_at":"...","occurrences":1,"organization":"octo-org","repository":null,
 "sent_at":"...","severity":"high","summary":"...","title":"...","type":"policy_rolled_back",
 "url":"https://commitguard.example.com/policies/1001"}
```

`<`, `>` and `&` in the JSON are escaped as `<`, `>`, `&`.

Verify every request:

```python
import hashlib, hmac, time

def verify(secret: str, headers, body: bytes, tolerance: int = 300) -> bool:
    timestamp = headers["X-CommitGuard-Timestamp"]
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > tolerance:
        return False  # stale: possible replay
    expected = "v1=" + hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, headers["X-CommitGuard-Signature"])
```

Then discard a delivery whose `X-CommitGuard-Delivery` you already processed.
`commitguard.notifications.channels.webhook.verify_signature` implements the
same check.

**Secrets.** Each endpoint has its own signing secret,
`whsec_` + HMAC-SHA256(signing key, endpoint ID). CommitGuard stores no
secret: it is derived when needed and shown once, when the endpoint is added.
To rotate, add a new endpoint and remove the old one. Rotating
`COMMITGUARD_NOTIFICATION_SIGNING_KEY` changes every endpoint's secret.

## Security

| Threat | Control |
|---|---|
| Notification IDOR | inbox rows belong to one user; every read checks the row's user, the member's current role for the type, the installation GitHub reported for the session and, for repository notifications, the repository GitHub reported. Anything else is 404. |
| Preference privilege escalation | personal preferences touch only the caller's inbox; organization settings need `notifications:manage`; mandatory types cannot be muted or disabled |
| Spoofed notifications | webhooks are signed with a per-endpoint secret over timestamp and body; receivers reject stale timestamps and repeated delivery IDs |
| Notification spam / duplicate attacks | domain keys, coalescing windows, unique outbox and delivery keys; replayed GitHub deliveries are duplicates; rate limits on settings and webhook changes (10/min) |
| Data exfiltration through webhooks | only administrators add endpoints; explicit confirmation and a sign-in from the last 15 minutes; HTTPS only; public addresses only in production, checked at send time with the connection pinned to the checked address (no DNS rebinding); no redirects; at most 10 endpoints |
| Template, header and HTML injection | text sanitised when the event is created (control characters visible, secrets redacted, bounded); plain-text e-mail through `EmailMessage` (no header injection); JSON with `<>&` escaped; the dashboard renders text only |
| Secret leakage | payloads never contain tokens, keys, session data or webhook secrets; provider credentials come from the environment or secret files and are registered for log redaction |
| Private repository data to the wrong people | in-app visibility follows GitHub access; e-mail and webhook destinations are chosen by the organization's administrators and are documented as such |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `COMMITGUARD_NOTIFICATIONS_MODE` | `off` | `off`: in-app only. `deliver`: send e-mail and webhooks. `test`: run the whole pipeline but record deliveries in memory, never send. `COMMITGUARD_ENV=test` always forces `test`. |
| `COMMITGUARD_SMTP_HOST` | — | SMTP relay (enables e-mail) |
| `COMMITGUARD_SMTP_PORT` | `587` | |
| `COMMITGUARD_SMTP_SECURITY` | `starttls` | `starttls`, `tls`, or `none` (localhost outside production only) |
| `COMMITGUARD_SMTP_USERNAME` | — | optional |
| `COMMITGUARD_SMTP_PASSWORD` / `COMMITGUARD_SMTP_PASSWORD_FILE` | — | optional; prefer the file |
| `COMMITGUARD_SMTP_FROM` | — | sender address (required with a host) |
| `COMMITGUARD_NOTIFICATION_SIGNING_KEY` / `..._FILE` | — | 32+ characters; enables webhooks |
| `COMMITGUARD_NOTIFICATION_RETENTION_DAYS` | `90` | notification and delivery history |
| `COMMITGUARD_DASHBOARD_URL` | — | links in e-mail and webhook payloads |

Invalid values stop the service at start-up with an error naming the variable,
never its value.

## Retention

The hourly maintenance purge deletes notification events (with their inbox
rows and delivery records) whose last occurrence is older than
`COMMITGUARD_NOTIFICATION_RETENTION_DAYS`, except events with a delivery still
pending, and removed webhook endpoints older than the same period. Audit events
about notifications follow the audit retention
(`COMMITGUARD_APP_RETENTION_DAYS`).

## Metrics

`notifications_created`, `notifications_sent`, `notifications_failed`,
`notification_retries` (labelled by type or channel only; no destinations or
content).
