# Security boundaries

What each CommitGuard component trusts, what it treats as untrusted, and where
that decision is enforced in the code. This page describes the code in this
repository as of 2026-09-17. For threats and evidence see the
[threat model](threat-model.md); for roles see the
[authorization matrix](authorization-matrix.md).

## Sources of truth

| Question | Answered by | Not answered by |
|---|---|---|
| Which policy evaluates a local commit? | the work tree's `.commitguard.yaml`, global configuration and `--config` (`working_tree` source in `src/commitguard/config/sources.py`) | nothing else; local results are advisory |
| Which policy evaluates a pull request, merge group or push in CI or the App? | the tree of a **trusted commit**: pull request or merge group base, push `before`, or the default branch tip for a new branch; built-in defaults when no trusted commit exists (`src/commitguard/services/ci.py`) | the commits being evaluated; their `.commitguard.yaml` changes are reported, not applied |
| What can repository policy not lower? | the operator's mandatory policy file (`COMMITGUARD_APP_MANDATORY_POLICY_FILE`, `src/commitguard/policies/mandatory.py`) and, for the App, organization floors, security baseline and approved exceptions (`src/commitguard/policies/governance.py`) | `.commitguard.yaml`, group or repository policy defaults |
| Which detection rules apply? | the rule data of the **installed** CommitGuard package (`src/commitguard/rules/loader.py`), plus organization rules stored in the App database (identity data only) | rule files in the repository being scanned |
| Who is the signed-in user, which installations and repositories can they see? | GitHub, at sign-in (`GET /user`, `/user/installations`, `/user/installations/{id}/repositories`) | the browser, request parameters |
| What role does a user hold? | the CommitGuard database (`memberships`), granted by an owner or by the operator | GitHub organization roles |
| Is a webhook from GitHub? | `X-Hub-Signature-256` HMAC-SHA256 with the configured webhook secret | source IP, headers without a valid signature |
| Can the App read a repository? | GitHub, when minting an installation token down-scoped to that repository ID | installation or repository IDs in a payload |
| Is a check required for merging? | GitHub branch protection or rulesets | CommitGuard (it cannot verify this) |

## Component boundaries

### Detection core (detectors, rules, policy engine, provenance)

Code: `src/commitguard/detectors/`, `src/commitguard/rules/`,
`src/commitguard/policies/`, `src/commitguard/provenance/`,
`src/commitguard/core/`.

| Trusts | Does not trust |
|---|---|
| rule data loaded by the rules loader; the policy set passed in | every commit field: message, trailers, author and committer names and e-mails |

- Receives data, never a repository handle; performs no I/O, network or
  subprocess calls (`tests/unit/test_architecture.py`:
  `test_pure_layers_do_not_import_io_or_interface_modules`,
  `test_detection_code_uses_no_network_or_llm_libraries`).
- Parsing is linear and bounded (`MAX_TRAILERS = 1000`, at most 16 leading
  characters skipped before a trailer key); exceeding the trailer bound fails
  closed.
- A detector exception or invalid output becomes a failure that blocks
  (`src/commitguard/core/engine.py`).
- Rules are literal values matched after normalisation; no regular
  expressions from data.

### Local CLI and Git hooks

Code: `src/commitguard/cli/`, `src/commitguard/git/`,
`src/commitguard/services/hooks.py`, `src/commitguard/utils/subprocess.py`.

| Trusts | Does not trust |
|---|---|
| the developer who runs it; the `git` executable on `PATH`; the Python interpreter recorded in the hook; the work tree's configuration (for advisory local checks) | commit metadata, ref names, pre-push stdin, message files, configuration syntax, a `commitguard/` directory in the work tree |

- All external commands go through `run_command`: argument sequences only,
  `shell=False`, NUL bytes refused, stdin closed unless input is given, a
  timeout on every call.
- Git is run with `GIT_TERMINAL_PROMPT=0`, `GIT_NO_REPLACE_OBJECTS=1`,
  `GIT_OPTIONAL_LOCKS=0` and `LC_ALL=C` (`src/commitguard/git/commands.py`);
  revisions are validated (`src/commitguard/security/validation.py`) and passed
  after `--end-of-options`.
- Hooks run `python -P -m commitguard` so the current directory is not on
  `sys.path`. If the recorded interpreter is missing, the wrapper runs any
  `commitguard` found on `PATH` without checking which project provides it.
- Output shown in the terminal passes through
  `src/commitguard/security/sanitization.py`.
- **Not a boundary against the developer.** `--no-verify`, deleting hooks or
  changing `core.hooksPath` bypasses local enforcement; this is documented and
  tested, not treated as a vulnerability.

### GitHub Actions check

Code: `action.yml`, `.github/workflows/commitguard.yml`,
`src/commitguard/cli/commands/ci.py`, `src/commitguard/ci/`,
`src/commitguard/services/ci.py`, `src/commitguard/github/actions.py`,
`src/commitguard/github/events.py`.

