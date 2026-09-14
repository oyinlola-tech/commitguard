# GitHub enforcement

> Status: **not implemented** (Phase 4). `src/commitguard/github/` is a
> placeholder and `.github/workflows/commitguard.yml` only runs `doctor` and
> `policy list` on manual dispatch. No GitHub API access exists.

## Why a second layer

Local hooks run where the developer controls everything. The repository is the
first place a policy can be enforced independently of the contributor.

```text
developer ──git push──▶ GitHub ──pull_request──▶ CommitGuard workflow
                                                     │
                                          scan base..head, every commit
                                          policy from BASE branch
                                                     │
                                         required status check ──▶ merge allowed / blocked
```

## Planned design

1. **Workflow** triggered on `pull_request` (not `pull_request_target`, so
   untrusted code never runs with write tokens).
2. **Full history checkout** (`fetch-depth: 0`) and scan of **every** commit in
   `base..head` — a violating commit hidden under a clean HEAD still fails.
3. **Policy from the base branch.** The pull request must not be able to add
   `action: allow` to `.commitguard.yaml` and approve itself.
4. **Result reporting** via workflow annotations and job summary; Checks API
   integration optional and opt-in.
5. **Branch protection guidance**: make the CommitGuard check required;
   consider disallowing merge strategies that rewrite attribution unexpectedly
   (e.g. squash merges can concatenate `Co-authored-by` trailers from all
   commits into the final message — the squash commit must be evaluable too).
6. **Least privilege**: `contents: read` by default; `checks: write` only if
   Checks API reporting is enabled.

## Out of scope for the local tool

The local CLI never requires GitHub authentication and never contacts GitHub.
Tokens, when used in Phase 4, are read from the environment at call time and
never logged.
