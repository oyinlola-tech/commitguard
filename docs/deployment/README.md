# Deploying CommitGuard

CommitGuard 0.1.2 is pre-alpha software. This section describes the ways it
can be deployed today, what each way protects against, and what it does not.
Every mode runs the same detection engine and policy evaluator; they differ in
where the check runs, who controls it, and whether it can prevent a merge.

> **Installation.** `pipx install commitguardian`. The PyPI projects named
> `commitguard` and `commitguard-cli` belong to other authors; installing either
> gets you someone else's code. Pinning to a full commit SHA is also supported -
> see [Installing from source](#installing-from-source).

## Deployment modes

| Mode | Page | Status |
|---|---|---|
| Local Git hooks | [local.md](local.md) | **Implemented** |
| GitHub Actions check | [github-actions.md](github-actions.md) | **Implemented** |
| GitHub App service (webhooks, Check Runs, dashboard) | [github-app.md](github-app.md) | **Experimental**: tested against an offline model of the GitHub API, not against github.com |
| Organization governance (inside the App service) | [organization.md](organization.md) | **Experimental**: same test basis as the App |
| Several hosts sharing one database, or an external queue | - | **Planned**: not implemented; the App supports one host |
| Container images, packages, release artifacts | - | **Planned**: none are published |

Operating the App service yourself: [self-hosted.md](self-hosted.md) (the only
way the App runs) and [production.md](production.md) (what an operator must
provide, and what has and has not been validated). Error messages and fixes:
[troubleshooting.md](troubleshooting.md). Incident procedures:
[../operations/runbook.md](../operations/runbook.md).

### Local only

```text
Developer ──git commit / git push──▶ Git hooks ──▶ CommitGuard (commitguard hook <name>)
                                                        │
                                          ALLOW/WARN ─▶ exit 0 ─▶ Git continues
                                          BLOCK      ─▶ exit 1 ─▶ Git stops
                                          error      ─▶ exit 2 ─▶ Git stops
```

Fast feedback before anything leaves the machine. Bypassable by anyone who
controls the clone (`--no-verify`, deleting hooks, another clone).

### GitHub Actions

```text
Developer ──push / pull request──▶ GitHub ──▶ GitHub Actions ──▶ CommitGuard (commitguard ci github)
                                                                      │
                                                     policy from the trusted base commit
                                                                      ▼
                                                   job "commitguard" succeeds or fails
                                                                      ▼
                                   merge blocked ONLY if branch protection / a ruleset requires it
```

Runs on GitHub's infrastructure with a read-only token. Uses `action.yml` (or,
in this repository, `.github/workflows/commitguard.yml`).

### GitHub App

```text
GitHub ──HTTPS webhook──▶ TLS reverse proxy ──▶ CommitGuard App (commitguard github serve)
                                                     │ signature check, delivery dedup, scan job in SQLite
                                                     ▼
                                               in-process queue ──▶ scan worker threads
                                                     │ installation token, metadata-only git fetch
                                                     ▼
                                               CommitGuard core (same code as the Action)
                                                     ▼
                                     Check Run "commitguard-app" via the Checks API
                                                     ▼
                                   merge blocked ONLY if branch protection / a ruleset requires it
```

A service you operate: one process, a SQLite database and repository mirrors
on local disk, and optionally the web dashboard on the same origin.

### Organization governance

```text
Organization (GitHub account that installed the App)
      │ administrators in the dashboard / API
      ▼
Central policy: organization · groups · repositories · security baseline
      │ drafts → approval → publication → staged rollout → rollback; exceptions
      ▼
Effective policy per repository (repository_effective_policies)
      │ applied by the App's scan workers
      ▼
Repositories: Check Run "commitguard-app"
```

Not a separate deployment: it is part of the App service and its database.
Without the App there is no central policy. The GitHub Action and the hooks
never read organization policy.

## Security properties and limitations

