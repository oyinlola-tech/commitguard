# GitHub enforcement

> Status: **implemented (Phase 4)** as a GitHub Actions check: `commitguard ci
> github`, the composite Action (`action.yml`), a workflow template
> (`commitguard init --github`) and local guidance (`commitguard github setup`).
> PR comments are not implemented. A webhook-driven **GitHub App** that
> publishes Check Runs is available as an alternative or complement, see
> [github-app.md](github-app.md). CommitGuard **cannot configure or verify
> branch protection**.

## Why a second layer

Local hooks (Phase 3) run on the developer's machine and can be bypassed with
`git push --no-verify`, by deleting hooks, or by pushing from another clone.
The GitHub layer runs the **same detection and policy engine** on GitHub's
infrastructure, where the contributor does not control execution.

```text
                 Developer
                     │  git commit / git push
                     ▼
            Local CommitGuard hooks      fast feedback; bypassable
                     │
                     ▼
                   GitHub
                     │  pull_request / merge_group / push
                     ▼
         GitHub Actions: commitguard ci github
                     │  same Analyzer: detectors + policy evaluator
              ┌──────┴──────┐
            PASS           FAIL
              │              │
        check succeeds   check fails
              │              │
              ▼              ▼
   mergeable (if required)   merge blocked — ONLY when branch protection or a
                             ruleset requires the "commitguard" check
```

**Adding the workflow alone does not prevent anything.** A failing check blocks
a merge only when repository settings require it (see
[Branch protection](#branch-protection-required)).

## Quick start

### Using the Action from another repository

```bash
commitguard init --github \
  --action-repository OWNER/commitguard \
  --action-ref <40-character commit SHA of the CommitGuard repository>
```

This writes `.commitguard.yaml` (if missing) and
`.github/workflows/commitguard.yml` (never overwrites an existing workflow):

```yaml
name: CommitGuard
on:
  pull_request:
  merge_group:
  push:
permissions:
  contents: read
jobs:
  commitguard:
    name: commitguard
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: OWNER/commitguard@<commit sha>
```

The Action must be pinned to a full commit SHA; `init` refuses tags and
branches. CommitGuard is not published to PyPI, and no package index name is
used anywhere, which avoids dependency confusion with a similarly named package.

Then configure branch protection and run `commitguard github setup`.

### This repository

`.github/workflows/commitguard.yml` dogfoods the check. It installs CommitGuard
from a **trusted commit of this repository** (PR base / commit before the push
/ default branch) using `git worktree`, so a pull request cannot weaken the
scanner or its bundled rules. While the trusted commit predates CI support
(bootstrap only), it falls back to the checked-out source with a warning.

## Action reference

| Input | Default | Meaning |
|---|---|---|
| `config` | `.commitguard.yaml` / `.yml` | Repository-relative policy file, **read from the trusted commit**, never the checkout |
| `fail-on` | `block` | `block`: only blocking findings fail. `warn`: warnings fail too |
| `max-commits` | `10000` | More commits than this fail the check (never analysed partially) |
| `python-version` | `3.12` | Python used to run CommitGuard (3.12+) |

| Output | Values |
|---|---|
| `result` | `allow`, `warn`, `block` |
| `commits`, `violations`, `warnings` | integers |

What the Action does:

1. `actions/setup-python` (pinned) with `update-environment: false`;
2. creates a venv under `$RUNNER_TEMP` and installs **hash-pinned** dependencies
   (`requirements/ci.txt`, `pip install --require-hashes --no-deps`), then
   CommitGuard from the Action's own source (`--no-deps --no-build-isolation`,
   so no unhashed build dependency is downloaded);
3. runs `python -P -m commitguard ci github` in the workspace (`-P`: a
   `commitguard/` directory in the checked-out repository cannot shadow the
   installed package). Inputs reach the script through `env:`, never `${{ }}`
   interpolation.

## Status check name

The required status check is the job name: **`commitguard`**. GitHub displays
it as `CommitGuard / commitguard (pull_request)` in the pull request, and lists
it as `commitguard` when you add required checks (after it has run once). If you
rename the job's `name:`, the check name changes with it.

## What is scanned

| Event | Commits analysed | Policy read from |
|---|---|---|
| `pull_request` | `git rev-list head ^base` from `pull_request.head.sha` / `base.sha` | the **base commit's** tree |
| `merge_group` | `head ^base` from `merge_group.head_sha` / `base_sha` | the merge queue base commit |
| `push`, `before` present | `after ^before` | the `before` commit |
| `push`, new branch / tag, or `before` not in the clone (force push) | `after ^<default branch tip>` | the default branch tip |
| `push`, new default branch (initial push) with no trusted commit | everything reachable from `after`, bounded by `max-commits` | built-in defaults |
| `push`, ref deleted | nothing | — |
| annotated tag push | peeled to its commit | as above |
| tag of a tree/blob | nothing | — |

- **Not only the latest commit:** every commit in the range is analysed, and
  all findings are reported (output capped at 50 findings in text, 10
  annotations per level; `--format json` / `--report-file` contain everything).
- **GitHub's synthetic merge commit** (`refs/pull/N/merge`, what
  `actions/checkout` checks out for pull requests) is never analysed; the SHAs
  come from the event payload, not from `HEAD`.
