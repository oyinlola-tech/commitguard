# Merge queue

A pull request that passed CommitGuard is not automatically safe to merge
through a merge queue. The queue tests a **temporary merge group commit**: the
pull request combined with the latest target branch and every change queued
ahead of it. Commits that were never part of the pull request - an
AI-attributed commit merged ahead of it, for example - can be part of that
commit's history. CommitGuard therefore validates the merge group itself.

```text
pull request ──► CommitGuard check "commitguard-app" on the PR head ──► PASS
      │
      ▼ added to the merge queue
merge group commit on refs/heads/gh-readonly-queue/<base>/pr-<n>-<sha>
      │  merge_group webhook (checks_requested)
      ▼
CommitGuard scans base_sha..head_sha, policy from the target branch (trusted)
      │
      ▼
Check Run "commitguard-app" on the merge group commit ──► PASS / FAIL
      │
      ▼
merge queue merges, or removes the pull request from the queue
```

## GitHub semantics (verified)

Checked against GitHub's published webhook schemas
(`webhook-merge-group-checks-requested`, `webhook-merge-group-destroyed`) and
the merge queue documentation:

| | |
|---|---|
| Event | `merge_group`, available to GitHub Apps with the **Merge queues: read** permission |
| `checks_requested` | status checks are requested for a merge group (created, or a pull request was added) |
| `destroyed` | the merge group was removed; `reason` is `merged`, `invalidated` or `dequeued` |
| Payload | `merge_group.head_sha` (the commit the queue waits on), `head_ref` (`refs/heads/gh-readonly-queue/<base>/...`), `base_sha` (its parent on the target branch), `base_ref`, `head_commit` |
| Required checks | the queue waits for required status checks reported **on the merge group commit**; after the queue's check timeout, missing checks count as failed |

CommitGuard normalises the event into a `MergeGroupEvent` with only these
fields. The pull request number is parsed from GitHub's ref name for display
and is never used for authorization.

## What CommitGuard does

| Event | CommitGuard |
|---|---|
| `checks_requested` | validates the refs (`head_ref` must be on GitHub's read-only queue ref for `base_ref`; `head_commit.id` must equal `head_sha`), records the merge group, and queues a scan of `base_sha..head_sha` whose Check Run `commitguard-app` is created on `head_sha` |
| `destroyed` | marks the group destroyed, cancels a queued scan, ends the group's violation exposures (`merged`: they move to the target branch) |
| a repeated `checks_requested` for the same group | nothing (duplicate) |
| `checks_requested` after `destroyed` (out of order) | nothing: a destroyed group is never revived |
| recreated group (new base, new SHA) | a new, independent scan: results are never reused across merge group SHAs |

The scan runs the same planner as the GitHub Action's `merge_group` support:
the commit range is `base_sha..head_sha` and the policy is read from
`base_sha`, the trusted target branch, never from the queued changes.

Before scanning, the worker checks that the merge group still exists. A group
destroyed first is cancelled as `stale` and nothing is published.

## Failure

| Situation | GitHub check on the merge group commit | Dashboard | Notification |
|---|---|---|---|
| a commit in the group violates policy | `failure` | scan `blocked`, **Failure source: merge queue**, violation with a merge group exposure | `merge_queue_failure` (and `high_violation`/`critical_violation` for new violations) |
| CommitGuard cannot validate (GitHub API, fetch, timeout) | `failure`/`timed_out` when it can be published; otherwise no check, so the queue times out | scan `error` | `merge_queue_failure` |
| the group is removed before the scan | none | scan `stale` | none |

Unknown is never treated as pass.

Audit events: `merge_group_created`, `merge_group_passed`,
`merge_group_blocked`, `merge_group_scan_failed`, `merge_group_destroyed`
(repository, SHA, pull requests, scan, result). Metrics:
`merge_groups_scanned`, `merge_groups_failed`.

## Configuring GitHub

CommitGuard does not change GitHub settings. To make CommitGuard gate merges
through the queue:

1. In the GitHub App settings, grant **Repository permissions → Merge queues:
   Read-only** and subscribe to the **Merge group** event. Existing
   installations must accept the new permission.
2. In a ruleset for the target branch (or branch protection), enable
   **Require merge queue**.
3. In the same ruleset, **Require status checks to pass** and add
   `commitguard-app` (the GitHub App's check). The same name is used for pull
   requests and merge groups.
4. Refresh the repository's enforcement status in the dashboard.

`commitguard github validate` warns when the App lacks the permission or the
`merge_group` subscription. Without them, merge groups are simply not scanned
and the queue waits for (then times out on) the required check.

Repositories that enforce CommitGuard through the GitHub Action instead
already run the Action on `merge_group` (see
[github-enforcement.md](github-enforcement.md)).

## Dashboard

Repository → **Merge queue** shows:

| Field | Source |
|---|---|
| Status `enabled` / `not_enabled` / `unknown` | a ruleset `merge_queue` rule on the default branch; `not_enabled` needs both the rules and branch endpoints to answer with no protection; a classically protected branch is `unknown`, because its merge queue setting is not visible with the App's permissions |
| Current merge group, CommitGuard result, last validation | stored merge groups and their scans |
| Recent merge groups | state, destroy reason, pull requests, result |

A missing permission is shown as such. "Status unavailable" (an error) is
never displayed as "not enabled".

`GET /api/v1/repositories/{id}/merge-queue` returns the same data.

## Limitations

- Evidence about whether a merge queue is required is read on **Refresh
  enforcement status**, not continuously.
- With the `ALLGREEN` grouping strategy GitHub may request checks for several
  merge commits of one group; each SHA gets its own scan.
- Tested against an offline model of GitHub's API and webhooks, not github.com.
