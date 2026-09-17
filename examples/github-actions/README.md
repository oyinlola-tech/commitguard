# GitHub Actions: the check a contributor cannot skip

What this shows: every commit a pull request introduces is scanned on GitHub, with
the policy read from the **base** commit, so a pull request cannot relax the rules
that judge it.

## Set it up

```bash
commitguard init --github \
  --action-repository oyinlola-tech/commitguard \
  --action-ref <40-character commit sha>
```

That writes `.github/workflows/commitguard.yml`. The copy here
(`commitguard.yml`) is the same file, rendered with a placeholder SHA - replace
`bbbb...` with the commit you want to pin before using it.

Then make the check required, which is the step that actually blocks merges:

1. Repository **Settings -> Rules / Branches**, protect `main`.
2. Require a pull request before merging, and block direct pushes.
3. Require status checks to pass and add the check named **`commitguard`**.
   GitHub only offers the name after the workflow has run once.

`commitguard github setup` prints these steps and what it can verify locally.

## What you should see

| Situation | Check | Merge |
|---|---|---|
| Pull request with a clean history | `commitguard` passes (exit 0) | allowed |
| Pull request containing `Co-authored-by: Claude <noreply@anthropic.com>` | `commitguard` fails (exit 1) with an annotation on the commit | blocked, once the check is required |
| Pull request that edits `.commitguard.yaml` to allow AI attribution | still fails | blocked: the policy comes from the base commit |
| Pull request that deletes the workflow | no check runs | blocked, because a required check that never reports is not satisfied |
| Commit pushed straight to `main` | the push-triggered run fails **after** the commit is on GitHub | prevention needs protected branches and required pull requests |

The last two rows are recorded experiments, not claims:
`tests/integration/github/security/test_bypass_resistance.py`.

## Permissions

The job needs `contents: read` and no secrets, so it works for pull requests
from forks. It never checks out or runs repository code.
