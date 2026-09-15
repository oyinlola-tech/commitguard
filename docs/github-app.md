# CommitGuard GitHub App

> Status: Phase 5, pre-alpha. The App service is implemented and tested against
> real Git repositories and an offline model of the GitHub API. It has **not**
> yet been exercised against github.com by this project's test suite. Treat a
> first deployment as a trial next to the existing GitHub Action.

The GitHub App is a third adapter around the CommitGuard core, next to the Git
hooks and the GitHub Action. It receives GitHub webhooks, fetches the commits an
event introduces, evaluates them with the **same detection engine and policy
evaluator**, and publishes a GitHub **Check Run**.

```text
GitHub ──HTTPS webhook──▶ POST /webhooks/github
                             │ signature (HMAC-SHA256) · delivery-ID dedup · validation
                             ▼
                        scan job (stored) ──▶ queue ──▶ ScanWorker
                                                          │ installation token (1 repo, least privilege)
                                                          │ metadata-only Git fetch (no checkout)
                                                          ▼
                                                    ScanService  ── same code as `commitguard ci github`
                                                          │
                                                   Detection ▶ Policy
                                                          ▼
                                             Check Run "commitguard-app"
                                                          ▼
                                   Branch protection / ruleset (configured by you)
```

## Why an App, and how it differs from the Action

| | GitHub Action (Phase 4) | GitHub App (Phase 5) |
|---|---|---|
| Runs | in each repository's CI | as a service you operate |
| Setup | a workflow file per repository | install once on an account or organisation |
| Credentials | none (`contents: read`) | App ID, private key, webhook secret |
| Infrastructure | none | HTTPS endpoint, a host, persistent disk |
| Result | job status `commitguard` | Check Run `commitguard-app` via the Checks API |
| Central policy | per repository | optional **mandatory policy** applied to every repository |
| Repository can edit the check | yes, via the workflow file (mitigate with CODEOWNERS) | no: the App runs outside the repository |
| Merge queues (`merge_group`) | supported | not supported yet |

**Advantages of the App:** centralised, organisation-wide installation,
webhook-driven, first-class Checks API output, and a policy floor that
repositories cannot weaken.

**Limitations of the App:** you must deploy and operate it, protect its
credentials, and expose a webhook endpoint.