- **Merge commits** inside the PR are analysed; history already on the base is not.
- **Empty ranges** pass without scanning history.
- `fetch-depth: 0` is required. If a base or head commit is missing, the check
  fails with an instruction to fetch full history. CommitGuard never fetches.
- `pull_request_target` and unsupported events **fail** the check.

Detection is identical to `commitguard scan base..head` with the same policy;
tests compare finding fingerprints between the two.

## Policy trust model

**Decision (option A): the policy that evaluates a change comes from a trusted
commit, never from the change.**

A pull request that edits `.commitguard.yaml` from `action: block` to
`action: allow` is still evaluated with `block`. The check prints and annotates
a notice that the configuration changes and that the new policy takes effect
once merged. The same applies to a `config:` input path: it is resolved in the
trusted commit's tree (a file that only exists in the PR is an error).

Consequences:

- A policy change takes effect for changes made after it lands on the base branch.
- Loading ignores the user's global configuration: CI results depend only on
  the trusted commit and the CommitGuard version.
- Configuration is validated strictly (unknown keys, policies, actions and
  types, duplicate keys, YAML tags and aliases are errors); invalid trusted
  configuration **fails** the check. Symlinks, submodules and oversized files
  are rejected.

The abstraction is `commitguard.config.sources.PolicySource`
(`working_tree`, `revision`, `builtin`); organisation-level sources can be added
as further layers without changing detection or evaluation.

## Rule trust model

Detection rules always come from the **installed CommitGuard package**
(`commitguard/rules/data`, built from `rules/` at packaging time). A
repository's own `rules/` directory is never read, so a pull request that
removes `Claude` from `rules/ai-identities.yaml` has no effect (tested).
Repository-specific custom rules are not supported. To change rules, change the
CommitGuard version you pin.

Nothing is downloaded at runtime: rules are deterministic for a given pinned
Action commit.

## Exit codes and failure behaviour

| Exit | Meaning | Job |
|---|---|---|
| 0 | allowed (warnings are reported; with `fail-on: warn` they fail) | success |
| 1 | blocked by policy | failure |
| 2 | could not evaluate: invalid/missing policy, malformed or unsupported event, missing commits, Git unavailable, too many commits, internal error | failure |

Any failure to install or import CommitGuard also fails the job (non-zero). No
error path prints a passing result:

```text
CommitGuard could not verify repository policy.
Reason: .commitguard.yaml: invalid configuration: ...
Security validation could not be completed.
Result: FAILED
```

## Output

Text log (untrusted values escaped; workflow commands disabled around them):

```text
CommitGuard
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Repository: example/project
Event: pull_request (PR #12)
Range: 1a2b3c4d5e6f..8e71c2a0b1c2
Policy: pull request base (1a2b3c4d5e6f)
✓ policy loaded
✓ commits scanned: 7
✓ detection completed
Findings:
  ai_coauthor: 1
Violations: 1
Warnings: 0
Allowed: 6

✗ AI coauthor detected
  Commit:   8e71c2a  feat: implement authentication
  Evidence: Claude <noreply@anthropic.com> (Co-authored-by trailer)
  Rule:     ai_coauthor
  Severity: high
  Action:   block

Result: BLOCK

Remediation: Please update the listed commits so that they comply with the
repository's contribution policy, then push the updated branch. CommitGuard
does not automatically rewrite Git history.
```

Also produced in Actions: `::error` / `::warning` / `::notice` annotations, a
Markdown job summary (`$GITHUB_STEP_SUMMARY`) and step outputs. JSON:
`commitguard ci github --format json` or `--report-file PATH` (schema version 1,
with a `ci` section). Findings carry rule ID, severity, message, evidence and
commit, so SARIF export can be added later; it is not implemented.

CI never rewrites commits, removes trailers or force-pushes.

## Branch protection (required)

For authoritative enforcement on `main` (or any protected branch), configure in
repository **Settings → Rules → Rulesets** (or **Branches**):

```text
main
├── direct pushes blocked (restrict updates; no bypass list, or a minimal one)
├── pull requests required before merging
├── required status check: commitguard      ← CommitGuard
├── other required checks
└── if using a merge queue: keep the merge_group trigger in the workflow
```

Then:

```text
Developer ─▶ PR ─▶ CommitGuard ─▶ PASS ─▶ Merge
                        │
                        └─▶ FAIL (AI attribution) ─▶ PR cannot merge
```

