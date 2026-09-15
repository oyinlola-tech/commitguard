# CommitGuard dashboard and control plane

The dashboard answers one question for the people responsible for a GitHub
organization: **what is CommitGuard protecting, what has it detected, what is
currently blocked, and why?**

It is a web application served by the same process as the GitHub App. It does
not detect anything or decide anything. Detection and policy run in the
CommitGuard core, the GitHub App stores each result, the `/api/v1` API exposes
it, and the dashboard explains it. GitHub enforces the result.

```text
CommitGuard core (detection · policy · ScanResult)
        │
        ▼
GitHub App worker ──► GitHub Check Run "commitguard-app"
        │
        ▼
ScanResultRecorder ──► state database (scans · executions · findings · violations · audit
        │                                   · policy versions · notification outbox)
        ▼
/api/v1 (authentication · authorization · tenant scope)
        │
        ▼
Dashboard (React, same origin)
```

Contents:

- [Who uses it](#who-uses-it)
- [Running the dashboard](#running-the-dashboard)
- [Sign-in and sessions](#sign-in-and-sessions)
- [Organizations, roles and permissions](#organizations-roles-and-permissions)
- [Tenant isolation](#tenant-isolation)
- [Scans](#scans)
- [Violations and their lifecycle](#violations-and-their-lifecycle)
- [Policies](#policies)
- [Rules](#rules)
- [Repository protection and enforcement status](#repository-protection-and-enforcement-status)
- [Security health](#security-health)
- [Audit log](#audit-log)
- [Notifications](#notifications)
- [GitHub installations](#github-installations)
- [API reference](#api-reference)
- [Security controls](#security-controls)
- [Frontend](#frontend)
- [Testing](#testing)
- [Performance](#performance)
- [Known limitations](#known-limitations)

## Who uses it

| Team | Situation | What they set up | What they use the dashboard for |
|---|---|---|---|
| Security and compliance | AI agents may assist but must not be credited as authors of production code; auditors want evidence | An organization floor of `block` for `ai_coauthor` and `ai_identity` | Blocked pull requests with evidence, open and critical violations, the policy history (who changed what, when, why) |
| Platform / developer experience | Hundreds of repositories; no appetite for a workflow file in each | The GitHub App on the organization; the `commitguard-app` check required by a ruleset | Which repositories are verified to require the check, which are not, where configuration is broken; installation sync |
| Open-source maintainers | Contributors use AI tools; the project wants clear human authorship | Built-in defaults (attribution blocks, bots warn) | The violation page linked from a failed check, with the exact trailer and fix |
| Regulated engineering | Provenance decisions must be explainable months later | Retention sized to the audit period | Scan detail: commit range, effective policy, organization policy version, rules version, CommitGuard version |

Roles map to these jobs: **viewers** read; **security managers** triage
violations and request re-scans; **admins** own policy and repository
monitoring; **owners** also manage members.

## Running the dashboard

The dashboard needs the GitHub App service ([github-app.md](github-app.md),
[deployment.md](deployment.md)) plus the App's OAuth client credentials.

1. In the GitHub App settings, note the **Client ID**, generate a **client
   secret**, and set the **Callback URL** to
   `https://<dashboard host>/api/v1/auth/callback`. Leave "Request user
   authorization (OAuth) during installation" off; users sign in from the
   dashboard. "Expire user authorization tokens" may stay on.
2. Build the dashboard once:

   ```bash
   cd web
   npm ci
   npm run build            # writes web/dist
   ```

3. Configure and start the service:

   ```bash
   export COMMITGUARD_DASHBOARD_URL=https://commitguard.example.com
   export COMMITGUARD_GITHUB_CLIENT_ID=Iv23li...
   export COMMITGUARD_GITHUB_CLIENT_SECRET_FILE=/etc/commitguard/client-secret
   export COMMITGUARD_DASHBOARD_STATIC_DIR=/opt/commitguard/web/dist
   commitguard github serve --host 127.0.0.1 --port 8080
   ```

4. Grant the first owner of an organization (logins can be renamed, so use the
   numeric GitHub user ID):

   ```bash
   gh api users/alice --jq .id          # 12345678
   commitguard dashboard members grant --organization octo-org --user-id 12345678 --role owner --login alice
   ```

   The owner of a *personal* installation is its owner automatically. After
   the first owner signs in, they manage other members under **Settings**.

Environment variables (in addition to the App's):

| Variable | Required | Meaning |
|---|---|---|
| `COMMITGUARD_DASHBOARD_URL` | yes, to enable the dashboard | Public origin, e.g. `https://commitguard.example.com`. Used for the OAuth callback and Origin checks. `https` in production. |
| `COMMITGUARD_GITHUB_CLIENT_ID` | yes | The GitHub App's client ID |
| `COMMITGUARD_GITHUB_CLIENT_SECRET` / `..._FILE` | yes | The client secret, or a file containing it (preferred) |
| `COMMITGUARD_DASHBOARD_STATIC_DIR` | no | Absolute path to `web/dist`; without it only the API is served |
| `COMMITGUARD_DASHBOARD_ALLOWED_ORIGINS` | no | Comma-separated extra origins allowed to call the API with credentials (explicit list, never `*`) |
| `COMMITGUARD_ENV` | no | `production` (default), `development` or `test` |

`development` and `test` additionally accept `http://localhost` and
`http://127.0.0.1` dashboard URLs and omit `Strict-Transport-Security`. Every
other control (cookies, CSRF, CORS, CSP, authorization) is identical in all
environments. Tests generate their own keys and secrets.

**Local development.** Run the service on port 8080 with
`COMMITGUARD_ENV=development` and `COMMITGUARD_DASHBOARD_URL=http://localhost:5173`,
then `npm run dev` in `web/`. Vite proxies `/api` and `/health` to the service,
so the browser sees one origin. `COMMITGUARD_API_URL` changes the proxy
target.

## Sign-in and sessions

Sign-in uses the GitHub App's user authorization (OAuth web flow). CommitGuard
never asks for a password or a personal access token.

1. `GET /api/v1/auth/login?return_to=/violations` stores a random `state`
   (bound to the browser by an `HttpOnly` cookie) and a PKCE verifier, then
   redirects to GitHub.
2. GitHub redirects to `/api/v1/auth/callback`. The state must match the
   cookie and an unexpired, unused record; the code is exchanged with the PKCE
   verifier.
3. With the user token CommitGuard reads `GET /user`,
   `GET /user/installations` and `GET /user/installations/{id}/repositories`,
   stores the lists with the new session, and **discards the token**.

| Property | Value |
|---|---|
| Cookie | `__Host-commitguard_session`, `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/` |
| Token | 256 random bits; the database stores only its SHA-256 |
| Absolute lifetime | 8 hours |
| Idle timeout | 2 hours |
| Re-authentication for weakening changes | sign-in within the last 15 minutes |
| Revocation | sign-out, or **Settings → Security** (any of your sessions) |
| `return_to` | same-origin application paths only; anything else becomes `/dashboard` |

An expired or revoked session answers `401 SESSION_EXPIRED`; the dashboard
redirects once to `/login?reason=expired&return_to=...` and never loops.
Changes to GitHub access take effect at the next sign-in; changes to
CommitGuard roles take effect on the next request.

## Organizations, roles and permissions

An **organization** (tenant) is a GitHub account with the App installed,
identified by its numeric account ID. Access to it requires **both**:

- a CommitGuard role in that organization, and
- GitHub listing the installation for the user at sign-in.

Repository-level data additionally requires that GitHub listed **the
repository** for the user. An admin without GitHub access to a private
repository does not see its scans or violations.

| Permission | Viewer | Security manager | Admin | Owner | Used by |
|---|:-:|:-:|:-:|:-:|---|
| `repositories:read` | ✓ | ✓ | ✓ | ✓ | Overview, repositories, installations |
| `scans:read` | ✓ | ✓ | ✓ | ✓ | Scans |
| `violations:read` | ✓ | ✓ | ✓ | ✓ | Violations |
| `policies:read` | ✓ | ✓ | ✓ | ✓ | Policies |
| `rules:read` | ✓ | ✓ | ✓ | ✓ | Rules |
| `violations:manage` | | ✓ | ✓ | ✓ | Acknowledge / remove acknowledgement |
| `scans:trigger` | | ✓ | ✓ | ✓ | Scan again |
| `audit:read` | | ✓ | ✓ | ✓ | Audit log, repository and installation activity |
| `policies:write` | | | ✓ | ✓ | Organization policy |
| `repositories:manage` | | | ✓ | ✓ | Pause/resume monitoring, refresh enforcement status |
| `github:manage` | | | ✓ | ✓ | Sync installation repositories |
| `members:read` | | | ✓ | ✓ | Members list |
| `members:manage` | | | | ✓ | Grant, change, remove roles |
| `notifications:read` | ✓ | ✓ | ✓ | ✓ | Notification center, own notification preferences |
| `policies:rollback` | | | ✓ | ✓ | Roll back organization policy |
| `notifications:manage` | | | ✓ | ✓ | Organization notification settings, e-mail recipients, webhooks, delivery log |

`policies:rollback` is checked separately from `policies:write`, so a future
custom role can edit policy without restoring old versions (or the reverse).
Which notifications a member receives depends on the type's permission; see
[notifications.md](notifications.md).

Member rules enforced by the server: the last owner cannot be removed or
demoted; nobody changes their own role; the owner of a personal installation
is implicit and cannot be edited.

## Tenant isolation

- Every read service takes an `AccessScope` built from the session: the
  installations whose account grants the permission, and the session's
  GitHub-reported repository list. Every SQL statement filters by both
  (`installation_id IN (SELECT value FROM json_each(?))` and
  `EXISTS (SELECT 1 FROM session_repositories ...)`).
- A resource outside the scope is **404 Not Found**, identical to a resource
  that does not exist. 403 means "you can see this, but your role cannot do
  that".
- Writes resolve the target inside the scope first, then check the permission
  for that resource's organization.
- Tests cover every read and write endpoint with two tenants
  (`tests/integration/github/app/dashboard/test_dashboard_authorization.py`).

## Scans

Each GitHub App scan job is a scan **execution** in the dashboard. Its
**result** comes from the stored job and the core's decision; the dashboard
never computes it. Executions of the same commits and check (a GitHub
"Re-run", **Scan again**, an automatic retry) share one logical scan and are
numbered; see [Executions](#executions).

| Result | Meaning | GitHub check |
|---|---|---|
| `queued` | waiting for a worker | queued |
| `running` | fetching metadata and evaluating | in progress |
| `pass` | policy allowed every commit | success |
| `warning` | allowed, with `warn` findings | success |
| `blocked` | policy result at or above `block` | failure |
| `error` | could not be evaluated (fails closed) | failure / timed out |
| `cancelled` | pull request closed, merge group removed, or monitoring paused | none / not updated |
| `stale` | superseded: a newer execution or newer commit owns the check | not updated (the newer execution publishes) |

| Trigger | Meaning |
|---|---|
| `push`, `pull_request`, `merge_group` | a GitHub webhook |
| `rerun` | GitHub "Re-run" / "Re-run all checks" on a CommitGuard check |
| `manual` | **Scan again** in the dashboard |
| `retry` | automatic recovery after an infrastructure failure ([recovery.md](recovery.md)) |

Merge group scans show **Failure source: merge queue** and the merge group
(target branch, queued pull requests); see [merge-queue.md](merge-queue.md).

Scan detail shows the commit range, timings, conclusion, every finding with
evidence and remediation, notices (for example a policy-weakening attempt),
statistics, a comparison with the previous completed scan of the same pull
request or branch, and **reproducibility metadata**: organization policy
version, effective policy fingerprint and entries, policy source, rules
version and CommitGuard version.

**Scan again** (`scans:trigger`) queues a new execution for exactly the
commits of the stored scan, with the **current** effective policy (a caller
cannot choose a historical policy). The repository, SHA and event come from
storage, never from the request, and the execution goes through the normal
worker path (GitHub authorization, trusted policy, check ownership). Only the
newest scan of a pull request or branch can be repeated, and while an execution
of the same scan is queued or running a second request is refused (`409`). The
page polls while a scan is queued or running, starting at 2 seconds and
backing off to 30 seconds, and announces the final result to screen readers.

### Executions

`GET /api/v1/scans/{id}/executions` lists every execution of the logical scan,
newest first, with trigger, result, commit, organization policy version,
effective policy fingerprint, rules version and duration. The newest execution
determines the current GitHub check; earlier executions are never modified.
When executions were evaluated under different policy or rules versions the
page says so, because their results are not directly comparable.

## Violations and their lifecycle

A **finding** is one detector result in one scan and is immutable history. A
**violation** is a finding whose policy action was `block` or `warn`, tracked
across scans by its fingerprint (detector, rule, commit SHA, evidence) within a
repository.

A violation is *exposed* where CommitGuard saw it:

- **Pull request exposure**: active while the newest completed scan of that
  pull request still contains the finding. Pull request scans cover the full
  `base..head` range, so absence means the commit left the pull request.
  Closing a pull request ends the exposure; merging moves it to the base
  branch; reopening an unchanged pull request restores it.
- **Merge group exposure**: active while the merge queue's merge group commit
  that contained the finding exists. A merged group's exposures move to the
  target branch; an invalidated or dequeued group's end.
- **Branch exposure**: push scans are incremental, so absence proves nothing.
  The exposure ends when a later push scan shows the commit is no longer
  reachable from the branch head (history rewritten), or when the branch is
  deleted. If reachability cannot be determined the exposure stays active.

| Status | Meaning | Set by |
|---|---|---|
| `open` | at least one active exposure: present in a monitored pull request or branch | CommitGuard |
| `acknowledged` | open, and a security manager recorded a review (optional note) | a user with `violations:manage` |
| `resolved` | no active exposure remains; the resolution text says why | CommitGuard only |

Acknowledging does **not** change enforcement: the GitHub check still fails
and the violation remains open until the commit is gone. There is no "mark
resolved" or "ignore" action. A violation that reappears is reopened and its
acknowledgement cleared. History (detections, scans, audit events) is never
deleted by a state change, only by retention. If a newer scan of the same pull
request or branch has already completed, an older scan's findings are stored
as history but change no exposure.

Evidence is limited to the commit metadata that triggered the finding
(trailer value, identity), truncated and secret-redacted. The author and
committer identities of commits **with findings** are stored for the detail
page; commit messages and file contents are not stored.

## Policies

```text
service policy       COMMITGUARD_APP_MANDATORY_POLICY_FILE (operator), optional
 + organization policy  edited in the dashboard, versioned
 + repository policy    .commitguard.yaml at the trusted revision (the base commit)
 = effective policy     recorded with every scan
```

The organization policy is a set of **floors**: for a rule it can require at
least `warn`, always `block`, or leave the repository to decide. It is applied
with the same code as the service policy, so a repository configuration that
disables or lowers the rule is still evaluated at the floor. A floor of
`allow` does not exist: it would weaken nothing and would suggest an override
that CommitGuard does not implement.

Saving a policy:

1. The dashboard asks the server for a preview (`POST /policies/{id}/preview`),
   which classifies every change. Classification is never done in the browser.
2. **Weakening** changes (removing or lowering a floor) show a confirmation
   dialog with the current and new value of each rule, a required reason, and
   an "I understand" checkbox. The server also requires `confirm_weakening`, a
   reason and a sign-in within 15 minutes (`401 REAUTHENTICATION_REQUIRED`).
3. The update names the version it was based on. If another admin saved first,
   the server answers `409 CONFLICT` and nothing is overwritten.
4. The new version row, its `organization_policy_changed` audit event and the
   `policy_changed` notification are written in one transaction.

Versions are immutable (database triggers refuse updates and deletes). Each
scan stores the organization policy version and the effective policy it was
evaluated with, so historical scans keep showing the policy that produced
them.

**Version history and rollback.** The history lists every version (newest is
`ACTIVE`, older ones `ARCHIVED`) with its author, time, reason, a summary of the
changes against the previous version, and rollback lineage. **Compare** shows
a structured diff (added, changed, removed, weakening) from the active version.
**Roll back to vN** (`policies:rollback`) opens a dialog with the current and
target versions, the impact diff, a required reason and an acknowledgement; it
publishes a *new* version that restores vN's document. See
[policy-management.md](policy-management.md).

## Rules

Rules are bundled with the installed package and trusted: the same rule IDs
(`ai_coauthor`, `ai_identity`, `ai_trailer`, `malformed_trailer`,
`bot_identity`) appear in the CLI, hooks, Action, App and dashboard. The rules
page shows each rule's detector, severity, default action, rules version, data
files and remediation. Nothing is editable: there are no repository-defined
rules. A unit test checks the catalogue against the registered detectors,
built-in policies and the severities the detectors emit.

## Repository protection and enforcement status

Protection is never inferred from the App being installed.

| Protection | Rule |
|---|---|
| `at_risk` | the GitHub App installation is suspended or removed, or the repository is no longer granted: CommitGuard checks no longer run, whatever the last scan said |
| `unprotected` | monitoring is paused, or GitHub showed that no CommitGuard check is required |
| `configuration_error` | the latest completed scan failed because the CommitGuard configuration is invalid |
| `protected` | GitHub showed that the default branch requires `commitguard-app` or `commitguard` |
| `unknown` | scanned by the App, but branch protection has not been verified |

The repository page shows the signals separately:

| Signal | Values | Evidence |
|---|---|---|
| GitHub App | `connected`, `suspended`, `disconnected` | installation state and granted repositories |
| GitHub Actions | `detected`, `not_detected`, `unknown` | workflow files on the default branch, read with Contents: read and inspected statically (never executed) |
| Required check | `required`, `not_required`, `unknown` | `GET /repos/{o}/{r}/rules/branches/{b}` and `GET /repos/{o}/{r}/branches/{b}` |
| Merge queue | `enabled`, `not_enabled`, `unknown` | a ruleset `merge_queue` rule on the default branch; a classically protected branch is `unknown` |
| CommitGuard check | latest completed scan result | stored scans |
| Local hooks | `not_verifiable` | a server cannot see developer machines |

`required` needs a CommitGuard context in a ruleset's required status checks or
in the branch's visible protection. `not_required` needs both endpoints to
answer and show no protection that could require it. A protected branch whose
required contexts are hidden without Administration permission (which
CommitGuard does not request) is `unknown`. Evidence is gathered when an admin
selects **Refresh enforcement status**; CommitGuard never changes GitHub
settings.

**Pause monitoring** (`repositories:manage`) stops scanning a repository
without touching GitHub. It requires confirmation, a reason and a recent
sign-in, cancels queued scans, and keeps history. If branch protection
requires the check, pull requests will wait for a check that never arrives;
the dialog says so.

## Security health

The overview shows explicit checks, not a score:

| Check | OK when | Attention when | Unknown when |
|---|---|---|---|
| GitHub integration | every visible installation is connected with required permissions | an installation is suspended or missing permissions | — |
| Merge protection | every monitored repository is `protected` | any repository is `at_risk`, or a monitored one is `unprotected` or `configuration_error` | none unprotected, some `unknown`, or none monitored |
| Critical violations | 0 open critical violations | ≥ 1 | — |
| Open violations | 0 open `block` violations | ≥ 1 | — |
| Scan reliability | 0 `error` scans in the period | ≥ 1 | — |
| Configuration | 0 repositories with `configuration_error` | ≥ 1 | — |

Counts are computed on the server from stored scans, violations and
enforcement evidence for the selected period (24 hours, 7 days, 30 days) and
organization. No number is estimated. When an installation is disconnected or
any repository is `at_risk`, the overview shows **GitHub enforcement at risk**
above everything else.

## Audit log

Audit events record security-relevant actions with an actor (`user`,
`github`, `system`), the organization, and where relevant the installation,
repository and commit. They are separate from application logs, which are
operational. The dashboard cannot edit or delete events; retention
(`COMMITGUARD_APP_RETENTION_DAYS`) removes old ones.

Recorded: sign-in, sign-out, session revocation; member role granted, changed,
removed; organization policy changed (old and new version, changes, weakening
flag, reason); violation opened, reopened, resolved, acknowledged,
acknowledgement removed; repository monitoring paused and resumed; enforcement
status checked; repositories synced; scan requested, queued, passed, failed,
error, cancelled; policy violation; security policy modification detected;
installation created, removed, suspended, unsuspended, permissions updated;
repositories added and removed; webhook rejected; authorization denied; pull
request merged.

Phase 7 adds: organization policy rolled back (previous, target and new
version, changes, reason, request ID); scan started (manual, re-run, retry),
scan retry scheduled; check re-run requested and rejected (with the reason);
merge group created, passed, blocked, scan failed, destroyed; notification
created, delivered, delivery failed (masked destination), read; notification
settings and personal preferences changed; notification webhook added and
removed. Every audit event of an API request carries its `request_id`.

## Notifications

The bell in the top bar and **Notifications** in the navigation show the
member's unread count (critical in a distinct colour; counts above 999 are
capped). The notification center filters by all, unread, critical, category
and archived; marking read, unread and archived affects only the member's own
inbox. Links go to the violation, scan, policy, repository or installation,
where authorization is checked again. **Settings → Notifications** holds
personal in-app preferences and, for `notifications:manage`, organization
delivery settings, e-mail recipients, signed webhooks and the delivery log.
Details, deduplication, retries and security: [notifications.md](notifications.md).

## GitHub installations

The installations page lists accounts where the App is installed and the user
has access, with status, repository count, permissions (required, missing,
excessive) and the last event. **Sync repositories** (`github:manage`)
refreshes the installation's details and repository list from GitHub with an
installation token for the installation the caller is authorized for; a
repository ID or installation ID supplied by the browser never selects what
is read. Newly granted repositories appear in a user's view after they sign
in again, because repository visibility comes from GitHub at sign-in.

Uninstalling or suspending the App happens on GitHub (**Manage on GitHub**);
the dashboard does not claim to remove an installation.

## API reference

All endpoints are under `/api/v1`, return JSON and require a session except
`auth/login` and `auth/callback`.

**Envelope**

```json
{ "data": { }, "meta": { "next_cursor": "eyJ...", "limit": 25 } }
```

```json
{ "error": { "code": "FORBIDDEN", "message": "You do not have permission to perform this action.", "request_id": "5b1f..." } }
```

| Status | Code | When |
|---|---|---|
| 400 | `VALIDATION_ERROR` | invalid parameter or body (`field` names it) |
| 401 | `UNAUTHENTICATED`, `SESSION_EXPIRED`, `REAUTHENTICATION_REQUIRED` | no session, expired session, stale sign-in for a weakening change |
| 403 | `FORBIDDEN`, `CSRF_FAILED`, `CORS_REJECTED` | role lacks the permission; missing CSRF token or foreign Origin |
| 404 | `NOT_FOUND` | missing **or outside your access** |
| 405 | `METHOD_NOT_ALLOWED` | with an `Allow` header |
| 409 | `CONFLICT`, `CONFIRMATION_REQUIRED` | optimistic concurrency, invalid state change, unconfirmed weakening or rollback |
| 422 | `POLICY_VERSION_INVALID` | a stored policy version failed its integrity check and cannot be restored |
| 411 / 413 / 415 | `LENGTH_REQUIRED`, `PAYLOAD_TOO_LARGE`, `UNSUPPORTED_MEDIA_TYPE` | bodies are JSON, at most 64 KB |
| 429 | `RATE_LIMITED` | with `Retry-After: 60` |
| 500 | `INTERNAL_ERROR` | no details are returned; use `request_id` with the logs |
| 502 | `GITHUB_UNAVAILABLE` | GitHub could not be reached |

**Pagination.** `limit` defaults to 25, maximum 100. `cursor` is opaque:
keyset positions for scans, violations and audit events; offsets for
repositories, members and policy versions. A cursor grants nothing.

**Filtering and sorting** happen on the server. Filter values are
allow-listed enums, positive integer IDs or ISO 8601 dates; `q` is normalised,
at most 100 characters, and matched with escaped `LIKE` patterns. Sort keys
come from a fixed list per endpoint.

**Routes**

| Route | Permission | Purpose |
|---|---|---|
| `GET /api/v1/auth/login` | public | Start GitHub sign-in (`return_to`) |
| `GET /api/v1/auth/callback` | public | Complete sign-in; sets the session cookie |
| `GET /api/v1/auth/session` | session | Current user, organizations, roles, CSRF token |
| `POST /api/v1/auth/logout` | session | End the session |
| `GET /api/v1/auth/sessions` | session | Your active sessions |
| `DELETE /api/v1/auth/sessions/{session_id}` | session | Revoke one of your sessions |
| `GET /api/v1/dashboard/overview` | `repositories:read` | Aggregated overview (`period`=`24h`\|`7d`\|`30d`, `organization`) |
| `GET /api/v1/organizations` | session | Organizations you can access, with role and permissions |
| `GET /api/v1/organizations/{organization_id}/members` | `members:read` | Members |
| `PUT /api/v1/organizations/{organization_id}/members/{user_id}` | `members:manage` | Grant or change a role (`role`, optional `login`) |
| `DELETE /api/v1/organizations/{organization_id}/members/{user_id}` | `members:manage` | Remove a member |
| `GET /api/v1/repositories` | `repositories:read` | Repositories (`organization`, `protection`, `q`, `sort`=`name`\|`risk`\|`recent`) |
| `GET /api/v1/repositories/{repository_id}` | `repositories:read` | Repository detail with enforcement, policy, scans, violations, activity |
| `GET /api/v1/repositories/{repository_id}/merge-queue` | `repositories:read` | Merge queue status, current and recent merge groups with results |
| `PUT /api/v1/repositories/{repository_id}/monitoring` | `repositories:manage` | Pause (`enabled: false`, `confirm`, `reason`) or resume monitoring |
| `POST /api/v1/repositories/{repository_id}/enforcement/refresh` | `repositories:manage` | Read enforcement evidence from GitHub |
| `GET /api/v1/scans` | `scans:read` | Scans (`organization`, `repository`, `result`, `event`, `rule`, `severity`, `from`, `to`, `q`, `sort`) |
| `GET /api/v1/scans/{scan_id}` | `scans:read` | Scan detail with findings |
| `GET /api/v1/scans/{scan_id}/comparison` | `scans:read` | New, no longer present and unchanged findings versus the previous scan |
| `GET /api/v1/scans/{scan_id}/executions` | `scans:read` | Every execution of the scan with trigger, result and policy/rules versions |
| `POST /api/v1/scans/{scan_id}/rescan` | `scans:trigger` | Queue a new execution of the same commits (202; 409 while one is queued or running) |
| `GET /api/v1/violations` | `violations:read` | Violations (`organization`, `repository`, `status`, `severity`, `rule`, `action`, `from`, `to`, `q`, `sort`=`newest`\|`oldest`\|`severity`\|`repository`) |
| `GET /api/v1/violations/{violation_id}` | `violations:read` | Violation detail with evidence, remediation, exposures, detections |
| `PUT /api/v1/violations/{violation_id}/acknowledgement` | `violations:manage` | Acknowledge (`note`) |
| `DELETE /api/v1/violations/{violation_id}/acknowledgement` | `violations:manage` | Remove the acknowledgement |
| `GET /api/v1/policies` | `policies:read` | Organization policies you can read |
| `GET /api/v1/policies/{organization_id}` | `policies:read` | One organization's policy |
| `PUT /api/v1/policies/{organization_id}` | `policies:write` | Save (`expected_version`, `floors`, `reason`, `confirm_weakening`) |
| `POST /api/v1/policies/{organization_id}/preview` | `policies:read` | Classify changes (`floors`) without saving |
| `GET /api/v1/policies/{organization_id}/versions` | `policies:read` | Version history |
| `GET /api/v1/policies/{organization_id}/versions/{version}` | `policies:read` | One version |
| `GET /api/v1/policies/{organization_id}/diff` | `policies:read` | Structured diff between two versions (`from`, `to`; 0 = no organization policy) |
| `POST /api/v1/policies/{organization_id}/rollback` | `policies:rollback` | Publish a new version restoring `target_version` (`expected_current_version`, `reason`, `confirm`) |
| `GET /api/v1/rules` | `rules:read` | Bundled rules |
| `GET /api/v1/rules/{rule_id}` | `rules:read` | Rule detail |
| `GET /api/v1/audit` | `audit:read` | Audit events (`organization`, `repository`, `type`, `actor`, `from`, `to`, `sort`) |
| `GET /api/v1/audit/{event_id}` | `audit:read` | One audit event |
| `GET /api/v1/github/installations` | `repositories:read` | Installations |
| `GET /api/v1/github/installations/{installation_id}` | `repositories:read` | Installation detail and recent events |
| `GET /api/v1/github/installations/{installation_id}/repositories` | `repositories:read` | Repositories of the installation you can access |
| `POST /api/v1/github/installations/{installation_id}/sync` | `github:manage` | Refresh the installation's repositories from GitHub |
| `GET /api/v1/notifications` | session | Your notifications (`state`=`unread`\|`read`\|`archived`\|`all`, `category`=`critical`\|`violations`\|`policy`\|`github`\|`scans`, `organization`, `cursor`); `meta.counts` |
| `GET /api/v1/notifications/counts` | session | Unread and unread critical counts (bounded at 1,000) |
| `POST /api/v1/notifications/read-all` | session | Mark your unread notifications read (optional `organization_id`) |
| `GET /api/v1/notifications/{notification_id}` | session | One of your notifications |
| `POST /api/v1/notifications/{notification_id}/read` | session | Mark read |
| `POST /api/v1/notifications/{notification_id}/unread` | session | Mark unread |
| `POST /api/v1/notifications/{notification_id}/archive` | session | Archive |
| `GET /api/v1/notification-preferences` | `notifications:read` | Per organization: channels, type settings, your in-app choices (recipients and webhooks only for `notifications:manage`) |
| `PATCH /api/v1/notification-preferences` | `notifications:read` | Mute or unmute non-mandatory types in your inbox (`organization_id`, `in_app`) |
| `GET /api/v1/organizations/{organization_id}/notification-settings` | `notifications:read` | One organization's notification settings |
| `PUT /api/v1/organizations/{organization_id}/notification-settings` | `notifications:manage` | Replace settings (`expected_version`, `types`, `email_recipients`, `confirm` when turning deliveries off) |
| `POST /api/v1/organizations/{organization_id}/notification-webhooks` | `notifications:manage` | Add an HTTPS endpoint (`url`, `confirm`; recent sign-in); returns the signing secret once (201) |
| `DELETE /api/v1/organizations/{organization_id}/notification-webhooks/{endpoint_id}` | `notifications:manage` | Remove an endpoint; pending deliveries are cancelled |
| `GET /api/v1/organizations/{organization_id}/notification-deliveries` | `notifications:manage` | E-mail and webhook delivery records |

Resource models are defined in `commitguard.controlplane.views` and mirrored
in `web/src/api/types.ts`. There is no OpenAPI document; this table and the
view models are the contract, and a test checks the table against the
implemented routes.

## Security controls

| Threat | Control |
|---|---|
| Unauthorized access | GitHub sign-in, server-side sessions, 401 on every protected route |
| IDOR / cross-tenant reads | access scope in every query; 404 for anything outside it; two-tenant tests for every route |
| Privilege escalation | permissions checked on the server per resource; no role logic in the browser decides anything |
| CSRF | `SameSite=Lax` cookie; writes require an allowed `Origin` **and** `X-CSRF-Token` (derived from the session, delivered in JSON); JSON bodies only |
| CORS | none by default (same origin); optional explicit allow-list with credentials; `*` refused at start-up |
| XSS through Git metadata | stored as text (control characters made visible, secrets redacted); rendered by React as text; no `dangerouslySetInnerHTML` (lint rule and test); API responses `application/json` with `nosniff` |
| Injected scripts | CSP `default-src 'self'; script-src 'self'; style-src 'self'; ... frame-ancestors 'none'` for the dashboard, `default-src 'none'` for the API; no inline scripts or `eval` in the build |
| SQL injection | parameterised statements assembled only from constant fragments; allow-listed sort keys; escaped `LIKE` |
| Open redirect | `return_to` limited to same-origin application paths |
| Session theft | `HttpOnly` cookie, hashed at rest, 8 h lifetime, 2 h idle timeout, revocation, HSTS in production |
| Token exposure | GitHub user tokens used during sign-in only, never stored or sent to the browser; installation tokens never leave the server |
| Abuse | per-user rate limits: sign-in 20/min per address, reads 600/min, searches 120/min, writes 30/min, GitHub-calling actions 10/min, sensitive changes (rollback, notification settings, webhooks) 10/min |
| Policy rollback abuse | `policies:rollback`, required reason, explicit confirmation, recent sign-in for weakening rollbacks, optimistic concurrency, immutable versions, audit and notification |
| Notification leakage | inbox rows per user; type permission and GitHub repository visibility re-checked on every read; webhook secrets derived, shown once, never stored |
| Clickjacking | `frame-ancestors 'none'`, `X-Frame-Options: DENY` |
| Logs | request ID, method, route template, status, duration, user ID; never cookies, headers, tokens or query strings |

Every mutation that changes security state writes its audit event in the same
database transaction.

## Frontend

`web/` is a React 19 + TypeScript application built with Vite.

| Concern | Choice |
|---|---|
| Routing | React Router; every resource is deep-linkable and survives refresh (the server returns `index.html` for application routes) |
| Server state | TanStack Query: short staleness, refetch on focus, backoff polling for queued/running scans, 60 s polling for notification counts; filters and cursors live in the URL |
| UI state | local component state; no global store |
| API client | `web/src/api/client.ts` is the only `fetch`; typed resource modules per area |
| Errors | error boundaries per page and at the root; `401` redirects to sign-in once |
| Icons | lucide-react |
| Fonts | self-hosted: Bricolage Grotesque (display), Public Sans (text), Commit Mono (SHAs, rule IDs, evidence) |

**Design system.** Colour is reserved for verdicts. The interface is ink on a
cool mineral paper in light mode and pale ink on a green-black surface in dark
mode; the only saturated colours are PASS green, BLOCKED seal red, WARNING
ochre, RUNNING blue and CRITICAL oxblood. Every status is text with an icon
and a colour, never colour alone, and uses one vocabulary everywhere
(`web/src/lib/labels.ts`). The mark is a commit node on its branch line held
between inspection brackets. Light and dark themes follow the system setting,
with a manual override.

**Responsive.** Below 960 px the sidebar becomes an accessible drawer; below
720 px tables become labelled cards and filters collapse behind a toggle.

**Accessibility.** Semantic landmarks, a skip link, visible focus, labelled
controls, focus-trapped dialogs that restore focus, `aria-current` navigation,
live announcements when a scan completes, reduced-motion support, and WCAG AA
contrast in both themes (checked with axe in the browser tests).

## Testing

```bash
# Python: unit, integration, API, authorization, security, lifecycle, performance
pytest tests/unit/controlplane tests/integration/github/app/dashboard

# Frontend: components and pages with loading, empty, success and error states
cd web && npm test && npm run typecheck && npm run lint

# Browser: the complete stack (App, workers, real Git, API, built dashboard)
cd web && npm run build && npm run e2e
```

The browser tests start `tests/e2e/dashboard_harness.py`, which runs the real
service against an offline model of GitHub and real Git repositories. GitHub's
authorization page is simulated; github.com is never contacted. Set
`CHROMIUM_PATH` to use an installed Chromium instead of `npx playwright install`.

## Performance

`test_dashboard_performance.py` seeds 100 repositories, 10,000 scans, 50,000
findings and 100,000 audit events, and separately 100,000 notifications,
100,000 webhook event records and 10,000 policy versions. It requests every
list and detail endpoint and a second cursor page, and checks that the default
orderings use indexes. These are test targets, not a claim of production scale.
Measured on the development machine (Linux, Python 3.13, SQLite 3.53):

| Request | First page | Second page |
|---|---:|---:|
| Overview | 134 ms | — |
| Repositories (100) | 22 ms | 21 ms |
| Scans | 86 ms | 55 ms |
| Scans filtered by rule | 50 ms | 54 ms |
| Violations | 54 ms | 14 ms |
| Violations by severity | 18 ms | 16 ms |
| Audit log | 2 ms | 132 ms |
| Scan / violation / repository detail | 4 ms / 2 ms / 8 ms | — |
| Notifications (100,000 in the inbox) | 13 ms | 11 ms |
| Unread notifications / by category | 11 ms / 10 ms | 11 ms / 10 ms |
| Notification counts (bounded) | 27 ms | — |
| Policy versions (10,000) / diff | 1 ms / 0.4 ms | 1 ms |
| Mark 10,000 notifications read | 314 ms | — |

No endpoint returns more than 100 items; the browser never loads a
collection. There is no server-side cache: security state is read fresh on
every request (`Cache-Control: no-store`).

## Known limitations

- Tested against an offline model of GitHub's API and OAuth flow, not against
  github.com. The OAuth request includes PKCE parameters.
- Repository visibility and installation access are read from GitHub at
  sign-in; newly granted repositories appear after the next sign-in (at most 8
  hours).
- Branch protection is `unknown` for classic protection rules whose required
  checks GitHub hides without Administration permission.
- Single host: SQLite and in-process workers, as for the App.
- Enforcement evidence (required check, merge queue) is refreshed on request,
  not on a schedule.
- Notification e-mail goes to organization-level recipients configured by
  administrators; CommitGuard does not store members' e-mail addresses.
- No SAML/SSO beyond GitHub sign-in, no OpenAPI document.
- Members are added by numeric GitHub user ID.