Both can run at the same time; see [Running the App and the Action together](#running-the-app-and-the-action-together).

## What the App does and does not do

It **does**: verify webhooks, track installations, scan pull request and push
commits, create and update Check Runs, record audit events, and expose health
endpoints.

It **never**: checks out or builds repository code, runs repository scripts,
installs packages from a repository, amends or force-pushes commits, deletes
branches, rewrites history, removes co-authors, comments on pull requests, or
modifies files. It does not configure or verify branch protection.

## 1. Create the GitHub App

In GitHub, create a new GitHub App under your user or organisation developer
settings. Use these values (field names can vary slightly as GitHub updates
its UI):

- **GitHub App name:** e.g. `CommitGuard (your-org)`. App names are globally
  unique on GitHub.
- **Homepage URL:** any URL describing your deployment.
- **Webhook:** active.
- **Webhook URL:** `https://<your-host>/webhooks/github`. Must be HTTPS.
- **Webhook secret:** a long random value, for example
  `python -c "import secrets; print(secrets.token_hex(32))"`. CommitGuard
  refuses secrets shorter than 16 characters.
- **Callback URL / user authorization:** not used. CommitGuard never acts on
  behalf of a user, so no client secret or OAuth flow is needed.

## 2. Permissions (least privilege)

Repository permissions:

| Permission | Level | Why |
|---|---|---|
| Checks | Read and write | create and update the CommitGuard Check Run |
| Contents | Read-only | fetch commit objects; receive `push` events |
| Metadata | Read-only | mandatory for all Apps; look repositories up by ID |
| Pull requests | Read-only | receive `pull_request` events; confirm a PR is still open |

Grant nothing else. CommitGuard does **not** need Contents write,
Administration, Actions, Workflows, Members or any organisation permission.
`commitguard github validate` reports missing permissions as errors and
unnecessary ones as warnings.

Every installation token CommitGuard requests is additionally **down-scoped**
to exactly these four permissions and to the single repository being scanned.

## 3. Subscribe to events

Subscribe to **Pull request** and **Push**. GitHub always sends
`installation` and `installation_repositories` events to Apps.

| Event | Handling |
|---|---|
| `installation` (`created`, `deleted`, `suspend`, `unsuspend`, `new_permissions_accepted`) | record, disable or re-enable the installation |
| `installation_repositories` (`added`, `removed`) | record access; removed repositories lose their mirror and cached tokens |
| `pull_request` `opened`, `synchronize`, `reopened` | scan `base..head` (all PR commits) |
| `pull_request` `edited` with a base branch change | scan again |
| `pull_request` `closed` | not merged: cancel queued scans; merged: audit event only |
| `push` to a branch | scan `before..after` (new branch: limited by the default branch) |
| `push` deleting a branch, tags | ignored |
| everything else, including `ping` | acknowledged and ignored |

## 4. Generate a private key

Generate a private key for the App and download the `.pem` file. Store it as
a secret (a file readable only by the service user, or your secret manager's
file mount). Never commit it.

```bash
install -m 600 -o commitguard ~/Downloads/commitguard.*.private-key.pem /etc/commitguard/app.pem
```

CommitGuard parses the key at start-up and fails closed if it is malformed,
encrypted, not RSA, or shorter than 2048 bits. The key never appears in logs,
errors, API responses or Check Runs.

## 5. Configure the environment

The App needs the optional dependency set:

```bash
pip install 'commitguard[app]'   # adds cryptography (JWT signing); nothing else
```

| Variable | Required | Meaning |
|---|---|---|
| `COMMITGUARD_GITHUB_APP_ID` | yes | numeric App ID |
| `COMMITGUARD_GITHUB_PRIVATE_KEY_FILE` | one of these two | path to the PEM file (preferred) |
| `COMMITGUARD_GITHUB_PRIVATE_KEY` | | PEM text (escaped `\n` accepted) |
| `COMMITGUARD_GITHUB_WEBHOOK_SECRET_FILE` | one of these two | file containing the webhook secret |
| `COMMITGUARD_GITHUB_WEBHOOK_SECRET` | | the webhook secret |
| `COMMITGUARD_APP_DATA_DIR` | yes | absolute path for the state database and mirrors (created `0700`) |
| `COMMITGUARD_APP_MANDATORY_POLICY_FILE` | no | mandatory policy applied to every repository |
| `COMMITGUARD_APP_WORKERS` | no | scan worker threads (default 2) |
| `COMMITGUARD_APP_RETENTION_DAYS` | no | retention of deliveries, jobs, audit events and unused mirrors (default 30) |
| `COMMITGUARD_APP_MAX_COMMITS` | no | commits per scan before failing closed (default 10000) |

`COMMITGUARD_GITHUB_CLIENT_ID` and `COMMITGUARD_GITHUB_CLIENT_SECRET` are not
used. Errors name the variable that is wrong, never its value.

## 6. Run the service

```bash
commitguard github serve --host 127.0.0.1 --port 8080
```

This starts the webhook endpoint, the scan workers and the maintenance thread
(recovery of abandoned jobs, retention). It listens on plain HTTP on a local
address: put a TLS-terminating reverse proxy in front of it. See
[deployment.md](deployment.md) for production options.

Endpoints:

| Method and path | Purpose |
|---|---|
| `POST /webhooks/github` | GitHub webhooks |
| `GET /health` | process is alive (`{"status": "ok"}`) |
| `GET /ready` | configuration loaded, database reachable, workers running, queue not saturated |

## 7. Install the App

Install the App on your account or organisation and choose **all** or
**selected** repositories. CommitGuard never assumes it can access a repository:
before every scan it asks GitHub for a token limited to that repository, and
GitHub refuses if the installation does not include it.

## 8. Verify the installation

```bash
commitguard github validate
commitguard github validate --installation-id 12345678
```

```text
CommitGuard GitHub Configuration

✓ App ID
✓ Private key
✓ Webhook secret
✓ Data directory
✓ GitHub authentication: App commitguard-your-org
✓ Required permissions: checks: write, contents: read, metadata: read, pull_requests: read
✓ Webhook events
✓ Installation access: installation 12345678: 3 repository(ies) accessible

Status:
READY
```

`--offline` checks only the local configuration. `validate` never prints the
key, the webhook secret, JWTs or installation tokens.

## 9. Test a webhook locally

`webhook-test` verifies and normalises a payload without contacting GitHub,
scanning or running anything:

```bash
commitguard github webhook-test payload.json --event pull_request \
    --signature "sha256=<value of X-Hub-Signature-256>"
```

Recent deliveries (headers and payload) are visible in the App's advanced
settings on GitHub, which is useful for copying a real payload and signature.

## 10. Test a pull request scan

1. Open a pull request with an ordinary commit: the `commitguard-app` check
   goes queued → in progress → **success**.
2. Push a commit containing `Co-authored-by: Claude <noreply@anthropic.com>`:
   the check becomes **failure** with the rule, commit, identity and
   remediation.
3. Rewrite the branch without that trailer and force-push: the new head
   passes. CommitGuard itself never rewrites anything.

## Making the check a merge requirement

A failing Check Run **does not block anything by itself**. In a branch
protection rule or ruleset for your protected branch:

1. require a pull request before merging, and do not allow bypass;
2. require status checks to pass and add **`commitguard-app`**, selecting the
   CommitGuard App as the expected source if GitHub offers that option;
3. restrict direct pushes.

CommitGuard cannot configure or verify these settings. `commitguard-app/push`
is informational: a push event arrives after the commits are already on GitHub.

## Check Runs

| Scan result | Conclusion | Output |
|---|---|---|
| ALLOW | `success` | commits scanned, policy source, scan ID |
| WARN only | `success` | as above, plus the warnings |
| BLOCK | `failure` | violations (rule, commit, identity, action, remediation) |
| invalid trusted or mandatory policy | `failure` | configuration error |
| GitHub, Git or storage failure | `failure` | "could not verify repository policy" |
| fetch timeout | `timed_out` | as above |

- **Lifecycle:** each check is created `queued`, moves to `in_progress` with the
  commit count, then `completed` with a conclusion.
- **Output limits:** at most 20 findings are listed. Larger results show counts
  and the exact `commitguard scan <base>..<head> --format json` command for the
  full machine-readable report.
- **No annotations:** commit metadata has no file or line, so CommitGuard does
  not attach findings to invented locations.

**Check names.** Pull requests publish `commitguard-app`; pushes publish
`commitguard-app/push`. Separate names mean a push scan, which covers only the
newly pushed commits, can never replace a pull request scan, which covers every
commit in the pull request, on the same SHA.

**Correct SHA, no stale results:**

- Every Check Run is created with the exact commit SHA that was fetched and
  scanned. The worker verifies that the planned range ends at that SHA before
  publishing.
- Scan jobs have a monotonically increasing sequence. For a given repository,
  commit and check name, a newer job takes ownership of the Check Run. An older
  job that finishes later cannot update it, and it reuses the existing run
  instead of creating duplicates.
- A queued pull request scan is cancelled when a newer event for the same pull
  request exists, or when the pull request has been closed.

**Duplicates and replays:**

- Delivery IDs are recorded. A repeated delivery is acknowledged and ignored.
- A reused delivery ID with a different payload is rejected with `409`.
- The same event under a new delivery ID maps to an existing scan job and does
  not run again. The exception is a job that previously ended in an error:
  reopening the pull request or pushing retries it.

## How policy is trusted

The App uses the Phase 4 trust model unchanged, through the same code:

- **Pull request:** commits `head ^base`, with policy read from
  `.commitguard.yaml` **at the base commit**.
- **Push:** commits `after ^before`, with policy from `before`. For a new
  branch, the default branch tip (from the GitHub API) is used instead. If no
  trusted commit exists, the built-in defaults apply.
- **Configuration in the evaluated commits is never applied.** When those
  commits would weaken a policy, the Check Run shows **"Security policy
  modification detected"** with each change (for example
  `ai_coauthor: block -> allow`), and an audit event is recorded.