`commitguard github setup` prints these steps and the check name.
`commitguard doctor` reports "branch protection cannot be verified locally":
it inspects workflow files only.

With the GitHub App installed, the [dashboard](dashboard.md#repository-protection-and-enforcement-status)
can read evidence of this configuration: whether a workflow on the default
branch runs CommitGuard (**GitHub Actions: detected**), and whether a ruleset
or visible branch protection requires `commitguard` or `commitguard-app`
(**Required check: required**). It reports `unknown` whenever GitHub does not
show enough to be sure, and it never changes these settings.

### Push checks run after the fact

```text
push ─▶ GitHub receives the commits ─▶ workflow runs ─▶ check fails
```

A failing push check means the commits **are already on GitHub**. It is a
detection signal (and protects later pushes from building on it silently), not
prevention. Prevention needs: protected branch + required pull requests +
required `commitguard` check.

### Protect the workflow itself

On `pull_request`, GitHub runs the workflow definition from the pull request's
merge commit. A pull request can therefore edit
`.github/workflows/commitguard.yml` (for example to `exit 0`) and still produce
a passing check named `commitguard`. Mitigate with one of:

- **CODEOWNERS + "Require review from Code Owners"** for `.github/workflows/`,
  `action.yml`, `.commitguard.yaml` and `requirements/`;
- **organisation rulesets that require a workflow from a separate repository**,
  which pull requests in the protected repository cannot modify.

This is a GitHub platform property; CommitGuard cannot enforce it.

## Forks and permissions

- Event: `pull_request` only. Fork pull requests get a read-only token and no
  secrets; CommitGuard needs neither. `pull_request_target` is rejected by the
  scanner and flagged by `doctor`.
- Permissions: `contents: read`. That is required by `actions/checkout` to read
  the repository; the scanner itself reads only local Git objects. No
  `pull-requests`, `checks`, `statuses` or `contents: write`.
- `persist-credentials: false`: the token is not left in `.git/config`.
- No `concurrency: cancel-in-progress`: a cancelled push run would leave that
  push's commits unchecked (flagged by `doctor`).
- No step executes repository-controlled code: CommitGuard is installed from
  the pinned Action (or, in this repository, a trusted commit) and only reads
  Git data. Commit messages, author names, branch names and trailers are never
  interpolated into shell commands (tested with `$(touch /tmp/commitguard-pwned)`
  and friends).

## Supply chain policy

- Third-party actions are pinned to full commit SHAs (verified against the
  upstream tags noted in comments). Tests fail if a workflow or `action.yml`
  uses an unpinned action.
- Dependencies are installed from `requirements/ci.txt` with `--require-hashes`
  (sha256 of every published file for each pinned version). Regenerate it when
  dependencies change (see `requirements/ci.in`).
- CommitGuard itself is installed from source (the pinned Action or a trusted
  commit), never by package name from an index.
- Update pins deliberately: resolve the new tag to its commit, review the diff,
  update the SHA and the version comment together.

## GitHub Actions or the GitHub App

| | Actions (this page) | GitHub App ([github-app.md](github-app.md)) |
|---|---|---|
| Advantages | simple, repository-local, no service or credentials | centralised and organisation-wide, webhook-driven, Checks API, mandatory policy |
| Limitations | runs inside CI; per-repository setup; a PR can edit its own workflow | needs deployment, App credentials and a webhook endpoint; no merge queue support yet |
| Check name | `commitguard` | `commitguard-app` (pull requests), `commitguard-app/push` |
| Dashboard | not recorded (results live in the workflow run) | scans, findings, violations, policy versions and audit history |

Both use `ScanService` and the same trust model, so for the same commits,
policy and rules they reach the same ALLOW, WARN or BLOCK decision (covered by
`tests/integration/github/app/test_app_action_consistency.py`). If both run,
pull requests show two checks. Require the one you rely on, and see
[running both](github-app.md#running-the-app-and-the-action-together) for the
recommended transition. CommitGuard never removes an existing workflow.

## Limitations

- Branch protection, rulesets and CODEOWNERS are not configured or verified by
  CommitGuard.
- A push-triggered check cannot prevent commits from reaching GitHub.
- A pull request can modify the workflow that checks it (see above).
- Squash and rebase merges create new commits at merge time; the merge message
  can be edited by the person merging. The post-merge `push` check detects
  attribution added there, after it has landed.
- Remote-tracking refs are trusted to describe what the default branch already
  contains. For new-branch pushes, commits on the default branch are not re-checked.
- The initial push of a default branch uses built-in default policies.
- Annotated tag messages are not analysed, only the commits they point to.
- Windows and macOS runners: the Action's shell steps are bash; the CI matrix
  runs the test suite on all three platforms, but the Action has only been
  exercised on Linux.
