# ADR-004: Policy comes from the base commit

**Status:** Accepted (Phase 4) · **Applies to:** `ci/`, `github/`

## Context

If the check reads `.commitguard.yaml` from the pull request it is checking, then
the first commit of any pull request can be "disable the check".

## Decision

Server-side evaluation reads the policy from the **base** commit of the pull
request (or the commit before a push, or the default branch). The workflow goes
further and installs CommitGuard itself from that trusted commit via
`git worktree`.

## Consequences

Good:

- A pull request cannot weaken the rules judging it: disabling, allowing and
  deleting the configuration all still produce exit 1 (recorded experiment).
- Policy changes take effect after review, which is what review is for.

Bad:

- A pull request that *tightens* policy does not take effect until merged, which
  surprises people who expect the new rule to apply immediately.
- The base commit must be fetched, so the workflow needs full history
  (`fetch-depth: 0`).

## Enforcement

`tests/integration/github/security/test_bypass_resistance.py`
(`policy-tampering-in-pull-request`), and
`tests/unit/github/test_workflows_static.py` asserts the workflow installs from a
trusted commit.