| Trusts | Does not trust |
|---|---|
| the Action's own source at the pinned commit (`github.action_path`), or in this repository a trusted commit checked out with `git worktree`; `requirements/ci.txt` hashes; the runner; GitHub's event payload after normalisation | the pull request head: its policy, rule files, a `commitguard/` package in the work tree; commit metadata printed to the log |

- Inputs reach scripts through `env:`, never `${{ }}` inside `run:` (tested
  in `tests/unit/github/test_workflows_static.py`).
- Untrusted text is printed between `::stop-commands::<random token>` markers;
  annotations and the job summary are escaped; step outputs are enums and
  integers.
- `pull_request_target` is refused (`src/commitguard/github/events.py`).
- Token: `contents: read`; no secrets; `persist-credentials: false`.
- **Does not protect** against a pull request that edits the workflow file
  itself; that needs CODEOWNERS or rulesets.

### GitHub App webhook receiver

Code: `src/commitguard/github/app.py` (`handle_webhook`),
`src/commitguard/github/webhooks.py`, `src/commitguard/github/events.py`,
`src/commitguard/github/storage.py` (deliveries).

| Trusts | Does not trust |
|---|---|
| a delivery whose body authenticates with the webhook secret; GitHub's delivery ID format | everything before the signature check; payload fields after it (normalised into typed events); installation, repository, owner and ref values in payloads (used as claims, then authorised through GitHub) |

Processing order, each step before the next:

```text
rate limit (per REMOTE_ADDR) -> body size (25 MB) -> X-Hub-Signature-256
  -> X-GitHub-Event / X-GitHub-Delivery format -> strict JSON (UTF-8, no duplicate keys, depth <= 64)
  -> typed event normalisation -> delivery ID + payload digest recorded (replay protection)
```

- The signature is compared with `hmac.compare_digest`; an unconfigured
  secret fails with `500`, never accepts.
- A delivery ID seen with a different payload digest is refused.
- The rate limiter uses `REMOTE_ADDR` only; behind a reverse proxy that is the
  proxy's address.

### Scan workers and Git mirrors

Code: `src/commitguard/github/worker.py`, `src/commitguard/github/repositories.py`,
`src/commitguard/github/installations.py`, `src/commitguard/github/check_runs.py`.

| Trusts | Does not trust |
|---|---|
| GitHub's answer when minting a down-scoped token and looking up the repository by ID; the installed rules; stored organization governance inputs | the repository's contents, Git server responses, commit metadata |

- `InstallationManager.authorize` refuses deleted or suspended installations,
  mints a token for one repository ID, looks the repository up by ID with that
  token and rejects an identity mismatch.
