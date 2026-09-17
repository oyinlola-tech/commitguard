# Merge queue and re-run security

Two ways a passing check can become misleading: a **stale re-run** (a check that
passed on an older commit is re-run and treated as current) and a **merge queue**
(a pull request whose own head passed, merged together with another change that
did not).

## Observed results

**Recorded 2026-09-17** (`stale-rerun-and-merge-queue`):

| Scenario | Observed |
|---|---|
| Re-run the passing check of an outdated commit after a violating commit was pushed | `{"status": "ignored"}`; the newest pull request check remains **failure** |
| A pull request whose own head passed, queued behind an AI-attributed change | the pull request head check is **success**; the **merge group** check is **failure** |

## Why re-runs are restricted

GitHub lets anyone with write access press "Re-run". If CommitGuard simply
re-scanned whatever commit the re-run names, an old, clean commit's check could be
refreshed and presented as the current state of a branch that has since gained a
violation. Re-runs are therefore only honoured for the newest commit of a pull
request or branch; anything else is ignored, and the ignore is recorded.

Earlier results are never deleted: a re-run is a new, numbered execution, and the
dashboard shows the history. A result that was once a failure stays visible.

## Why the merge group is scanned, not just the head

A merge queue builds a temporary merge commit from several pull requests. Each
pull request may be clean on its own while the combination is not - and it is the
combination that will land on the protected branch. CommitGuard handles
`merge_group` events by scanning `base..merge-group SHA`, which is the code that
would actually be merged, and publishes the check on the merge group commit.

The refs GitHub uses for this (`refs/heads/gh-readonly-queue/<base>/pr-<n>-<sha>`)
are validated rather than assumed: a `merge_group` payload whose refs do not
describe a merge queue is rejected as malformed.

## Limits

- Modelled against a fake GitHub, like the rest of the server-side experiments.
- If the merge queue is configured not to require the check, none of this blocks a
  merge - GitHub's setting, outside CommitGuard's control, and reported as such.