- **Detection rules** always come from the installed CommitGuard package. A
  repository cannot remove bundled AI identities.
- **Mandatory policy** (`COMMITGUARD_APP_MANDATORY_POLICY_FILE`, same schema as
  `.commitguard.yaml`) is applied after the trusted configuration and can only
  tighten it. Every policy it names is enabled, at the stricter of the two
  actions. `enabled: false` in a mandatory policy is rejected. Example:

  ```yaml
  version: 1
  policies:
    ai_coauthor:
      action: block
    ai_identity:
      action: block
    bot_identity:
      action: warn
  ```

  If a repository sets `ai_coauthor: allow`, the result is still **BLOCK**.

## Fork pull requests

Fork pull requests arrive as `pull_request` events to the base repository's
installation. The App:

- fetches the fork's head commit from the base repository;
- evaluates it with the base commit's policy;
- uses a token limited to the base repository and read-only permissions plus
  Checks write.

No secret is ever exposed to fork code, because no fork code runs. Pull request
titles, descriptions, branch names and commit metadata are untrusted data: they
are validated, escaped for Markdown, and never executed.

## How commits are read (no checkout)

The App keeps a bare, partial mirror per repository at
`<data dir>/mirrors/<installation id>/<repository id>.git`:

- It fetches the exact SHAs from the verified event with `--filter=blob:none`.
  Commits and trees are stored; **file contents are not**. The only exception is
  the CommitGuard configuration file at the commits being evaluated.
