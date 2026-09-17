# Security review guide

A guide for an independent security reviewer of CommitGuard. It explains the
architecture, where the security decisions are made, how to run the security
tests, and which files deserve the most time.

**Status.** No external or independent security review of CommitGuard has
taken place. The project is pre-alpha (`0.1.0.dev0`), has a single maintainer
and has published no releases. Review the latest commit on `main`.

Companion documents: [threat model](threat-model.md),
[security boundaries](security-boundaries.md),
[authorization matrix](authorization-matrix.md),
[supply chain](supply-chain.md), [CI pipeline review](ci-pipeline-security.md).

## Contents

- [Getting started](#getting-started)
- [Architecture overview](#architecture-overview)
- [Trust boundaries](#trust-boundaries)
- [Authentication](#authentication)
- [Authorization](#authorization)
- [Policy engine](#policy-engine)
- [Rule engine and detection](#rule-engine-and-detection)
- [Git integration](#git-integration)
- [GitHub integration](#github-integration)
- [Webhooks](#webhooks)
- [Database](#database)
- [Secrets handling and log redaction](#secrets-handling-and-log-redaction)
- [Notifications](#notifications)
- [Web dashboard](#web-dashboard)
- [Known limitations](#known-limitations)
- [Running the tests](#running-the-tests)
- [High-risk files](#high-risk-files)
- [Suggested review questions](#suggested-review-questions)

## Getting started

Install from a clone of this repository. **Do not run
`pip install commitguard`:** that name on PyPI belongs to an unrelated project
([supply-chain.md](supply-chain.md#name-collision-on-pypi)).

```bash
git clone https://github.com/oyinlola-tech/commitguard.git
cd commitguard
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"   # or scripts/install-dev.sh
```

Requirements: Python 3.12 or newer, Git 2.31 or newer. The `[dev]` extra
includes `cryptography`, which the GitHub App needs for JWT signing. The web
dashboard lives in `web/` (Node.js, `npm ci`).

## Architecture overview

```text
                 ┌──────────────── interfaces ────────────────┐
 cli/            │ commands: scan, check, install, hook, doctor, ci, github, dashboard, benchmark
 api/            │ dashboard JSON API (WSGI), static hosting, governance routes
 github/         │ App service: webhooks, events, auth, client, installations, worker, mirrors, checks
                 └─────────────────────────────────────────────┘
                 ┌──────────────── services ──────────────────┐
 services/       │ analysis, scan, hooks, ci (range + trusted policy), reports, audit
 controlplane/   │ access (roles), identity (sign-in, sessions), queries, commands, policies, members, notifications
 governance/     │ settings, groups, inventory, workflow, exceptions, rollouts, simulation, rules, schedules, posture
 notifications/  │ outbox, dispatcher, channels (in-app, e-mail, webhook)
                 └─────────────────────────────────────────────┘
                 ┌──────────────── pure core (no I/O) ────────┐
 core/           │ engine, decision, result
 detectors/      │ coauthor, trailer, identity, bot (+ registry)
 provenance/     │ trailers, normalization, author, committer
 rules/          │ loader, matcher, models   (data: rules/*.yaml)
 policies/       │ evaluator, mandatory, governance resolver, model
 config/         │ schema, loader, sources (which policy is trusted)
                 └─────────────────────────────────────────────┘
 security/       secrets, sanitization, validation, safe_yaml, rate_limit, hashing
 git/, utils/    Git wrapper, hook rendering, push parsing, ranges; safe subprocess, filesystem
 research/       benchmarks and datasets (developer tooling)
```

Layering is enforced by `tests/unit/test_architecture.py` (pure layers import
no I/O; only the GitHub client and server use the network; Git fetch happens
only in the mirror manager; the API routes contain no SQL; no `shell=True`;
YAML only through the strict loader; `cryptography` only for App
authentication).

Deployment modes: local hooks and CLI, the GitHub Action (`action.yml`), the
GitHub App service with dashboard (`commitguard github serve`), and the
benchmark tooling. See the
[threat model](threat-model.md#attack-surfaces-by-deployment-mode).

## Trust boundaries

Summarised in the [threat model](threat-model.md#trust-boundaries) (TB1–TB12)
and component by component in
[security-boundaries.md](security-boundaries.md). The two decisions everything
else depends on:

1. **Policy comes from a trusted commit, never from the change being
   evaluated** (`src/commitguard/services/ci.py`,
   `src/commitguard/config/sources.py`).
2. **Rules come from the installed package** (plus identity-only organization
   rules in the App), never from the scanned repository
   (`src/commitguard/rules/loader.py`).

## Authentication

| Mechanism | Code | What to check |
|---|---|---|
| Dashboard sign-in (GitHub App user authorization, OAuth web flow with PKCE) | `src/commitguard/controlplane/identity.py`, `src/commitguard/api/app.py` (`login`, `callback`) | state bound to an `HttpOnly` cookie and a single-use record (10 minutes); `return_to` restricted to same-origin paths; user token used only during sign-in; the installation and repository lists stored with the session |
| Sessions | `identity.py` (`AuthService.authenticate`), `src/commitguard/api/http.py` (cookies) | 256-bit token, SHA-256 stored, `__Host-` cookie, 8 h absolute / 2 h idle, revocation, deletion when the last membership is removed |
| CSRF | `api/app.py` (`_check_csrf`), `identity.py` (`verify_csrf`) | `Origin` must be in `trusted_origins` **and** `X-CSRF-Token` must equal SHA-256(`"csrf"` ‖ session token); applies to `POST`, `PUT`, `PATCH`, `DELETE` |
| Recent sign-in for sensitive changes | policy, settings, webhook endpoint services | `authenticated_at` within 15 minutes |
| GitHub App JWT | `src/commitguard/github/auth.py` (`AppCredentials`) | RS256, ≥ 2048-bit RSA, 540 s lifetime, 60 s backdate, reuse margin |
| Installation tokens | `auth.py` (`InstallationTokenManager`), `src/commitguard/github/installations.py` (`authorize`) | one repository ID per token, exact permission set, granted permissions verified, memory-only cache, invalidation on 401/403/404 |
| Webhook authenticity | `src/commitguard/github/webhooks.py` | see [Webhooks](#webhooks) |
| Outbound webhook signatures | `src/commitguard/notifications/channels/webhook.py` | per-endpoint secret derivation, timestamp window |

## Authorization

See the [authorization matrix](authorization-matrix.md). Review points:

- `DashboardApi._dispatch` in `src/commitguard/api/app.py` applies the
  route-level permission and builds the `AccessScope`; many write routes
  declare only a read permission there and rely on the service to check the
  write permission. Governance routes (`src/commitguard/api/governance.py`)
  declare none. Confirm each handler reaches a `require()` or
  `principal.can()` for the right permission **before** parsing the body.
- Tenant scoping in `src/commitguard/controlplane/queries.py` (every statement
  filters on `json_each(?)` of the scope's installation IDs and on
  `session_repositories`) and `src/commitguard/governance/common.py`
  (`require`, `visible_repository_ids`, `require_visible_repositories`).
- Separation of duties in `src/commitguard/governance/workflow.py` and
  `src/commitguard/governance/exceptions.py`; note the defaults
  (`require_policy_approval` off, `require_separate_approver` on).

## Policy engine

| Step | Code |
|---|---|
| Configuration schema (strict, unknown keys rejected, strict booleans) | `src/commitguard/config/schema.py`, `src/commitguard/config/loader.py` |
| Strict safe YAML (no object construction, no aliases, duplicate keys rejected) | `src/commitguard/security/safe_yaml.py` |
| Policy source selection (work tree, trusted revision, built-in) | `src/commitguard/config/sources.py`, `src/commitguard/services/ci.py` |
| Mandatory floors (operator file) | `src/commitguard/policies/mandatory.py` |
| Organization resolution (floors, defaults, groups, exceptions, monitor mode) | `src/commitguard/policies/governance.py`, `src/commitguard/governance/resolver.py`, `src/commitguard/governance/cache.py` |
| Evaluation to ALLOW / WARN / BLOCK | `src/commitguard/policies/evaluator.py`, `src/commitguard/core/decision.py` |

Questions: can any configuration value, layer ordering or cache state produce
a weaker action than the strongest applicable floor? Does every error path end
in BLOCK (hooks: exit 2; CI: non-zero exit; App: `failure` or no check)? Is the
effective policy cache invalidated in the same transaction as every change
that affects it?

## Rule engine and detection

| Part | Code |
|---|---|
| Rule files (`rules/*.yaml`, shipped as `commitguard/rules/data`) | `src/commitguard/rules/loader.py`, `src/commitguard/rules/models.py` |
| Matching (literal values, no regular expressions) | `src/commitguard/rules/matcher.py` |
| Organization rules (identity data compiled into the rule set) | `src/commitguard/governance/rules.py` |
| Trailer parsing (bounded, fail closed on floods, leading-character skipping) | `src/commitguard/provenance/trailers.py` |
| Unicode normalisation (NFKC, invisible characters, look-alikes; ASCII fast path) | `src/commitguard/provenance/normalization.py` |
| Identity parsing | `src/commitguard/provenance/author.py`, `committer.py` |
| Detectors and registry | `src/commitguard/detectors/` |
| Engine (detector failures become blocking failures) | `src/commitguard/core/engine.py` |

This is where detection bypasses have been found (see
[findings](vulnerability-response.md#security-fixes-to-date)). Productive
approaches: Unicode confusables and compatibility characters before, inside
and after trailer keys; line separators; trailer block boundaries; very long
or deeply repetitive messages (time and memory); disagreement between how Git
parses trailers and how CommitGuard does. The detection benchmark
(`commitguard benchmark detection`) runs the labelled datasets in
`benchmarks/datasets/`.

## Git integration

| Part | Code | What to check |
|---|---|---|
| Subprocess choke point | `src/commitguard/utils/subprocess.py` (`run_command`) | argument sequences only (`str`/`bytes` refused), `shell=False`, NUL bytes refused, stdin `DEVNULL` unless input is given, timeout on every call (default 30 s), environment only extended. Captured output is **not** size-bounded (`TODO(phase-2)`). |
| Git wrapper | `src/commitguard/git/commands.py` (`run_git`, `GIT_ENV_OVERRIDES`) | `--no-pager`, `GIT_TERMINAL_PROMPT=0`, `GIT_NO_REPLACE_OBJECTS=1`, `GIT_OPTIONAL_LOCKS=0`, `LC_ALL=C`; `git` resolved once from `PATH`; stderr sanitised in errors |
| Input validation | `src/commitguard/security/validation.py` | `validate_revision` (no leading `-`, no control characters, ≤ 256 chars), `validate_git_sha` (full SHA-1/SHA-256), `validate_git_config_key`, `validate_repository_path` (no absolute paths, `..`, backslashes, leading `-`) |
| Repository reads | `src/commitguard/git/repository.py` | every revision after `--end-of-options`; `--no-use-mailmap`; NUL-delimited formats with a random per-call record boundary cross-checked against requested SHAs |
| Ranges and pre-push parsing | `src/commitguard/git/ranges.py`, `src/commitguard/git/push.py`, `src/commitguard/cli/commands/hook.py` | strict four-field parsing, object ID validation, 8 MiB stdin limit, commit limits fail closed |
| Hook rendering and installation | `src/commitguard/git/hooks.py` | shell quoting of the interpreter path, `-P`, chaining of foreign hooks, checksum, refusal of shared `core.hooksPath`, `PATH` fallback |
| App mirrors | `src/commitguard/github/repositories.py` | `init --bare --template=`, per-fetch `-c` hardening (protocol allow-list, no redirects, empty credential helper, null hooks path), token via `GIT_CONFIG_*` `extraHeader`, numeric directory names |

Bandit's low-severity subprocess notices are expected (`bandit -r src -ll` in
CI reports medium and high only); the single `# noqa: S603` is on the
`subprocess.run` call in `run_command`.

## GitHub integration

| Part | Code | What to check |
|---|---|---|
| HTTP client | `src/commitguard/github/client.py` | fixed `https://api.github.com`, only an HTTPS handler installed (`# noqa: S310`), redirects, pagination links restricted to the API host, bounded retries and rate-limit waits, path segments encoded, errors normalised without echoing tokens |
| App authentication | `src/commitguard/github/auth.py`, `src/commitguard/github/settings.py` | key parsing errors never chained (could echo key material), token scoping, `Secret` wrapping |
| Permissions | `src/commitguard/github/permissions.py` | `REQUIRED_PERMISSIONS` (`checks: write`, `contents/metadata/pull_requests: read`), optional `merge_queues: read`, missing and excessive permission detection |
| Installations | `src/commitguard/github/installations.py` | state transitions (deleted/suspended refused), `authorize` identity check by repository ID |
| Events | `src/commitguard/github/events.py` | typed normalisation; SHA validation; `pull_request_target` refused; merge group ref validation |
| Checks | `src/commitguard/github/check_runs.py`, `checks.py`, `markdown.py` | ownership per (repository, SHA, check name); output caps; Markdown escaping and redaction |
| Actions output | `src/commitguard/github/actions.py` | `::stop-commands::` with an unpredictable token, annotation escaping, summary escaping |
| Worker and queue | `src/commitguard/github/worker.py`, `queue.py`, `recovery.py` | failure paths never publish success; leases; retries bounded |

## Webhooks

Code: `src/commitguard/github/webhooks.py` (`parse_delivery`),
`src/commitguard/github/app.py` (`handle_webhook`, `_handle_verified`,
`create_wsgi_app`), `src/commitguard/github/storage.py` (`record_delivery`).

1. Rate limit per `REMOTE_ADDR` (600 per minute by default; per process;
   `X-Forwarded-For` is not read).
2. Body larger than 25 MB → `413` (checked before the signature).
3. `X-Hub-Signature-256` must match `sha256=<64 hex>`; HMAC-SHA256 of the raw
   body with the webhook secret, compared with `hmac.compare_digest`; missing,
   malformed or wrong → `401`; unconfigured secret → `500`.
4. `X-GitHub-Event` (`[a-z_]{1,64}`) and `X-GitHub-Delivery`
   (`[0-9A-Za-z-]{8,72}`) validated.
5. JSON: UTF-8, object at top level, duplicate keys rejected, depth ≤ 64.
6. Normalised into typed events; invalid payloads are audited and rejected.
7. Delivery deduplication: the delivery ID is stored with the SHA-256 of the
   body. Same ID and digest → duplicate (not processed again unless the
   earlier processing failed or was abandoned); same ID with a different
   digest → refused. Scan jobs have idempotency keys, so equivalent events
   under new delivery IDs map to one job.

Review questions: is anything derived from the payload before step 3? Can a
failed or abandoned delivery be replayed to run twice concurrently? Do
delivery IDs expire before GitHub could redeliver (retention)?

## Database

Code: `src/commitguard/github/storage.py` (`SqliteStateStore`, migrations,
retention), `src/commitguard/audit/storage.py`, and queries throughout
`controlplane/` and `governance/`.

- **Engine.** Standard-library `sqlite3`; file `commitguard-app.sqlite3` in
  `COMMITGUARD_APP_DATA_DIR`, created with mode `0600` (directory `0700`); WAL
  journal; `busy_timeout` 10 s; foreign keys on; `BEGIN IMMEDIATE` for
  read-modify-write; ordered migrations in one transaction; a database from a
  newer version is refused.
- **Integrity triggers.** `UPDATE`/`DELETE` refused on organization policy
  versions, scoped policy versions and organization rule versions; `UPDATE`
  refused on audit events (retention deletes old ones); `DELETE` refused on
  policy exceptions. These guard against application bugs, not against direct
  database access.
- **SQL construction.** Values are always bound parameters. Statements with
  optional filters are assembled from constant fragments defined in the same
  module: `_Where` in `controlplane/queries.py`, `" ".join((...))` tuples in
  `controlplane/notifications.py`, `sql += " AND ..."` with constant strings in
  `governance/rollouts.py`, `simulation.py`, `workflow.py` and `groups.py`.
  Sort orders come from allow-lists (`SCAN_SORTS`, `VIOLATION_SORTS`,
  `REPOSITORY_SORTS`, `AUDIT_SORTS`). Search uses `LIKE ... ESCAPE '\'` on
  escaped input; the SHA-prefix search uses a validated hexadecimal prefix.
  Because joining constant fragments is not flagged by Ruff `S608` or Bandit
  `B608`, review these call sites by hand: search for `" ".join(`, `.sql`,
  `sql +=` and `f"` near `SELECT`.
- **The Bandit `nosec` in `governance/common.py`.**
  `account_repository()` builds
  `f"SELECT * FROM ({_ACCOUNT_REPOSITORIES}) WHERE repository_id = ?"` and
  carries `# noqa: S608  # nosec B608`. The interpolated value is the module
  constant `_ACCOUNT_REPOSITORIES`; both the account ID and the repository ID
  are bound parameters (converted with `int()`), so no caller-controlled text
  reaches the SQL. The suppression exists because Bandit flags any f-string
  that contains `SELECT`. Confirm that `_ACCOUNT_REPOSITORIES` is never
  reassigned.
- **Other suppressions.** `# nosec B506` / `# noqa: S506` in
  `security/safe_yaml.py` (the loader is a `SafeLoader` subclass);
  `# noqa: S310` in `github/client.py`; `# noqa: S311` for non-security
  randomness (retry jitter, deterministic dataset generation); `# noqa: S105`
  on environment variable *names*; `# noqa: S101` on `assert` statements that
  check internal invariants in `controlplane/policies.py`,
  `controlplane/notifications.py`, `github/storage.py` and several governance
  modules. `assert` statements are removed under `python -O`; confirm none of
  them is a security check.
- **Data minimisation.** No commit messages, file contents, tokens or keys are
  stored (`tests/unit/github/app/test_storage.py::test_store_never_holds_commit_messages_or_identities`);
  author and committer identities are stored only with findings.

## Secrets handling and log redaction

Code: `src/commitguard/security/secrets.py`,
`src/commitguard/observability/logging.py`, `src/commitguard/github/errors.py`,
`src/commitguard/controlplane/results.py` (`clean_text`),
`src/commitguard/notifications/models.py`, `src/commitguard/audit/models.py`.

- `Secret` hides its value in `repr`, `str` and `format`, cannot be pickled,
  compares in constant time, and exposes the value only through `reveal()`.
  Review every `reveal()` call site.
- `SecretRedactor` (process-wide `default_redactor()`): every loaded or minted
  credential is registered; `redact()` replaces registered values (and, for
  PEM keys, each line of 16+ characters) and credential-shaped strings: PEM
  private key blocks (even truncated), `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_` and
  `github_pat_` tokens, JWT-shaped strings, `Authorization` header values,
  bearer/basic credentials and `sha256=` webhook signatures.
- Values shorter than 8 characters are not registered (to avoid mangling
  ordinary text). Redaction is substring replacement: an encoded or split
  secret (base64, URL-encoded, split across log fields) is not recognised
  unless it matches a pattern.
- Structured logs pass every string through `redact()` and truncate fields
  (1,000 characters, 40 fields); exceptions are logged by type and redacted
  message, never with tracebacks or local variables. Request logs contain no
  headers, cookies or query strings.
- Text stored or published from untrusted sources passes
  `sanitize_for_terminal(redact(...))`.

Tests: `tests/unit/github/app/test_secrets_and_logging.py`,
`tests/integration/github/app/test_app_security.py::test_secrets_never_leak`,
`tests/integration/github/app/dashboard/test_dashboard_security.py::test_secrets_never_reach_responses_or_logs`.

## Notifications

Code: `src/commitguard/notifications/`. Check outbound webhook SSRF defences
(`channels/webhook.py`: HTTPS only, address resolved at send time, private
addresses refused in production, connection pinned to the checked address, no
redirects), e-mail header handling (`channels/email.py`), and that delivery
never changes a security decision (`dispatcher.py`, `outbox.py`).

## Web dashboard

Code: `web/src/`. The bundle holds no secrets. Check that API data is rendered
as text (the ESLint configuration in `web/eslint.config.js` forbids the
`dangerouslySetInnerHTML` attribute), that the CSP from
`src/commitguard/api/hosting.py` (`DASHBOARD_CSP`) is served, and that the
CSRF token is sent on writes.

## Known limitations

Documented, not vulnerabilities (see
[threat model – remaining risks](threat-model.md#remaining-risks)):

- local hooks can be bypassed by the person who controls the repository;
- a pull request can edit the workflow that runs the Action check;
- merges are blocked only if branch protection or a ruleset requires the check;
- attribution removed before committing cannot be detected;
- GitHub access changes apply at the next sign-in (sessions ≤ 8 hours);
- the operator and the database are fully trusted;
- rate limits are per process and keyed by `REMOTE_ADDR`;
- no per-message size limit for commits read from Git objects; captured Git
  output is not size-bounded;
- single host, single SQLite writer.

## Running the tests

Run from the repository root after `pip install -e ".[dev]"`.

| Purpose | Command |
|---|---|
| Security regression suite | `python -m pytest -m security` |
| Full Python suite | `python -m pytest` |
| Architecture rules only | `python -m pytest tests/unit/test_architecture.py` |
| Workflow and Action static checks | `python -m pytest tests/unit/github/test_workflows_static.py` |
| Action install with hash-pinned dependencies (needs PyPI) | `COMMITGUARD_NETWORK_TESTS=1 python -m pytest tests/integration/github/test_action_scripts.py` |
| Record security experiment evidence | `COMMITGUARD_EVIDENCE_DIR=<dir> python -m pytest -m security` (the `observe` fixture appends to `<dir>/experiments.jsonl`) |
| Static analysis as in CI | `ruff check --select S,BLE .` and `bandit -r src -ll` |
| Dependency audit as in CI | `pip-audit --skip-editable` |
| Web unit tests, lint, type check | `cd web && npm ci && npm test && npm run lint && npm run typecheck` |
| Web end-to-end tests (Playwright) | `cd web && npm run e2e` |
| Detection benchmark | `commitguard benchmark detection --dataset-version 1.1.0` |

About the `security` marker: `tests/conftest.py` adds it at collection time to
every test under the paths in `SECURITY_TEST_PATHS` (parsers, bypass and
tampering experiments, webhook and GitHub integration security, dashboard
authorization and tenant isolation). It is declared in `pyproject.toml`, and
`--strict-markers` is on. Notes:

- `SECURITY_TEST_PATHS` lists `tests/security`: fuzzing, ReDoS and invariant tests.
- `pytest -m security` runs the marked suite (350 tests). `commitguard reproduce
  security --evidence-dir <dir>` runs the same suite and writes the experiment,
  fuzzing and ReDoS evidence that `commitguard report security` reads.
- Some security-relevant tests are not in the marker set, for example
  `tests/integration/github/app/dashboard/test_governance_workflow.py`
  (separation of duties), `tests/unit/test_architecture.py`,
  `tests/unit/github/test_workflows_static.py` and
  `tests/unit/notifications/test_notification_units.py`.
- CI (`.github/workflows/ci.yml`) runs the full suite on Linux, macOS and
  Windows with Python 3.12 and 3.13; it does not run the web tests.

## High-risk files

Ordered by where a defect would do the most damage. Spend the most time at the
top.

| # | File | Why |
|---|---|---|
| 1 | `src/commitguard/provenance/trailers.py` | every detection decision depends on it; two detection bypasses were found here |
| 2 | `src/commitguard/provenance/normalization.py` | Unicode handling for evasion resistance; performance on large messages |
| 3 | `src/commitguard/github/webhooks.py` | authenticity of every App event |
| 4 | `src/commitguard/github/app.py` | webhook handling order, deduplication, WSGI entry point |
| 5 | `src/commitguard/services/ci.py` and `src/commitguard/config/sources.py` | which policy is trusted; a mistake lets a pull request approve itself |
| 6 | `src/commitguard/controlplane/access.py` | role → permission table; `AccessScope` |
| 7 | `src/commitguard/api/app.py` | authentication, CSRF, route permissions, dispatch order |
| 8 | `src/commitguard/api/governance.py` | 65 routes that rely on service-level checks |
| 9 | `src/commitguard/controlplane/queries.py` | tenant scoping and SQL assembly for every read |
| 10 | `src/commitguard/governance/common.py` | `require`, repository visibility, the `nosec B608` query |
| 11 | `src/commitguard/governance/workflow.py`, `exceptions.py`, `settings.py` | approval, separation of duties, weakening controls |
| 12 | `src/commitguard/controlplane/identity.py` | sign-in, PKCE, sessions, CSRF token derivation |
| 13 | `src/commitguard/github/auth.py`, `installations.py` | JWT, token down-scoping, authorization by repository ID |
| 14 | `src/commitguard/github/repositories.py` | the only place the service runs `git fetch` against remote data |
| 15 | `src/commitguard/utils/subprocess.py`, `src/commitguard/git/commands.py`, `src/commitguard/security/validation.py` | command execution and argument validation |
| 16 | `src/commitguard/git/hooks.py` | generated shell scripts that run on every commit; `PATH` fallback |
| 17 | `src/commitguard/security/secrets.py`, `src/commitguard/observability/logging.py` | credential redaction |
| 18 | `src/commitguard/policies/governance.py`, `src/commitguard/governance/resolver.py`, `cache.py` | floors, exceptions, cache invalidation |
| 19 | `src/commitguard/github/storage.py` | schema, triggers, delivery replay protection, retention |
| 20 | `src/commitguard/notifications/channels/webhook.py` | outbound requests to user-supplied URLs |
| 21 | `src/commitguard/github/actions.py`, `github/events.py` | workflow command injection, event trust |
| 22 | `action.yml`, `.github/workflows/commitguard.yml`, `requirements/ci.txt` | what users' CI executes |
| 23 | `src/commitguard/security/safe_yaml.py`, `src/commitguard/config/schema.py` | configuration parsing |
| 24 | `src/commitguard/api/hosting.py` | static file serving and CSP |

## Suggested review questions

- Can any input reach a shell, a Git option position, SQL text or HTML without
  passing the documented validation?
- Can any failure (exception, timeout, GitHub error, database error, missing
  permission) produce ALLOW, a passing check, or a "secure" posture?
- Can a member of one organization learn that a resource of another exists?
- Can a pull request, a repository configuration, a group membership change or
  an exception lower an organization floor outside the documented exception
  path?
- Can a replayed, reordered or concurrently delivered event change a newer
  result?
- Can a credential reach a log line, an error message, a Check Run, an API
  response, a notification or the database?
- Does every new route have a service-level permission check that runs before
  the body is parsed?

Report findings privately as described in [SECURITY.md](../../SECURITY.md).
