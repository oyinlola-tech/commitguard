# GitHub Actions deployment

> Status: **Implemented.** `commitguard ci github`, the composite Action
> (`action.yml`) and the workflow template (`commitguard init --github`) are
> covered by integration tests. The Action's shell steps are bash and have only
> been exercised on Linux runners.

```text
Developer ──push / pull request──▶ GitHub ──▶ GitHub Actions job "commitguard"
                                                  │ actions/checkout (fetch-depth: 0, persist-credentials: false)
                                                  │ install CommitGuard from the pinned Action source (hash-pinned deps)
                                                  ▼
                                   python -P -m commitguard ci github
                                                  │ commits from the event payload; policy from the trusted commit
                                                  ▼
                                   exit 0 success · exit 1 blocked · exit 2 error
                                                  ▼
                   merge blocked ONLY when branch protection or a ruleset requires "commitguard"
```

Full reference (scanned ranges, trust model, output, forks):
[../github-enforcement.md](../github-enforcement.md).

## Set up in a repository

The Action is referenced from the CommitGuard repository pinned to a full commit
SHA. `init` refuses tags and branches:

```bash
commitguard init --github \
  --action-repository oyinlola-tech/commitguard \
  --action-ref <40-character commit SHA>
```

This writes `.commitguard.yaml` if it is missing and
`.github/workflows/commitguard.yml` (never overwriting an existing workflow).
The generated job is named `commitguard`, runs on `pull_request`, `merge_group`
and `push`, and has `permissions: contents: read`.

No package index is used: the Action installs CommitGuard from its own pinned
source with `pip install --no-deps --no-build-isolation`, after installing the
dependencies in `requirements/ci.txt` with `--require-hashes`.

Then:

1. Run `commitguard github setup` in the repository. It inspects the workflow
   files locally (no API access) and prints the required check name and the
   branch protection steps.
2. In **Settings → Rules → Rulesets** (or **Branches**) for the protected
   branch: require a pull request, block direct pushes, and require the status
   check **`commitguard`** (GitHub lists it after the workflow has run once).
3. Protect the workflow: require code owner review for `.github/workflows/`,
   `action.yml`, `.commitguard.yaml` and `requirements/`, or require the
   workflow from a separate repository with an organization ruleset. A pull
   request can otherwise edit the workflow that checks it.

`commitguard doctor` reports workflow issues too, and always states "branch
protection cannot be verified locally".

## This repository

`.github/workflows/commitguard.yml` installs CommitGuard from a **trusted commit
of this repository** (the pull request base, the merge queue base, the commit
before a push, or the default branch tip) using `git worktree`, so a pull request
cannot weaken the scanner or its bundled rules. If the trusted commit predates
CI support, it falls back to the checked-out change with a warning (bootstrap
only).

## Inputs and outputs

| Input | Default | Meaning |
|---|---|---|
| `config` | `.commitguard.yaml` / `.yml` | policy file path, read from the trusted commit |
| `fail-on` | `block` | `warn` also fails on warnings |
| `max-commits` | `10000` | more commits fail the check instead of being analysed partially |
| `python-version` | `3.12` | Python used to run CommitGuard |

Outputs: `result` (`allow`, `warn`, `block`), `commits`, `violations`,
`warnings`.

## Security properties

| Property | GitHub Actions |
|---|---|
| Runs | on a GitHub-hosted runner, in each repository's CI |
| Policy | read from the trusted commit; changes in the pull request never apply to it |
| Rules | bundled in the pinned CommitGuard source; a repository's `rules/` is never read |
| Token | `contents: read`; `persist-credentials: false`; no secrets |
| Forks | `pull_request` only; `pull_request_target` fails the check |
| Merge prevention | only when branch protection or a ruleset requires `commitguard` |
| Push events | run after the commits are already on GitHub: detection, not prevention |
| Main weakness | a pull request can modify the workflow file; mitigate with CODEOWNERS or a ruleset-required workflow |

## Upgrading and rolling back

Change the pinned SHA in `uses: oyinlola-tech/commitguard@<sha>` deliberately:
review the diff between the old and new commit, then update the SHA. To roll
back, restore the previous SHA. Rules and behaviour are determined entirely by
the pinned commit; nothing is downloaded at runtime apart from the hash-pinned
dependencies.

## Running with the GitHub App

Both can run. A pull request then shows `commitguard` (Action) and
`commitguard-app` (App); they use the same scan code and reach the same decision
for the same commits, policy and rules. Require the one you rely on. See
[../github-app.md](../github-app.md#running-the-app-and-the-action-together).

## Troubleshooting

See [troubleshooting.md](troubleshooting.md#github-actions).
