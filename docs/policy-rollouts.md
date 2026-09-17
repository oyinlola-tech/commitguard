# Staged policy rollouts

Publishing a policy to hundreds of repositories at once risks breaking all of
them. A staged rollout applies a new organization or group policy version to
**pilot repositories first**, then to more, with safety thresholds.

```text
Policy v15 published with a rollout
    Stage 1  Pilot        10 repositories    enrolled → resolve v15; the rest keep v14
    Stage 2  50 %         50 of 100          enrolled at the next "advance"
    Stage 3  All          100 %              state "active"
```

## How it works

A rollout does not create a separate kind of policy. The published version is
an ordinary immutable version; the rollout only decides **which version applies
to which repository** while it is in progress:

* an **enrolled** repository resolves the new version;
* every other repository in scope keeps the **previous** version;
* when every repository is enrolled the rollout is `active` and the newest
  version applies everywhere.

Enrolling a stage invalidates the effective policy of exactly the repositories
it enrolls ([policy-inheritance.md](policy-inheritance.md#effective-policy-cache-and-propagation)).
Every scan records the version it used and, in `governance.versions.rollouts`,
the rollout through which it applied.

## Creating a rollout

Publish a draft with a `rollout` (organization and group targets; a repository
policy applies to one repository):

```json
{
  "rollout": {
    "stages": [
      {"name": "Pilot", "repositories": [5001, 5002, 5003]},
      {"percent": 50}
    ],
    "thresholds": {"max_error_rate": 0.2, "max_block_rate": 0.5, "min_scans": 5},
    "auto_pause": true,
    "auto_rollback": false
  }
}
```

* A stage names repositories (a pilot) or a **cumulative** percentage of the
  scope (repositories enrolled in earlier stages count towards it). Percentages
  must not decrease; a final 100 % stage is added when the last one is a
  percentage below 100.
* Percentage stages pick repositories in a deterministic, stable order
  (a hash of the rollout and repository IDs), so re-evaluating never reshuffles.
* The scope is the organization's repositories (organization target) or the
  group's members (group target) at enrollment time.
* The first stage is enrolled in the same transaction as the publication.
* One rollout can be in progress per policy target (unique index).

## States

| State | Meaning |
|---|---|
| `pilot` | first stage enrolled |
| `rollout` | expanded beyond the pilot |
| `paused` | not expanding; enrolled repositories keep the new version |
| `active` | every repository in scope enrolled |
| `rolled_back` | a rollback version restored the previous document |

Operations (all audited): **advance** (enroll the next stage; the last one
completes the rollout), **pause** (reason), **resume**, **rollback** (reason and
confirmation; `policies:rollback`). Advance, pause and resume need
`policies:publish`.

## Progress, honestly

```text
Policy v15 rollout            Stage 2 of 3 · rollout
Repositories   80 / 100 enrolled     78 propagated
Scans since enrollment: 94   Passed 72   Blocked 20   Errors 2
```

| Field | Source |
|---|---|
| `scope_repositories`, `enrolled` | rollout enrollment |
| `propagated` | enrolled repositories whose effective policy is `up_to_date` with the new version |
| `scanned`, `passed`, `blocked`, `errors` | completed scans of enrolled repositories after enrollment that used the new version |
| `complete` | `true` only when the state is `active`, every repository in scope is enrolled **and** propagated |

A rollout is never reported complete because the last stage was requested.

## Automatic halt

With `auto_pause` (the organization default, `rollout_auto_pause`), the
maintenance loop checks every rollout in `pilot` or `rollout`: once enrolled
repositories have at least `min_scans` completed scans under the new version,

* a share of scans that **could not complete** (`error`) above `max_error_rate`, or
* a share of **blocked** scans above `max_block_rate`

pauses the rollout, audits `policy_rollout_paused` (actor `system`, with the
figures) and notifies `policy_rollout_failed`:

```text
Policy rollout paused: v15 - safety threshold exceeded: 4 of 6 scans could not be completed
```

Blocked scans are not necessarily a problem - a stricter policy is supposed to
block - so tune `max_block_rate` to what the change is expected to do.
**Automatic rollback** happens only when `auto_rollback` is set explicitly
(per rollout or organization); by default a person decides.

## Rollback

Rolling back a rollout uses the Phase 7 rollback: a **new** version restoring
the previous version's document, with its audit event, notification and
lineage (`kind=rollback`, `restored_version`, `rollback_of`). The rollout becomes
`rolled_back` with `rollback_version`; every repository in scope re-resolves the
restored policy; scans already recorded under the rolled-out version keep it.

```text
v14  archived
v15  archived   (rolled out to 50 %, then rolled back)
v16  active     kind=rollback  restored_version=14  rollback_of=15
```

A rollout of the very first version has nothing to roll back to: publish a new
version instead (the API explains this with a conflict).

## API

`GET /api/v1/organizations/{id}/rollouts` (`active=true`), `GET /api/v1/rollouts/{id}`,
`POST /api/v1/rollouts/{id}/advance|pause|resume|rollback`.