- It never checks out files: there is no work tree and no hooks
  (`--template=`, hooks path set to the null device).
- Fetches allow only HTTPS, follow no redirects, use no credential helpers,
  fetch no submodules or tags, and run no automatic GC. Analysis runs with
  `GIT_NO_LAZY_FETCH=1`, so it never touches the network.
- The installation token is passed as an HTTP header through `GIT_CONFIG_*`
  environment variables. It never appears in the URL, the command line or the
  mirror's configuration.

Git objects are read rather than GitHub API commit lists because the pull
request commits API returns at most 250 commits. Reading objects also makes the
App analyse byte-for-byte the same data as the Action and the hooks. The
consistency tests check that both reach the same decisions.

## How secrets are protected

- **Wrapping and redaction.** Credentials are wrapped as soon as they are read,
  so their `repr` is masked. They are registered with a redactor that scrubs
  logs, error messages and stored text. It also removes credential-shaped
  strings (`ghs_…`, JWTs, PEM blocks, `Authorization` values) that were never
  registered.
- **Lifetimes.** JWTs live 9 minutes. Installation tokens are kept in memory
  only, dropped five minutes before they expire, and discarded when an
  installation or repository is removed. They are never written to disk.
- **HTTP client.** It only speaks HTTPS to `api.github.com` and refuses
  redirects. Pagination links must stay on the API host.
- **Tested surfaces.** A test forces errors that echo credentials back and
  checks logs, Check Runs, HTTP responses, jobs, audit events, the database and
  the mirror configuration for leaks.

## Running the App and the Action together

If both run, a pull request shows two checks: `commitguard` from the Action and
`commitguard-app` from the App. They evaluate the same commits with the same
code, so their results agree.

| Option | Use when |
|---|---|
| **A. Action only** | a few repositories; no service to operate |
| **B. App only** | many repositories or organisation-wide enforcement; you can operate the service (not for merge queues yet) |
| **C. Both** | transition from A to B, or defence in depth |

**Recommendation:** start with A. Add the App in parallel (C) and require
`commitguard-app` in branch protection only after it has run reliably. Then
choose whether to remove the Action workflow; CommitGuard never removes it for
you. Keep the Action if you use merge queues.

