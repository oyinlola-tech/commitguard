# Policy tampering

Can a contributor weaken the policy that judges their own contribution?

## The attack

A pull request that changes `.commitguard.yaml`:

```yaml
version: 1
policies:
  ai_coauthor:
    enabled: false      # or: action: allow
```

...or simply deletes the file, and then includes a commit with
`Co-authored-by: Claude <noreply@anthropic.com>`.

## Observed result: prevented

**Recorded 2026-09-17** (`policy-tampering-in-pull-request`):

| Variant in the pull request | Check exit code |
|---|---|
| `enabled: false` | **1** (blocked) |
| `action: allow` | **1** (blocked) |
| `.commitguard.yaml` deleted | **1** (blocked) |

## Why

Two rules, both in the design rather than in a configuration setting:

1. **Trusted policy source.** The check evaluates with the policy from the *base*
   commit of the pull request, not from the pull request's own tree. Changing the
   policy takes effect only after the change itself has been merged - which is a
   review, by the people who own the branch.
2. **Rules are not repository data.** Detection rules come from the installed
   CommitGuard package, never from the repository being scanned, so a pull request
   cannot edit `rules/*.yaml` to make itself invisible. The workflow goes further
   and installs CommitGuard from a trusted commit with `git worktree`, so the
   scanner binary is not the contributor's either.

## The organization layer

Where a central service is used, a **mandatory policy** is applied after every
local layer and can only make enforcement stricter. Recorded as
`mandatory-policy-floor`: with the repository's own merged configuration disabling
`ai_coauthor`, the check is *success* without the mandatory policy and *failure*
with it.

This property is also checked over generated configurations rather than examples:
`tests/security/test_properties.py` asserts, for random repository and mandatory
configurations, that the effective action is never weaker than either input and
that `enabled: false` in a mandatory policy is rejected outright.

## What is not covered

- **Repository administrators** can remove the App, unprotect the branch or stop
  requiring the check. Nothing inside CommitGuard can prevent that; the dashboard
  reports it as a posture problem rather than ignoring it.
- **Workflow removal** in a pull request stops the check from running at all. A
  required check that never reports is not satisfied, so the merge is still
  blocked - but that depends on branch protection being configured, which
  CommitGuard cannot verify from outside GitHub.
