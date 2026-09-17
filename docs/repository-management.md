# Repository management at organization scale

How CommitGuard keeps track of an organization's repositories and how
administrators manage many of them at once: inventory, onboarding modes,
repository groups, bulk operations, scheduled scans and organization rules.

## Inventory and synchronisation

GitHub decides which repositories CommitGuard can see. They are identified by
their **immutable GitHub ID**, never by name, so a renamed or transferred
repository keeps its history and cannot be taken over by a new repository with
the old name.

| Source | When |
|---|---|
| `installation` / `installation_repositories` webhooks | the app is installed, repositories are added or removed |
| **Sync** (dashboard, `POST /api/v1/github/installations/{id}/sync`) | on demand; lists the installation's repositories with an installation token |
| repository webhooks | name, default branch, `private` and `archived` changes |

Synchronisation health is tracked **separately from the connection**. An
installation can be connected and still fail to synchronise:

| Sync status | Meaning |
|---|---|
| `healthy` | the last full synchronisation succeeded within 7 days |
| `syncing` | a synchronisation is running |
| `failed` | the last synchronisation failed, or did not finish within 15 minutes (a crashed worker) |
| `degraded` | the installation is suspended, or has not been synchronised for 7 days |
| `never` | repositories are known from GitHub events only |

A `failed` synchronisation or a suspended installation puts the organization's
posture `at_risk` ([security-posture.md](security-posture.md)). A `degraded` sync
is shown on the installation but does not change posture on its own: webhooks
keep the inventory current between synchronisations.

## Repository state has several dimensions

They are kept apart and never merged into one label:

| Dimension | Values |
|---|---|
| connection | `connected`, `suspended`, `disconnected` |
| archived | GitHub's `archived` flag |
| onboarding | `discovered`, `onboarded`, `excluded` |
| mode | `enforce`, `monitor` |
| enforcement | `protected`, `at_risk`, `unprotected`, `unknown` (GitHub branch protection evidence, Phase 5) |
| policy | `up_to_date`, `stale`, `syncing`, `error`, `pending` ([policy-inheritance.md](policy-inheritance.md#effective-policy-cache-and-propagation)) |

"Connected, onboarded, monitor mode, unprotected" is a valid combination and is
shown as such.

## Onboarding and modes

A newly discovered repository gets the organization's defaults
(`auto_onboard_new_repositories`, `default_onboarding_mode`). With
`archived_repositories: exclude`, archived repositories are recorded as
`excluded`.

| Mode | Scans | Checks | Violations and alerts |
|---|---|---|---|
| `enforce` | yes | fail on `block` | recorded, notified |
| `monitor` | yes | never fail; `block` is reported as `warn` | recorded; alerts read "Would block (monitor mode)" |

Monitor mode lets an organization see what CommitGuard *would* block before it
blocks anything. A repository in monitor mode is never shown as `secure`.

**Onboarding never removes governance.** Mandatory requirements apply to every
repository of the organization whatever its onboarding state; `discovered` only
means an administrator has not confirmed it yet. An excluded repository is not
counted as a "required" repository in the compliance fraction; that is shown,
not hidden.

Changing modes needs `repositories:manage` and `confirm: true`, because both
directions matter: `enforce` can make checks fail and stop merges, `monitor`
stops blocking (and also needs a `reason`). Every change is audited
(`repository_onboarded`, `repository_mode_changed`) and invalidates the
repository's effective policy.

```http
POST /api/v1/organizations/{id}/repositories/mode
{"repository_ids": [5001, 5002], "mode": "monitor", "reason": "Pilot", "confirm": true}
```

## Repository groups

A group is a named set of repositories that policies, exceptions, rollouts and
scan schedules can target (`Production`, `Backend`, `Documentation`).

* A repository can belong to any number of groups. Where several groups set a
  *default* for the same rule, the most restrictive applies; *mandatory*
  requirements always combine to the strongest.
* Members are repositories of the same organization that the caller can see;
  anything else is refused as if it did not exist.
* Names are unique per organization (case-insensitive); at most 500 groups.
* Membership changes are audited and invalidate exactly the repositories added
  or removed.
* Groups are **archived, never deleted**. Policy versions and audit events keep
  referring to them. Archiving a group that has a policy or active exceptions
  needs `confirm`; its open exceptions end (requests cancelled, active ones
  revoked with reason "repository group archived") and its members re-resolve
  their effective policy.

Routes: `GET/POST /api/v1/organizations/{id}/repository-groups`,
`GET/PATCH/DELETE /api/v1/repository-groups/{group_id}`,
`POST /api/v1/repository-groups/{group_id}/repositories` and `.../repositories/remove`.

## Bulk operations

Changing hundreds of repositories must not happen inside one web request.

```text
POST bulk operation ─► validate (permission, visibility, limits) ─► one row + one item per repository   (202)
maintenance loop    ─► claim with a lease ─► batches of 100 items per transaction
                                             (a failing batch is retried item by item)
status: queued → running → completed | partial | failed | cancelled
```

| Type | Parameters | Permission |
|---|---|---|
| `add_to_group`, `remove_from_group` | `group_id` | `repositories:manage` |
| `onboard` | `mode`, `confirm` | `repositories:manage` |
| `set_mode` | `mode`, `confirm`, `reason` for monitor | `repositories:manage` |
| `set_monitoring` | `enabled` | `repositories:manage` |
| `schedule_scan` | - | `scans:trigger` |

There is deliberately no "apply policy to these repositories" operation: add
them to a group that has the policy, so the change keeps versioning, approval,
simulation, rollouts and rollback.

Guarantees:

* **bounded**: at most 5,000 repositories per operation, 10 open operations per
  organization, 200 items per maintenance pass;
* **idempotent**: an `idempotency_key` (or a hash of the request) is unique per
  organization, so a double-submitted form creates one operation; each item is
  idempotent, so an item retried after a crash does not double-apply;
* **observable**: per-item status (`completed`, `failed` with a reason,
  `skipped`), totals, `GET /api/v1/bulk-operations/{id}`;
* **recoverable**: `cancel` stops pending items, `retry` re-queues failed ones;
* **audited**: one event for the request (`bulk_operation_requested`), one for
  the outcome, plus the normal per-change audit events.

Measured: 5,000 items in about 0.3 seconds of background processing with 10,000
repositories ([security-posture.md](security-posture.md#performance)).

## Scheduled scans

A policy change applies to new commits immediately, but a GitHub check on an
existing default branch reflects it only after another scan. Scheduled scans
re-evaluate default branches with the **current** effective policy.

```text
scan_schedules            "Production nightly" · group Production · daily 02:00 Europe/Berlin
    │ due
    ▼
scan_schedule_runs        one row per (schedule, slot) - a slot runs at most once
    │ at most 50 repositories per pass, nothing while more than 200 scans are queued
    ▼
scan_jobs (trigger "scheduled") ─► the normal worker, checks and policy
```

* Target: the organization, a group or one repository. Cadence `daily` or
  `weekly` (`weekday` 0 = Monday), local `hour:minute` in an IANA time zone
  (daylight-saving transitions handled). At most 100 schedules per organization.
* **Skipped with a reason, never silently**: `archived`, `disconnected`,
  `monitoring_paused`, `excluded`, `no_default_branch`, `already_queued`,
  `not_found`, and `unchanged` (the head was already scanned with the current
  effective policy). A policy change makes the same head worth scanning again.
* Runs record repositories, queued, skipped (by reason) and failed, and end
  `completed`, `partial` or `failed`.
* Creating, changing and disabling schedules needs `security:manage` and is
  audited (`scan_schedule_created`, `..._changed`, `..._disabled`); each run is
  audited as `scheduled_scans_queued`.
* Limitation: the first scan of a branch has no earlier trusted commit, so its
  repository configuration comes from the built-in defaults (as for any first
  push); organization governance applies fully.

Routes: `GET/POST /api/v1/organizations/{id}/scan-schedules`,
`GET/PATCH /api/v1/scan-schedules/{id}`, `POST /api/v1/scan-schedules/{id}/disable`.

## Organization rules

Organizations often have their own coding agents and automation accounts.
Organization rules add them as **data** to the trusted detection rules.

| Trust level | Source | Can |
|---|---|---|
| `built_in` | rule files shipped with CommitGuard | define rules and detectors |
| `organization` | dashboard, versioned | add identity data to existing rules |
| `repository` | `.commitguard.yaml` | configure policies only, no rules |

```json
{
  "ai_identities": [
    {"id": "internal_agent", "display_name": "Internal Agent",
     "names": ["Internal Agent"], "emails": ["agent@example.com"], "github_logins": ["internal-agent"]}
  ],
  "bot_identities": [
    {"id": "release_bot", "display_name": "Release Bot", "github_logins": ["release-bot"]}
  ]
}
```

* Entries are detected by the same detectors and reported under the same rule
  IDs (`ai_coauthor`, `ai_identity`, `ai_trailer`, `bot_identity`); their IDs are
  prefixed `org_` so they never collide with bundled agents.
* **No code, no patterns.** Values are exact names, name prefixes, e-mails and
  logins compared after normalisation. There are no regular expressions,
  wildcards, expressions or imports, so an organization rule cannot execute
  code or cause catastrophic backtracking (ReDoS). An architecture test forbids
  `eval`, `exec` and dynamic imports in the governance and policy packages.
* Validation: at most 100 entries per kind, 20 values per field, 128 characters
  per value, no control characters, 64 KiB per document; an alias already
  claimed by a bundled agent is rejected, not silently overridden.
* Versions are immutable (database triggers). Changing them needs `rules:manage`,
  an `expected_version` and a `reason`; removing an identity is called out in
  the audit event (`organization_rules_changed`). Every repository's effective
  policy is invalidated, so scheduled scans re-evaluate under the new rules.
* Each scan records the rules version it used:
  `<bundled rules fingerprint>+org-v<N>`.

Routes: `GET/PUT /api/v1/organizations/{id}/rules`,
`GET /api/v1/organizations/{id}/rules/history`.