| | Local hooks | GitHub Actions | GitHub App | Organization governance |
|---|---|---|---|---|
| Where it runs | developer machine | GitHub-hosted runner, per repository | a host you operate | inside the App service |
| Who controls execution | the developer | GitHub; the workflow file lives in the repository | the operator | the operator; policy by organization administrators |
| Can be bypassed by | `--no-verify`, deleting hooks, `core.hooksPath`, another clone, editing `.commitguard.yaml` | a pull request editing its own workflow (mitigate with CODEOWNERS or a ruleset-required workflow); not requiring the check | not requiring the check; an administrator setting a repository to `monitor` mode (checks never fail) | same as the App; exceptions and rollbacks by authorised members (approved and audited) |
| Policy source | working tree `.commitguard.yaml` plus user configuration | the trusted commit (PR base, commit before a push) | trusted commit, then the optional mandatory policy file and the organization policy, which can only tighten | organization, group and repository layers; floors cannot be weakened by repositories |
| Credentials held | none | the job's `GITHUB_TOKEN` with `contents: read`; no secrets | App private key, webhook secret; client secret if the dashboard is on | same as the App; dashboard sessions |
| Prevents a merge | no | only when branch protection or a ruleset requires `commitguard` | only when branch protection or a ruleset requires `commitguard-app` | same as the App |
| Push to a branch | blocked before objects are sent (pre-push) | checked after the commits are on GitHub | checked after the commits are on GitHub (`commitguard-app/push` is informational) | same as the App |
| Merge queues | not applicable | `merge_group` trigger | with the optional **Merge queues: read** permission and `merge_group` event | same as the App |
| Infrastructure | none | none | host, TLS reverse proxy, persistent disk, process supervision, backups | same as the App |
| What fails when it is unavailable | hooks fail closed (block) if CommitGuard cannot run | job fails (non-zero exit) | no check is published for new commits (also when monitoring is paused); a required check stays pending and keeps blocking | same as the App; scans resolve policy directly if the cache is not current, and fail closed if that fails |
| Validation so far | test suite with real `git commit`/`git push`, run on Linux; CI configured for Linux, macOS, Windows | Action exercised on Linux runners; `ci github` covered by integration tests | integration tests with real Git and an offline model of GitHub; **not run against github.com** | integration tests against the same offline model |

Recommended layering: hooks for fast feedback, plus one server-side check
(Action or App) that branch protection requires. CommitGuard never configures
or verifies branch protection itself.

## Installing from source

No release tags, binaries, containers or package-manager distributions exist.
Pin a full 40-character commit SHA from
`https://github.com/oyinlola-tech/commitguard` and review what you pin.

```bash
# CLI and Git hooks, isolated with pipx
pipx install "git+https://github.com/oyinlola-tech/commitguard@<commit-sha>"

# GitHub App service (adds the 'app' extra: cryptography for JWT signing), in a virtual environment
python -m venv /opt/commitguard/venv
/opt/commitguard/venv/bin/python -m pip install \
    "commitguard[app] @ git+https://github.com/oyinlola-tech/commitguard@<commit-sha>"

# From a clone (development)
git clone https://github.com/oyinlola-tech/commitguard && cd commitguard
git checkout <commit-sha>
python -m pip install -e ".[app]"
```

Do **not** run `pip install commitguard` or `pip install 'commitguard[app]'`
without a source URL: both resolve to the unrelated PyPI project. Some
CommitGuard error messages still print `pip install 'commitguard[app]'`; read
that as "install the `app` extra from source" as shown above.

Requirements: Python 3.12 or newer. Git 2.31 or newer for hooks and CI; Git
2.45 or newer on a host that runs the App service.

## Detailed references

| Topic | Document |
|---|---|
| Hooks: installation, chaining, `doctor`, bypass | [../git-hooks.md](../git-hooks.md) |
| Actions: trust model, scanned ranges, branch protection | [../github-enforcement.md](../github-enforcement.md) |
| Creating and configuring the GitHub App | [../github-app.md](../github-app.md) |
| Dashboard | [../dashboard.md](../dashboard.md) |
| Organization governance | [../organization-governance.md](../organization-governance.md) |
| Failure behaviour and recovery | [../recovery.md](../recovery.md) |
| Notifications | [../notifications.md](../notifications.md) |
| Merge queues | [../merge-queue.md](../merge-queue.md) |