- Mirrors: `git init --bare --template=`; each fetch sets
  `protocol.allow=never` except the allowed protocol,
  `http.followRedirects=false`, `credential.helper=` and
  `core.hooksPath=/dev/null` (the platform's null device); the token is sent as
  an `extraHeader` scoped to the remote URL through `GIT_CONFIG_*` variables.
  No checkout, no work tree, no submodules, no builds.
- A scan that cannot complete publishes `failure` or `timed_out`, or no check
  at all; it never publishes success.

### GitHub API client and App authentication

Code: `src/commitguard/github/client.py`, `src/commitguard/github/auth.py`,
`src/commitguard/github/permissions.py`, `src/commitguard/github/settings.py`.

| Trusts | Does not trust |
|---|---|
| `https://api.github.com` and `https://github.com` (fixed); the App private key and ID from configuration | response bodies (validated), pagination links (must stay on the API host), non-HTTPS URLs |

- JWT: RS256, 540-second lifetime, backdated 60 seconds; keys must be
  unencrypted PEM RSA of at least 2048 bits.
- Installation tokens request exactly `checks: write`, `contents: read`,
  `metadata: read`, `pull_requests: read` for a single repository ID; granted
  permissions are verified; tokens are cached in memory only and dropped near
  expiry, on rejection, and on uninstall or repository removal.
- Credentials are wrapped in `Secret` and registered with the redactor as
  soon as they are read.

### Dashboard API and sign-in

Code: `src/commitguard/api/app.py`, `src/commitguard/api/http.py`,
`src/commitguard/api/hosting.py`, `src/commitguard/controlplane/identity.py`.

| Trusts | Does not trust |
|---|---|
| a session cookie whose SHA-256 matches an unexpired session; GitHub's user, installation and repository lists captured at sign-in; origins configured in `COMMITGUARD_DASHBOARD_URL` and `COMMITGUARD_DASHBOARD_ALLOWED_ORIGINS` | the browser, every path segment, query parameter, header and JSON body; the `Origin` header unless it is in the allow-list; `return_to` values |

- Sign-in: state bound to the browser by an `HttpOnly` cookie, PKCE verifier
  kept server-side, state records single-use with a 10-minute lifetime; the
  GitHub user token is used during sign-in and discarded.
- Sessions: 256-bit token in a `__Host-` cookie (`HttpOnly`, `Secure`,
  `SameSite=Lax`); only its SHA-256 is stored; 8-hour absolute and 2-hour idle
  limits.
- Writes (`POST`, `PUT`, `PATCH`, `DELETE`) need an allowed `Origin` **and**
  `X-CSRF-Token` = SHA-256 of `"csrf"` and the session token.
- Order in `DashboardApi._dispatch`: route match → authentication →
  rate limit → CSRF → route-level permission and access scope → handler
  (services perform the finer checks).
- Static files are served from the build directory only; CSP allows
  same-origin scripts, styles, fonts and connections, no inline scripts or
  `eval`.

### Control plane and governance services

Code: `src/commitguard/controlplane/`, `src/commitguard/governance/`,
`src/commitguard/api/governance.py`.

| Trusts | Does not trust |
|---|---|
| the `Principal` built by the API for this request (memberships and roles read from the database per request; installations and repositories reported by GitHub at sign-in) | resource IDs in requests, repository IDs in bodies, draft and exception contents, organization rule data |

- Tenant scoping: every read filters by `AccessScope` (installations of
  accounts where the user holds the permission **and** that GitHub reported)
  and by the session's GitHub-reported repositories; governance services call
  `require()` (non-member → `404`, member without permission → `403`) and
  `require_visible_repositories()` for repository IDs.
- Organization rules are identity data (names, prefixes, e-mails, logins); no
  patterns, expressions or imports.
- Policy versions, organization rule versions and scoped policy versions are
  immutable through database triggers; policy exceptions cannot be deleted.

### Notifications

Code: `src/commitguard/notifications/`, `src/commitguard/controlplane/notifications.py`.

| Trusts | Does not trust |
|---|---|
| the operator's SMTP relay and signing key; endpoint URLs added by `notifications:manage` members (still checked) | notification text derived from commit data; DNS answers for webhook hosts (checked at send time, connection pinned) |

- Outbound webhooks: HTTPS, public addresses only in production, no
  redirects, per-endpoint secret `whsec_` + HMAC-SHA256(signing key, endpoint
  ID), signature over timestamp and body.
- Delivery happens after the security decision is stored; failures never
  change it.

### State store (SQLite)

Code: `src/commitguard/github/storage.py`, `src/commitguard/audit/storage.py`.

| Trusts | Does not trust |
|---|---|
| the operator and the file system | values from requests and payloads (always bound parameters) |

- The database file is created with mode `0600` and its directory with
  `0700`; WAL journal, foreign keys on.
- Holds no commit messages, file contents, tokens or keys; session tokens only
  as SHA-256.
- Triggers make policy versions and audit events non-updatable. They are
  integrity checks against application bugs, not against anyone who can write
  the file.

### Web dashboard (browser bundle)

Code: `web/`.

| Trusts | Does not trust |
|---|---|
| the same-origin API | data returned by the API is rendered as text (no raw HTML) |

- Holds no secrets; receives the CSRF token from `GET /api/v1/auth/session`.

### Benchmark and research tooling

Code: `src/commitguard/research/`, `src/commitguard/cli/commands/benchmark.py`.

| Trusts | Does not trust |
|---|---|
| the developer running it; dataset files (validated with pydantic, not size-limited) | nothing adversarial is expected; it must not affect the developer's Git setup |

- Git runs in temporary directories with a private `HOME` and global
  configuration.
- Results are written to new files and never overwrite existing ones.

## Operator configuration

Credentials and settings are read from the environment or from files when the
service starts (`commitguard github serve`). Changing them requires a restart.

| Setting | Variables | Handling |
|---|---|---|
| App ID | `COMMITGUARD_GITHUB_APP_ID` | positive integer |
| App private key | `COMMITGUARD_GITHUB_PRIVATE_KEY` or `COMMITGUARD_GITHUB_PRIVATE_KEY_FILE` (preferred) | at most 32 KB; `Secret`; redacted; `commitguard github validate` warns when the file is readable by group or others |
| Webhook secret | `COMMITGUARD_GITHUB_WEBHOOK_SECRET` or `..._FILE` | at least 16 characters; `Secret`; redacted |
| Client ID and secret (dashboard) | `COMMITGUARD_GITHUB_CLIENT_ID`, `COMMITGUARD_GITHUB_CLIENT_SECRET` or `..._FILE` | `Secret`; redacted |
| Notification signing key | `COMMITGUARD_NOTIFICATION_SIGNING_KEY` or `..._FILE` | 32+ characters; rotating it changes every endpoint secret |
| SMTP password | `COMMITGUARD_SMTP_PASSWORD` or `..._FILE` | `Secret`; redacted |
| Data directory, mandatory policy | `COMMITGUARD_APP_DATA_DIR` (absolute), `COMMITGUARD_APP_MANDATORY_POLICY_FILE` | trusted operator input |
| Environment | `COMMITGUARD_ENV` (`production` default) | production requires an `https://` dashboard URL and sends HSTS |

Errors name the variable, never its value
(`tests/unit/github/app/test_auth.py::test_secret_configuration_errors_name_variables_not_values`).