## Operational notes

- **Structured logs.** Logs are JSON lines on stderr. Webhook, scan, API and
  Check Run records share `delivery_id`, `job_id`, `scan_id`, `installation_id`
  and `repository` fields.
- **Audit events.** Types include installation created or removed, repositories
  added or removed, webhook rejected, scan queued, repository scanned, scan
  passed, failed, cancelled or errored, policy violation, policy modification,
  configuration error, authorization denied, and pull request merged. They are
  stored in the state database, scoped by installation and repository, and
  contain IDs, SHAs, rule IDs, fingerprints and counts. They never contain
  commit messages, names or e-mail addresses.
- **Job states.** `queued`, `running`, `passed`, `failed` (policy blocked),
  `error` (could not complete), `cancelled`. `failed` and `error` both fail the
  check, but they are recorded separately.
- **Retention.** Deliveries, finished jobs, Check Run ownership records, audit
  events, deleted installations and unused mirrors are purged after
  `COMMITGUARD_APP_RETENTION_DAYS` (default 30). Nothing is kept forever by
  default. Delivery IDs older than the retention period are forgotten: a
  replayed delivery that old would re-run a scan, which produces the same result
  for the same SHA.
- **Metrics.** Counters are kept in memory and are not exposed over HTTP:
  `webhooks_received`, `webhooks_rejected`, `webhooks_duplicate`,
  `scans_queued`, `scans_started`, `scans_completed`, `scans_failed`,
  `scans_cancelled`, `policy_violations`, `github_api_errors`,
  `github_rate_limits`.

## Local development

```text
GitHub ──webhook──▶ HTTPS tunnel of your choice ──▶ commitguard github serve (127.0.0.1:8080)
```

1. Create a development App whose webhook URL is your tunnel's HTTPS URL plus
   `/webhooks/github`. Any tunnel works; GitHub's own webhook documentation uses
   a relay service for this. No tunnel provider is a CommitGuard dependency.
2. Export the variables from step 5 with a throwaway data directory.
3. Run `commitguard github validate`, then `commitguard github serve`.
4. Install the development App on a test repository and open a pull request.

For work without GitHub, the test suite contains a full offline model:
`tests/integration/github/app/` (fake API, real Git, signed webhooks).

## Limitations

- **Real GitHub.** The test suite has not run against github.com. Fetching by
  SHA over HTTPS, Checks API field limits and real webhook payload variants are
  covered by the offline model only.
- **Branch protection.** It is not configured or verified. A failed check
  prevents nothing unless GitHub requires it.
- **Merge queues.** `merge_group` is not handled by the App. If a queue
  requires `commitguard-app`, entries will wait forever: use the Action for
  merge queues.
- **Several open PRs with the same head.** When pull requests into different
  base branches share a head SHA, they share one `commitguard-app` check on
  that SHA; the most recently started scan owns it. This follows GitHub's model
  of checks per commit.
- **Re-running a check.** "Re-run" from the GitHub UI (`check_run.rerequested`)
  is not handled. After an error, push a new commit or reopen the pull request.
- **Single host.** The SQLite state store and the in-process queue support one
  host (several processes on that host share the database safely). Running
  several hosts needs a shared database implementation of the storage
  interfaces, which is not provided.
- **No TLS in the service.** HTTPS must come from a reverse proxy. The built-in
  server is intended for development and small deployments.
- **Sizes.** Mirrors keep commit and tree objects (file names, not contents) for
  active repositories. Very large monorepos need proportional disk space and
  fetch time (fetches time out after 10 minutes).
- **Commit messages.** Extremely large commit messages are bounded only by the
  per-scan commit limit and Git timeouts, not by a per-message size limit.
- **Private key storage.** The key is held in process memory for the life of
  the service. Python cannot guarantee that memory is wiped.
- **Tested platforms.** The App service has been tested on Linux only. The core
  CLI and hooks are unchanged.
