# CommitGuard threat model

> Living document. It describes CommitGuard as it exists in this repository
> today (`0.1.0.dev0`, pre-alpha, no published releases). Status markers:
> **[done]** implemented and covered by tests, **[planned]** designed but not
> built, **limitation** a documented gap that CommitGuard does not close.
> Nothing here is the result of an external security review: none has taken
> place.

## Version history

| Version | Date | Change |
|---|---|---|
| v1 | 2026-09-14 | Introduced as `docs/threat-model.md` (commit `0559b9f`) and extended phase by phase (hooks, GitHub Actions, GitHub App, dashboard, notifications, governance) until 2026-09-17 (last change in commit `20d552e`). |
| v2 | 2026-09-17 | Moved to `docs/security/threat-model.md`. Added assets, actors, trust boundaries, trust assumptions, attack surfaces per deployment mode, a STRIDE register that cites the tests covering each mitigation, the project's own supply chain and CI, the security findings recorded so far, and remaining risks. The per-phase threat tables from v1 are kept, with outdated statuses corrected, in the [detailed catalogue](#detailed-threat-catalogue). |

Related documents: [security boundaries](security-boundaries.md),
[authorization matrix](authorization-matrix.md),
[review guide](review-guide.md), [supply chain](supply-chain.md),
[CI pipeline review](ci-pipeline-security.md),
[vulnerability response](vulnerability-response.md) and
[incident response](incident-response.md).

## Contents

- [Scope](#scope)
- [System overview](#system-overview)
- [Assets](#assets)
- [Actors](#actors)
- [Trust boundaries](#trust-boundaries)
- [Trust assumptions](#trust-assumptions)
- [Attack surfaces by deployment mode](#attack-surfaces-by-deployment-mode)
- [STRIDE threat register](#stride-threat-register)
- [Security findings to date](#security-findings-to-date)
- [Remaining risks](#remaining-risks)
- [Detailed threat catalogue](#detailed-threat-catalogue)
- [Maintaining this document](#maintaining-this-document)

## Scope

### What CommitGuard protects

The integrity of a repository's **contribution policy**: commits that violate
the policy (initially, AI agent attribution in commit metadata) should not
reach protected branches, and every decision should be explainable with
preserved evidence.

### What CommitGuard does not do

- **Prove authorship.** Git metadata is self-asserted. Someone who removes an
  AI trailer and commits under their own name is indistinguishable from a
  human author by metadata alone. CommitGuard enforces policy over *claims*.
- **Detect AI-written code from its content.** "AI generated contribution
  detection" is a possible future research area and would be probabilistic; it
  must never be presented as deterministic.
- **Replace server-side enforcement.** Local checks are advisory: the person
  who controls the machine controls the hooks.
- **Configure GitHub for you.** A check only blocks merges when branch
  protection or a ruleset requires it. CommitGuard reports this; it does not
  change repository settings.

## System overview

```text
 developer machine                 GitHub                           operator host
 ─────────────────                 ──────                           ─────────────
 git ─▶ hooks ─▶ commitguard       pull_request / push /            reverse proxy (TLS)
        (pre-commit, commit-msg,   merge_group ─▶ Actions runner        │
         pre-push)                   └▶ action.yml / commitguard.yml    ▼
                                        └▶ commitguard ci github    commitguard github serve
                                   webhooks ────────────────────────▶ /webhooks/github
                                   REST API ◀── installation tokens ─ scan workers ─▶ git fetch
                                   Checks API ◀─ Check Runs ───────── (bare mirrors)
                                   OAuth (user sign-in) ◀──────────── /api/v1 dashboard API
                                                                       │
                                                                     SQLite state store
 research: commitguard benchmark ─▶ temporary Git repositories ─▶ benchmarks/results/raw
```

All deployment modes share one core: the detectors
(`src/commitguard/detectors/`), the rule data (`rules/`, shipped as
`commitguard/rules/data`), the policy engine (`src/commitguard/policies/`) and
the provenance parsers (`src/commitguard/provenance/`). The core performs no
I/O; architecture tests in `tests/unit/test_architecture.py` enforce that.

## Assets

| Asset | Where it lives | Why it matters |
|---|---|---|
| Repository policy (`.commitguard.yaml`) and bundled rule data | repository tree; installed package | decides what is blocked; weakening it is the most direct bypass |
| Decisions and their evidence (findings, Check Runs, job summaries, audit events) | terminal output, GitHub Checks, SQLite | the reason a commit was blocked must be accurate and must not lie about success |
| Developer machine and environment | wherever hooks run | CommitGuard runs inside Git hooks with the developer's privileges |
| Repository contents | developer machine, Actions runner, App mirrors | must not leave the machine through CommitGuard; the App keeps commit and tree objects only |
| GitHub App credentials: private key, webhook secret, JWTs, installation tokens | operator host (files or environment), process memory | a stolen private key lets the holder act as the App on every installation |
| Dashboard credentials: GitHub App client secret, session tokens, CSRF tokens | operator host, browser cookie, SQLite (hash only) | access to tenant data and to policy changes |
| Notification credentials: notification signing key, SMTP password | operator host | forged CommitGuard webhooks; mail relay abuse |
| App state: installations, scan jobs, Check Run ownership, delivery IDs | SQLite (`commitguard-app.sqlite3` in `COMMITGUARD_APP_DATA_DIR`) | replay protection and result integrity depend on it |
| Tenant isolation between GitHub accounts that install the App | SQLite queries, access scopes | one tenant must not read or change another's data |
| Organization governance state: settings, policy versions, drafts, approvals, exceptions, rollouts, organization rules, effective policy cache, schedules, bulk operations, reports | SQLite | controls enforcement for every repository of an organization |
| Member roles and sessions | SQLite | privilege |
| Personal data: author and committer identities stored with findings, GitHub logins | SQLite | kept only for commits with findings and removed by retention |
| Benchmark datasets and recorded results | `benchmarks/` | published accuracy and performance claims must be reproducible and not overstated |
| The project's own source, CI workflows, lock file and GitHub Action | this repository | users run the Action and install from source; a compromise reaches them |

## Actors

| Actor | Trust | Goal (if hostile) |
|---|---|---|
| Developer using local hooks | trusted with their own machine, not with the policy outcome | commit or push a violating commit |
| Contributor opening a pull request (including from a fork) | untrusted | get a violating commit merged; make the check pass; run code in CI |
| Malicious commit author (any repository CommitGuard scans) | untrusted | crash, mislead or exploit CommitGuard via crafted metadata or ref names |
| Automated agent | untrusted | add attribution that evades detection, or strip it |
| Internet attacker | untrusted | forge or replay webhooks, reach other tenants' repositories, exhaust the service, steal credentials |
| Malicious web page visited by a signed-in user | untrusted | make the browser perform writes (CSRF) or read dashboard data |
| Dashboard user of another organization | untrusted for this tenant | read or change data of a tenant they do not belong to |
| Low-privilege member (viewer, security manager) | partially trusted | weaken policy, hide violations, or grant themselves a role |
| Policy author without approval rights / repository team | partially trusted | publish an unreviewed policy, widen an exception, weaken policy through repository configuration or group membership |
| Organization admin or owner | trusted within the organization, audited | abuse emergency publication or settings to weaken enforcement |
| Removed or demoted member | no longer trusted | keep acting with the access they had |
| Service operator (runs `commitguard github serve`, holds the database and credentials) | fully trusted | out of scope as an adversary; see [trust assumptions](#trust-assumptions) |
| GitHub | trusted source of truth | out of scope as an adversary |
| Upstream dependency or action publisher; typosquatter or name squatter on PyPI | untrusted | ship malicious code into CI or into a user's installation |
| Maintainer account (single maintainer, `oyinlola-tech`) | trusted | compromise of the account is a compromise of the project |

## Trust boundaries

| ID | Boundary | Crossing | Validation at the boundary |
|---|---|---|---|
| TB1 | Git repository data → CommitGuard | commit messages, identities, trailers, ref names, pre-push stdin, config blobs | argument vectors only, validated revisions after `--end-of-options`, NUL-delimited parsing, strict safe YAML, size limits, terminal sanitisation |
| TB2 | Developer's work tree → hook process | `.commitguard.yaml`, a `commitguard/` directory in the work tree | hooks run `python -P -m commitguard`; local configuration is trusted only for local, advisory checks |
| TB3 | Pull request head → CI check | changed policy, rule files, workflow files | policy read from the trusted base commit; rules from the installed package; scanner installed from the Action's own commit or a trusted commit |
| TB4 | GitHub event payload → Actions check | `GITHUB_EVENT_PATH` JSON | strict normalisation, validated SHAs, `pull_request_target` refused |
| TB5 | Internet → webhook endpoint | HTTP requests to `/webhooks/github` | rate limit, size limit, HMAC-SHA256 signature before parsing, header formats, strict JSON |
| TB6 | Verified webhook → scan authorization | installation and repository IDs in payloads | token minted per scan and down-scoped to one repository ID; GitHub refuses if the installation does not cover it |
| TB7 | CommitGuard service → GitHub API and Git host | outbound HTTPS and `git fetch` | fixed hosts, HTTPS only, no redirects, same-origin pagination, protocol allow-list, no credential helpers, timeouts |
| TB8 | Browser → dashboard API | cookies, JSON bodies, path and query parameters | session authentication, CSRF (Origin + token), rate limits, body and depth limits, strict route patterns, permission and access scope |
| TB9 | Tenant → tenant | any identifier in a request | every query filtered by access scope (installations GitHub reported and repositories GitHub listed for the session); out-of-scope is `404` |
| TB10 | Organization governance input → enforcement | settings, drafts, exceptions, organization rules, group membership | permissions per action, approval workflow, floors that only tighten, identity data only (no patterns or code) |
| TB11 | CommitGuard → notification receivers | e-mail, outbound webhooks | signed payloads, public-address check at send time with pinned connection, no redirects, header-injection checks |
| TB12 | Third-party code → this repository's CI and users' installs | PyPI packages, GitHub Actions, npm packages | SHA-pinned actions, hash-pinned install lock for the scanner, `pip-audit`, Bandit, dependency review; gaps in [supply-chain.md](supply-chain.md) |

## Trust assumptions

Security properties in this document hold only if these assumptions hold.

1. **The host is not compromised.** The `git` executable found on `PATH`, the
   Python interpreter, and the operating system on the developer machine, the
   Actions runner and the operator host behave as documented.
2. **The person who controls a machine controls its hooks.** Local
   enforcement is a convenience and an early warning, not a security boundary
   against that person.
3. **GitHub is the source of truth** for identities, repository access,
   installation coverage, webhook signatures and App permissions. CommitGuard
   does not second-guess what GitHub reports.
4. **Repository administrators configure branch protection or rulesets** to
   require the `commitguard` (Action) or `commitguard-app` (App) check.
   CommitGuard cannot enforce or verify this locally.
5. **The service operator is fully trusted.** The operator holds the App
   private key, webhook secret, client secret, notification signing key and the
   SQLite database, and can grant roles with
   `commitguard dashboard members grant`. Database triggers (immutable policy
   versions, audit events that cannot be updated) protect against application
   bugs, not against someone with write access to the database file.
6. **TLS is terminated correctly in front of the service.**
   `commitguard github serve` binds to `127.0.0.1` by default and does not
   terminate TLS itself. One service instance runs per SQLite database.
7. **Hash-pinned packages are what their publishers released.**
   `requirements/ci.txt` pins sha256 digests; it protects against substitution
   after pinning, not against a malicious release that was pinned.
8. **Users install CommitGuard from this repository**, pinned to a commit.
   The PyPI name `commitguard` belongs to an unrelated project (see
   [S-SC-3](#project-supply-chain-and-ci)).
9. **The maintainer's GitHub account is protected.** The project has a single
   maintainer; the repository's `main` branch has no branch protection or
   rulesets today (checked with the GitHub API on 2026-09-17).

## Attack surfaces by deployment mode

### Local hooks and CLI

- Inputs: commit message files, `git` output for commits and ranges, pre-push
  stdin (ref updates), `.commitguard.yaml` in the work tree, global
  configuration, command-line arguments.
- Executes: `git` via `src/commitguard/git/commands.py` →
  `src/commitguard/utils/subprocess.py`; hook scripts rendered by
  `src/commitguard/git/hooks.py`.
- Writes: hook files in the repository's hooks directory (existing hooks are
  preserved and chained), `.commitguard.yaml` on `init` (never overwrites).
- Trust: fully under the developer's control; bypassable by design.

### GitHub Actions check

- Inputs: the event payload, the checked-out history, the trusted commit's
  `.commitguard.yaml`, action inputs (`config`, `fail-on`, `max-commits`,
  `python-version`).
- Executes: `pip install` of the hash-pinned lock and of CommitGuard from the
  Action's own path (`action.yml`) or from a trusted commit via
  `git worktree` (`.github/workflows/commitguard.yml`), then
  `python -P -m commitguard ci github`.
- Writes: workflow commands, annotations, job summary, step outputs (enums and
  integers only).
- Token: `contents: read`, no secrets, `persist-credentials: false`.

### GitHub App service

- Inputs: webhook deliveries (`installation`, `installation_repositories`,
  `pull_request`, `push`, optional `check_run`, `check_suite`, `merge_group`),
  GitHub REST responses, Git objects fetched into bare mirrors, the optional
  mandatory policy file, environment configuration.
- Executes: `git init --bare --template=` and hardened `git fetch` in
  `src/commitguard/github/repositories.py`; no checkout, no hooks, no builds.
- Writes: Check Runs, SQLite state, mirrors under numeric installation and
  repository directories, structured JSON logs.
- Credentials: App private key (JWT), installation tokens (one repository,
  about one hour, memory only).

### Dashboard and JSON API

- Inputs: `/api/v1/...` requests with a session cookie, CSRF header and JSON
  bodies; OAuth callback parameters; static file paths.
- Writes: policy versions, member roles, acknowledgements, notification
  settings and webhook endpoints, sessions, audit events.
- Credentials: GitHub App client secret; the GitHub user token is used during
  sign-in and discarded.

### Organization governance

- Inputs: settings, policy drafts, approvals, exceptions, rollouts,
  simulations, bulk operations, scan schedules, organization rules, group
  membership, report and search requests.
- Background work: effective policy propagation, exception expiry, rollouts,
  simulations, schedules, bulk operations (leased, bounded).
- Outputs: effective policies consumed by scans, JSON and CSV reports,
  notifications.

### Benchmark and research tooling

- `commitguard benchmark dataset|detection|performance|hooks|repository`
  (`src/commitguard/research/`, `src/commitguard/cli/commands/benchmark.py`).
- Inputs: generated datasets or a `--dataset` directory of JSONL files
  (validated with pydantic), the local Git installation.
- Executes: real `git` in temporary directories with a private `HOME` and
  global configuration (`src/commitguard/research/gitenv.py`), including the
  production hook installer.
- Writes: new result files under `benchmarks/results/raw/<benchmark>/`
  (existing files are never overwritten) and `benchmarks/results/processed/index.json`.
  Manifests record OS, CPU model, memory, Python and Git versions, the source
  revision and whether the checkout was dirty; they are committed to a public
  repository.
- Trust: a developer tool run on trusted input; its security relevance is the
  integrity of published measurements and the findings it surfaces.

### The project's own supply chain and CI

- Workflows: `.github/workflows/ci.yml`, `commitguard.yml`, `security.yml`;
  the composite Action `action.yml`.
- Dependencies: PyPI (runtime, dev and build backend), GitHub Actions,
  npm packages for `web/` (`web/package-lock.json`).
- Distribution: source only (Git). No releases, tags, wheels or PyPI uploads
  exist. Reviewed in [supply-chain.md](supply-chain.md) and
  [ci-pipeline-security.md](ci-pipeline-security.md).

## STRIDE threat register

Categories: **S** spoofing, **T** tampering, **R** repudiation, **I**
information disclosure, **D** denial of service, **E** elevation of privilege.
Evidence paths are relative to `tests/`. Files in the security regression suite
(`pytest -m security`, defined by `SECURITY_TEST_PATHS` in `tests/conftest.py`)
are marked with (sec). Test files named `test_dashboard_*.py` and
`test_governance_*.py` without a directory are in
`integration/github/app/dashboard/`.

### Local hooks and CLI

| ID | STRIDE | Threat | Mitigation | Evidence | Status |
|---|---|---|---|---|---|
| S-L-1 | T | Crafted message, identity or branch name injects shell commands | no shell anywhere; argument vectors; `shell=False` enforced by a test | `unit/security/test_safe_subprocess.py` (sec), `unit/test_architecture.py::test_no_shell_true_anywhere`, `integration/hooks/test_commit_hooks.py::test_malicious_message_is_plain_text`, `integration/hooks/test_pre_push.py::test_malicious_branch_names` | [done] |
| S-L-2 | T | Revision interpreted as a Git option (`--output=…`) | `validate_revision` rejects leading `-` and control characters; `--end-of-options` before revisions | `unit/security/test_validation.py` (sec) | [done] |
| S-L-3 | T | `git replace` or `.mailmap` substitutes an innocent commit or identity | `GIT_NO_REPLACE_OBJECTS=1`; `--no-use-mailmap` | code: `src/commitguard/git/commands.py`, `src/commitguard/git/repository.py` | [done] |
| S-L-4 | S / T | Attribution hidden by formatting (Unicode look-alikes, invisible characters, separators, leading symbols, trailer floods) | normalisation and bounded parsing; floods fail closed | `unit/provenance/test_trailers.py` (sec), `unit/provenance/test_identity_parsing.py` (sec), `unit/detectors/test_coauthor.py`, detection benchmark results in `benchmarks/results/raw/detection/` | [done] for known classes; see [findings](#security-findings-to-date) |
| S-L-5 | I / T | Terminal escape sequences in commit data spoof or hide output | ESC, C0/C1 and bidi controls made visible before display | `unit/security/test_sanitization.py` (sec) | [done] |
| S-L-6 | E | A `commitguard/` package in the work tree hijacks the hook | hooks run `python -P -m commitguard` | `integration/hooks/test_commit_hooks.py::test_repository_cannot_shadow_the_commitguard_package` | [done] |
| S-L-7 | T | Errors, missing installation or invalid configuration silently allow the operation | every error exits 2 and blocks; missing CommitGuard blocks | `integration/hooks/test_hook_templates.py::test_missing_commitguard_blocks_commit`, `integration/hooks/test_commit_hooks.py::test_invalid_configuration_fails_closed`, `integration/hooks/test_pre_push.py::test_malformed_stdin_fails_closed`, `unit/core/test_engine.py::test_detector_exception_becomes_sanitised_failure` | [done] |
| S-L-8 | T | Developer bypasses hooks (`--no-verify`, deleting hooks, `core.hooksPath`, another clone) | not prevented locally (tests assert Git's bypass works); `doctor` reports states; server-side checks catch the commits | `integration/hooks/test_commit_hooks.py::test_no_verify_bypasses_local_commit_hooks`, `integration/github/security/test_bypass_resistance.py` (sec) | limitation (mitigated server-side) |
| S-L-9 | T | Existing hooks destroyed or shared hooks path modified for all repositories | foreign hooks preserved and chained; shared `core.hooksPath` needs explicit permission | `integration/hooks/test_install.py::test_existing_hook_is_preserved_chained_and_restored`, `::test_shared_hooks_path_requires_explicit_permission` | [done] |
| S-L-10 | S | Hook fallback runs a different program named `commitguard` from `PATH` (for example the unrelated PyPI package) | the wrapper prefers the recorded interpreter (`python -P -m commitguard`); the `PATH` fallback does not verify which project the executable belongs to | `integration/hooks/test_hook_templates.py::test_falls_back_to_commitguard_on_path` (fallback exists) | limitation |
| S-L-11 | D | Huge ranges or messages exhaust resources | `max_push_commits` and `--max-commits` fail closed; message files limited to 1 MiB; linear-time parsing | `integration/hooks/test_pre_push.py::test_too_many_commits_fails_closed`, `unit/provenance/test_trailers.py` | [done]; captured Git output is not size-bounded (TODO in `src/commitguard/utils/subprocess.py`) |

### GitHub Actions check

| ID | STRIDE | Threat | Mitigation | Evidence | Status |
|---|---|---|---|---|---|
| S-A-1 | T / E | Pull request relaxes `.commitguard.yaml` to approve itself | policy read from the trusted base commit; changes reported as a notice | `integration/github/test_ci_github.py::test_pull_request_cannot_disable_its_own_policy`, `::test_config_input_is_read_from_trusted_commit`, `integration/github/security/test_bypass_resistance.py::test_pull_request_cannot_weaken_its_own_policy` (sec) | [done] |
| S-A-2 | T | Pull request edits `rules/` to weaken detection | rule files in the scanned repository are never read | `integration/github/test_ci_github.py::test_rule_tampering_in_repository_has_no_effect` | [done] |
| S-A-3 | T | Crafted metadata injects workflow commands or shell | env-only inputs; `::stop-commands::<random>`; escaped annotations and summary | `integration/github/test_ci_security.py` (sec), `unit/github/test_events_and_output.py::test_workflow_command_escaping`, `unit/github/test_workflows_static.py::test_enforcement_run_scripts_never_interpolate_expressions` | [done] |
| S-A-4 | I / E | Fork pull request steals secrets or writes to the repository | `pull_request` only, `contents: read`, no secrets, `persist-credentials: false` | `integration/github/test_ci_security.py::test_fork_pull_request_needs_no_secrets_or_write_access` (sec), `unit/github/test_workflows_static.py::test_no_secrets_and_no_write_permissions` | [done] |
| S-A-5 | T | Scanner cannot evaluate and the check passes | every error exits non-zero and prints `Result: FAILED` | `integration/github/test_ci_github.py::test_git_unavailable_fails`, `::test_commitguard_import_failure_fails`, `::test_shallow_checkout_fails_with_actionable_message`, `::test_malformed_or_unsupported_events_fail` | [done] |
| S-A-6 | T | Pull request edits the workflow that runs the check | not preventable with `pull_request`; CODEOWNERS or rulesets requiring a workflow from another repository | `integration/github/security/test_bypass_resistance.py::test_ci_bypass_by_removing_the_workflow_leaves_no_successful_check` (sec) | limitation |
| S-A-7 | T | Malicious YAML in the trusted policy | strict safe loader; tags, aliases and duplicate keys rejected | `integration/github/test_ci_github.py::test_malicious_yaml_in_trusted_policy_is_not_executed`, `unit/config/test_config_validation.py` | [done] |
| S-A-8 | T | Direct push or merge without the check being required | push-triggered check detects after the fact; prevention needs branch protection | documented in `docs/github-enforcement.md` | limitation |

### GitHub App service

| ID | STRIDE | Threat | Mitigation | Evidence | Status |
|---|---|---|---|---|---|
| S-G-1 | S | Forged webhook | HMAC-SHA256 over the raw body, constant-time comparison, verified before parsing; `401` otherwise | `unit/github/app/test_webhooks.py` (sec), `integration/github/app/security/test_server_enforcement_experiments.py::test_forged_and_tampered_webhooks_are_rejected` (sec) | [done] |
| S-G-2 | T / R | Replayed or reordered webhook changes a newer decision | delivery IDs stored with payload digest; same ID with a different payload refused; per-SHA Check Run ownership | `integration/github/app/test_app_concurrency.py`, `integration/github/app/security/test_server_enforcement_experiments.py::test_replayed_and_out_of_order_events_cannot_overwrite_newer_decisions` (sec), `unit/github/app/test_storage.py::test_delivery_replay_protection` | [done] |
| S-G-3 | E / I | Spoofed installation or repository reaches another tenant | installation token minted per scan for one repository ID; repository looked up by immutable ID | `integration/github/app/test_app_security.py::test_spoofed_installation_id_cannot_reach_another_tenant`, `::test_repository_outside_installation_is_not_accessed` (sec) | [done] |
| S-G-4 | I | Secrets leak into logs, errors, Checks or responses | `Secret` wrapper; redaction of registered values and credential-shaped strings | `unit/github/app/test_secrets_and_logging.py` (sec), `integration/github/app/test_app_security.py::test_secrets_never_leak` (sec) | [done] |
| S-G-5 | E | Repository code executes in the service | no checkout, no work tree, `--template=`, null hooks path, no submodules, no builds | code: `src/commitguard/github/repositories.py`; `unit/test_architecture.py::test_git_fetch_happens_only_in_the_mirror_manager` | [done] |
| S-G-6 | T | Outage, rate limit or revoked permission produces a false PASS | bounded retries; failures publish `failure`/`timed_out` or no check | `integration/github/app/test_app_security.py::test_github_outage_during_scan_fails_closed_with_bounded_retries`, `::test_persistent_rate_limit_fails_closed`, `::test_reduced_permissions_never_pass`, `integration/github/app/security/test_server_enforcement_experiments.py::test_database_unavailable_fails_closed` (sec) | [done] |
| S-G-7 | T | Result attached to the wrong commit or a stale scan overwrites a newer one | Check Runs created for the fetched SHA; guarded writes; superseded scans cancelled | `integration/github/app/test_app_concurrency.py::test_older_pull_request_scan_finishing_later_cannot_affect_newer_head`, `unit/github/app/test_storage.py::test_check_slot_ownership_prevents_stale_writes` | [done] |
| S-G-8 | T | Merge queue or re-run validates the wrong commit | queue refs and head SHA validated; stale re-runs refused | `integration/github/app/test_app_merge_queue_reruns.py` (sec) | [done] |
| S-G-9 | E | Stolen installation token or private key | tokens: one repository, least privilege, about one hour, memory only; key: file or environment, redacted, `validate` warns on group/other-readable key files | `unit/github/app/test_auth.py::test_installation_tokens_are_scoped_cached_and_refreshed`, `integration/github/app/test_app_security.py::test_private_key_errors_are_safe` (sec) | partially mitigated (key theft is operational; see [incident response](incident-response.md)) |
| S-G-10 | I | SSRF through payload URLs | fixed API and Git hosts, HTTPS only, same-origin pagination | `unit/github/app/test_client.py::test_pagination_follows_same_origin_links_only`, `::test_urllib_transport_refuses_non_https` | [done] |
| S-G-11 | D | Webhook flood or oversized payload | 25 MB limit from `Content-Length`, JSON depth limit, per-address rate limit, bounded queue, commit limit, timeouts | `unit/github/app/test_webhooks.py::test_oversized_payload_is_rejected` (sec), `integration/github/app/test_app_security.py::test_commit_limit_fails_closed` (sec) | [done]; see [remaining risks](#remaining-risks) for rate-limit keying |
| S-G-12 | T | Repository configuration weakens the operator's mandatory policy | mandatory policy is a floor; `enabled: false` rejected | `integration/github/app/test_app_security.py::test_mandatory_policy_overrides_trusted_repository_config` (sec), `integration/github/app/security/test_server_enforcement_experiments.py::test_mandatory_policy_floor_beats_repository_configuration` (sec) | [done] |
| S-G-13 | R | Actions cannot be traced | audit events for installations, rejected webhooks, scans; correlation IDs in logs | `integration/github/app/test_app_merge_queue_reruns.py::test_job_crashing_on_every_attempt_is_audited_and_fails_its_check` (sec) | [done] |

### Dashboard and JSON API

| ID | STRIDE | Threat | Mitigation | Evidence | Status |
|---|---|---|---|---|---|
| S-D-1 | S | Unauthenticated access | GitHub App user authorization with browser-bound state and PKCE; `401` on every protected route | `integration/github/app/dashboard/test_dashboard_authorization.py::test_unauthenticated_requests_are_rejected`, `::test_sign_in_flow_is_bound_to_the_browser` (sec) | [done] |
| S-D-2 | S | Session theft or fixation | `__Host-` cookie, `HttpOnly`, `Secure`, `SameSite=Lax`; SHA-256 stored; 8 h absolute, 2 h idle; revocation | `test_dashboard_authorization.py::test_session_lifecycle_expiry_logout_and_revocation`, `::test_absolute_session_lifetime` (sec) | [done] (a stolen cookie works until expiry or revocation) |
| S-D-3 | T | CSRF | allowed `Origin` plus `X-CSRF-Token` derived from the session; JSON only | `test_dashboard_security.py::test_writes_require_origin_and_csrf_token`, `::test_csrf_token_is_bound_to_the_session` (sec) | [done] |
| S-D-4 | I / E | IDOR and cross-tenant reads | access scope on every query; out-of-scope is `404` | `test_dashboard_authorization.py::test_tenants_cannot_read_each_other`, `::test_idor_on_every_write_is_rejected`, `::test_repositories_hidden_when_github_denies_the_user` (sec) | [done] |
| S-D-5 | E | Role escalation or privilege misuse | permissions resolved per request on the server; owner-only member management; no self role change; last owner protected | `test_dashboard_authorization.py::test_viewer_reads_but_cannot_change_anything`, `::test_security_manager_triage_but_not_policy`, `::test_owner_rules_for_members`, `::test_role_changes_apply_to_the_next_request` (sec) | [done] |
| S-D-6 | T / I | XSS through Git metadata | stored as sanitised text; React text rendering; CSP without `unsafe-inline`/`unsafe-eval`; `nosniff` | `test_dashboard_security.py::test_untrusted_git_metadata_is_returned_as_inert_text`, `::test_security_headers_by_environment` (sec) | [done] |
| S-D-7 | T | SQL injection, path traversal | SQL from constant fragments with bound parameters; allow-listed sorts; strict route patterns; static files resolved inside the build directory | `test_dashboard_security.py::test_injection_attempts_are_validated_or_inert`, `::test_static_site_path_traversal` (sec), `unit/test_architecture.py::test_api_routes_contain_no_sql` | [done] |
| S-D-8 | I | Tokens or secrets reach the browser or logs | view models only; user token discarded; request logs without headers, cookies or query strings | `test_dashboard_authorization.py::test_github_user_token_is_never_stored_or_returned`, `test_dashboard_security.py::test_secrets_never_reach_responses_or_logs` (sec) | [done] |
| S-D-9 | S | Open redirect after sign-in | `return_to` limited to same-origin paths | `test_dashboard_authorization.py::test_sign_in_never_redirects_off_site` (sec) | [done] |
| S-D-10 | D | API abuse | per-user or per-address rate limits per operation class; 64 KB bodies; page size ≤ 100 | `test_dashboard_security.py::test_rate_limits`, `::test_request_parsing_limits` (sec) | [done] |
| S-D-11 | E | Stale GitHub access after removal on GitHub | access lists read at sign-in; sessions end within 8 hours | `test_dashboard_authorization.py::test_absolute_session_lifetime` (sec) | partially mitigated |

### Organization governance

| ID | STRIDE | Threat | Mitigation | Evidence | Status |
|---|---|---|---|---|---|
| S-O-1 | I / E | Cross-organization access to governance resources | resource loaded with its organization; non-members get `404` before body parsing | `integration/github/app/dashboard/test_governance_isolation.py::test_other_tenants_get_not_found_for_every_governance_route` (sec) | [done] |
| S-O-2 | E | Viewer or security manager changes policy, approves or publishes | per-action permissions checked in services | `test_governance_isolation.py::test_viewers_cannot_change_anything`, `::test_security_manager_can_request_but_not_approve_or_publish` (sec) | [done] |
| S-O-3 | E / R | Self-approval or approving one document and publishing another | `require_separate_approver` (default on) blocks authors; approval bound to the document fingerprint; exception requesters can never approve their own request | `integration/github/app/dashboard/test_governance_workflow.py::test_approval_workflow_enforces_separation_of_duties` (not in the security marker set) | [done] when `require_policy_approval` is enabled (default off) |
| S-O-4 | E / R | Emergency publication abused | owner-only `policies:emergency`; required reason; critical audit event and mandatory notification | `test_governance_workflow.py::test_emergency_publication_is_owner_only_reasoned_and_loud` | [done] (an owner can still act alone, by design) |
| S-O-5 | T | Narrower layers weaken mandatory requirements | floors cannot be lowered by group, repository or `.commitguard.yaml` | `integration/github/app/dashboard/test_governance_policies.py::test_mandatory_organization_policy_overrides_repository_and_explains_the_conflict`, `unit/policies/test_governance_resolution.py` | [done] |
| S-O-6 | T | Stale effective policy used by a scan | same-transaction invalidation; resolution errors fail closed | `test_governance_policies.py::test_policy_changes_invalidate_and_propagate_effective_policies` | [done] |
| S-O-7 | E | Code execution or ReDoS through organization rules | identity data only, exact comparison; architecture test forbids dynamic execution in governance and policies | `unit/controlplane/test_governance_units.py::test_organization_rules_are_data_only`, `unit/test_architecture.py::test_governance_policies_do_not_execute_configuration` | [done] |
| S-O-8 | T | CSV formula injection in reports | cells starting with `=`, `+`, `-`, `@`, tab or carriage return prefixed | `unit/controlplane/test_governance_units.py::test_csv_cells_cannot_become_spreadsheet_formulas` | [done] |
| S-O-9 | T | Settings weakened quietly | relaxing a control needs confirmation, reason and a sign-in within 15 minutes; audited and notified | `unit/controlplane/test_governance_units.py::test_settings_weakening_is_detected_per_control`, `integration/github/app/dashboard/test_dashboard_policies.py::test_weakening_needs_confirmation_reason_and_recent_sign_in` | [done] |
| S-O-10 | I | False "secure" posture or certification claims | explicit states with reasons; reports state they are not certifications; disconnects put posture at risk | `test_governance_operations.py::test_compliance_reports_are_point_in_time_and_make_no_certification_claims`, `::test_disconnected_installation_puts_the_organization_at_risk_and_recovers` | [done] |

### Notifications

| ID | STRIDE | Threat | Mitigation | Evidence | Status |
|---|---|---|---|---|---|
| S-N-1 | S | Forged CommitGuard webhook to a receiver | per-endpoint HMAC-SHA256 over timestamp and body; five-minute window; delivery ID | `unit/notifications/test_notification_units.py::test_webhook_signature_timestamp_and_per_endpoint_secrets` | [done] (receiver must verify) |
| S-N-2 | I | Exfiltration or SSRF through a webhook endpoint | `notifications:manage`, confirmation, recent sign-in; HTTPS; public address checked at send time and connection pinned; no redirects | `unit/notifications/test_notification_units.py::test_pinned_transport_refuses_private_addresses_after_resolution`, `::test_webhook_urls_are_validated` | [done] |
| S-N-3 | I / E | Notification IDOR or silencing mandatory notifications | inbox rows per user with role and visibility re-checked; mandatory types cannot be muted | `integration/github/app/dashboard/test_dashboard_notifications.py::test_notification_idor_and_state_changes`, `::test_mandatory_critical_notifications_reach_every_eligible_member` | [done] |
| S-N-4 | T | E-mail header injection | recipients and headers validated; plain-text messages | `unit/notifications/test_notification_units.py::test_email_recipients_reject_header_injection_and_display_names` | [done] |
| S-N-5 | T | Delivery failure changes the security decision | decision, violation and check stored before delivery | `test_dashboard_notifications.py::test_email_outage_never_changes_the_security_decision` | [done] |

### Benchmark and research tooling

| ID | STRIDE | Threat | Mitigation | Evidence | Status |
|---|---|---|---|---|---|
| S-R-1 | T | Benchmark modifies the developer's Git configuration or repositories | temporary directories with private `HOME` and global configuration | code: `src/commitguard/research/gitenv.py` | [done] |
| S-R-2 | T / R | Published results overwritten or overstated | result files written once, never overwritten; manifest records source revision, dirty state, dataset version and fingerprint; mismatches listed per case | result files in `benchmarks/results/raw/` | [done] |
| S-R-3 | I | Manifest discloses host details | `BenchmarkManifest` has no host name or user name fields; it records OS, CPU model, memory, tool versions and the command line as typed, which can contain absolute paths (a committed performance result contains a home directory path); results are public once committed | code: `src/commitguard/research/environment.py`; `benchmarks/results/raw/performance/` | accepted; pass relative `--record` paths |
| S-R-4 | T | Dataset labels derived from the implementation hide failures | labels come from documented semantics, not from current output (documented in `src/commitguard/research/datasets.py`) | dataset versions `benchmarks/datasets/v1.0.0`, `v1.1.0` | [done] |

### Project supply chain and CI

| ID | STRIDE | Threat | Mitigation | Evidence | Status |
|---|---|---|---|---|---|
| S-SC-1 | T | Compromised third-party action | actions pinned to full commit SHAs | `unit/github/test_workflows_static.py::test_all_third_party_actions_are_pinned_to_commit_shas` | [done] |
| S-SC-2 | T | Dependency substitution when installing the scanner in CI | `--require-hashes` lock, `--no-deps`, `--no-build-isolation`, CommitGuard installed by path | `unit/github/test_workflows_static.py::test_action_inputs_are_only_supported_options`, `::test_repository_workflow_installs_commitguard_from_trusted_commit`, `integration/github/test_action_scripts.py` (network) | [done] for the Action and `commitguard.yml`; not for `ci.yml` and `security.yml` tool installs ([details](ci-pipeline-security.md)) |
| S-SC-3 | S / T | **Dependency confusion through the PyPI name.** `pip install commitguard` (or `commitguard[app]`) installs an unrelated project (`yezz123/CommitGuard`, a Git hooks library, versions up to 2.2.0) that provides its own `commitguard` import package and `commitguard` console script | this project publishes nothing to PyPI; install from a pinned Git commit; documentation that instructed `pip install 'commitguard[app]'` is being corrected; `ci.yml` is tested not to install from PyPI | `unit/github/test_workflows_static.py::test_ci_workflow_installs_checked_out_source_not_pypi` | open: a distribution name is an open maintainer decision ([supply-chain.md](supply-chain.md#name-collision-on-pypi)) |
| S-SC-4 | T | Vulnerable dependency | `pip-audit` on push, pull request and weekly; `actions/dependency-review-action` on pull requests | `.github/workflows/security.yml` | partial: `pip-audit` audits the base install only (not `cryptography` from `[app]`, not `web/`) |
| S-SC-5 | E | Maintainer account or `main` branch compromise | secret scanning and push protection enabled on the repository | GitHub settings (API, 2026-09-17) | gap: no branch protection, no rulesets, no signed commits or releases |

## Security findings to date

These are the security-relevant defects found in CommitGuard so far. All were
found by the project's own benchmarks and review; no external reports have
been received. Full records are in
[vulnerability-response.md](vulnerability-response.md#security-fixes-to-date).

| # | Date | Finding | Category | Status |
|---|---|---|---|---|
| 1 | 2026-09-17 | A replacement character or other symbol directly before `Co-authored-by:` hid the trailer (1 false negative in 9,115 cases, dataset v1.0.0) | detection bypass (T) | fixed in `src/commitguard/provenance/trailers.py` |
| 2 | 2026-09-17 | The first fix for #1 reported bulleted human trailers (`- Reviewed-by: …`) as malformed (2 false positives, dataset v1.1.0) | regression (false positive) | fixed the same day |
| 3 | 2026-09-17 | Normalising very large commit messages was slow (10 MB message: about 4.8 s p50 before, about 0.75 s after) | denial of service (D) | mitigated with an ASCII fast path in `src/commitguard/provenance/normalization.py` |
| 4 | 2026-09-17 | Documentation instructed `pip install 'commitguard[app]'`, which resolves to the unrelated PyPI project | dependency confusion (S/T) | being corrected in the documentation |
| 5 | 2026-09-17 | Non-ASCII letters or numbers directly before the key (for example U+2460, U+24DE, U+32AC) hid the trailer (11 false negatives, dataset v1.2.0) | detection bypass (T) | fix in development; not on `main` at the time of writing |
| – | ongoing | Local hooks are bypassable by the person who controls the repository | documented limitation, not a vulnerability | see S-L-8 |

## Remaining risks

Risks that remain after the mitigations above, roughly in order of impact.

1. **Self-asserted metadata.** Removing attribution before committing cannot
   be detected from metadata. This is inherent to the problem.
2. **Enforcement depends on GitHub configuration.** Without branch protection
   or a ruleset requiring the check, violations can be merged. A pull request
   can edit the workflow that runs the Action check (S-A-6). This repository's
   own `main` branch has no protection or ruleset today.
3. **Detection bypasses not yet found.** The adversarial datasets cover known
   classes; finding #5 shows new classes still appear. Detection is exact
   matching on normalised metadata, not a proof.
4. **Name collision on PyPI.** Anyone following old instructions, searching
   PyPI, or running `pip install --upgrade commitguard` in an environment where
   this project is installed as `0.1.0.dev0` gets unrelated code with a
   `commitguard` executable, which the hook wrapper's `PATH` fallback runs
   when the interpreter recorded at installation is missing.
5. **Single maintainer and no external review.** There is no second reviewer
   for changes, no independent security review, and response capacity is one
   person's.
6. **Separation of duties is opt-in.** `require_policy_approval` is off by
   default, so an admin can publish policy changes alone. An admin can turn
   approval off (with confirmation, a reason and a recent sign-in, audited and
   notified), and an owner can always publish in an emergency.
7. **The operator and the database are fully trusted.** Triggers that make
   policy versions and audit events immutable do not protect against direct
   database access; audit events are not hash-chained or signed, and retention
   deletes old audit events.
8. **Session and GitHub access lag.** Changes to a user's GitHub access apply
   at the next sign-in (up to 8 hours); a stolen session cookie works until it
   expires or is revoked. Users can revoke only their own sessions from the
   dashboard; removing a member deletes their sessions.
9. **Rate limits are per process and keyed by `REMOTE_ADDR`.** CommitGuard
   does not read `X-Forwarded-For`; behind a reverse proxy every unauthenticated
   client (including GitHub's webhook deliveries) shares the proxy's address
   and one limit (600 webhook requests per minute per address). The limiter
   also clears its table when it tracks more than 10,000 addresses. A flood can
   therefore delay legitimate deliveries; failures leave the check missing or
   failed rather than passing.
10. **Resource bounds are incomplete.** There is no per-message size limit
    for commits read from Git objects, and captured Git output is not
    size-bounded (`TODO(phase-2)` in `src/commitguard/utils/subprocess.py`);
    timeouts and commit limits bound the total work.
11. **Single host.** SQLite allows one writer; one service instance per
    database. Git client bugs in `git fetch` remain an upstream risk.
12. **Unpinned tooling in this repository's CI** (`ci.yml` development
    dependencies, `pip-audit`, `ruff`, `bandit`) and no dependency update
    automation; see [ci-pipeline-security.md](ci-pipeline-security.md).

## Detailed threat catalogue

The tables below are the per-phase catalogue from v1, kept because they record
detailed mitigations and limits. Statuses were re-checked for v2; corrected
entries say so.

### Organization governance (Phase 8)

```text
settings · groups · rules · drafts ─approve─▶ publish ─▶ immutable version ─▶ rollout
                                                              │
exceptions (scoped, expiring) ────────────────────────────────┤
                                                              ▼
                 resolver (same-transaction invalidation) ─▶ effective policy ─▶ policy engine ─▶ check
```

| Threat | Mitigation / limitation | Status |
|---|---|---|
| Cross-organization access (IDOR) to groups, drafts, exceptions, rollouts, simulations, bulk operations, schedules, reports | Every governance service loads the resource with its organization and checks membership first: another organization's resource is `404`, never `403`; referenced repositories must belong to the same organization **and** be visible to the caller on GitHub; a sweep test calls every governance route as a member of another organization, as a viewer and unauthenticated | **[done]** |
| Validation leaking existence (400 before 403) | Permission checks run before request bodies are parsed; the sweeps send empty, invalid bodies and require `404` (other organization) or `403` (viewer) | **[done]** |
| Privilege escalation through roles | New permissions (`organization:*`, `policies:publish/approve/emergency`, `exceptions:*`, `rules:manage`, `security:*`) are assigned per role on the server; viewers can read but not request exceptions; security managers can request but not approve or publish; only owners can emergency-publish (tested) | **[done]** |
| Approval bypass | With `require_policy_approval`, publication needs an approved draft and the Phase 6 direct save is refused with `APPROVAL_REQUIRED`; approval binds to the draft's document fingerprint and editing an approved draft returns it to `draft`; one pending approval per draft (unique index); drafts publish against their base version, so a draft approved against an outdated policy conflicts instead of overwriting newer changes | **[done]** (`require_policy_approval` is off by default) |
| Self-approval | With `require_separate_approver` (default on) the creator or submitter of a draft cannot approve it; the requester of an exception can never approve it | **[done]** |
| Emergency publish abuse | Separate `policies:emergency` permission (owners); required reason stored on the version; critical audit event and mandatory notification in the same transaction; shown in the policy history | **[done]** (by design, an owner can still act alone) |
| Weakening through narrower layers | Mandatory requirements are floors that group policies, repository policies and `.commitguard.yaml` cannot lower; attempts are recorded as conflicts and drift, never applied (pure resolver tests, end-to-end scan tests) | **[done]** |
| Exception abuse | One rule, one explicit scope (repository, group or organization); only `warn`/`allow`; required reason and expiry within `exception_max_days`; permanent only with an organization setting, an approver-level requester and approval by someone else (database check constraint); group and organization scopes always need approval; revocation audited; rows cannot be deleted (trigger); expiry enforced by the resolver even if the expiry worker is late | **[done]** |
| Stale effective policy (a change not applied to a scan) | Changes invalidate affected cache rows in the same transaction; scans use a cached policy only when it is up to date and no included exception has expired, otherwise resolve from the database; resolution errors fail the scan closed; propagation is never reported complete until every repository is up to date | **[done]** |
| Group membership used to escape a policy | Membership changes need `repositories:manage`, are audited, invalidate the affected repositories and apply organization mandatory requirements regardless of groups; archiving a group with a policy or exceptions needs confirmation and ends its exceptions | **[done]** |
| Monitor mode used to stop blocking silently | Mode changes need `repositories:manage`, confirmation and a reason for monitor; audited; alerts say "Would block (monitor mode)"; monitor-mode repositories are never shown as secure | **[done]** |
| Rollout confusion (wrong version applied) | Enrollment is recorded per repository; resolution is rollout-aware and each scan records the version and rollout it used; one rollout in progress per target (unique index); rollback publishes a new version through the Phase 7 path; automatic halt on error or block thresholds | **[done]** |
| Settings weakened quietly | Versioned settings with optimistic concurrency; relaxing a control (disabling approval, allowing permanent exceptions, lowering the baseline) needs confirmation, a reason and a sign-in within 15 minutes; audited and notified | **[done]** |
| Code execution or ReDoS through organization rules | Rules are identity data (names, prefixes, e-mails, logins) compared exactly after normalisation; no regular expressions, wildcards, expressions or imports; size limits; architecture test forbids `eval`/`exec`/`compile`/dynamic imports in `governance` and `policies` | **[done]** |
| Simulation side effects | Simulations only read recorded scans and findings and store their own result; no checks, violations, notifications or versions change (tested by comparing state before and after); they cannot run detectors or fetch Git data | **[done]** |
| Resource exhaustion (simulations, bulk operations, schedules, propagation, reports) | Queued background work with leases; per-organization caps (3 open simulations, 10 open bulk operations, 100 schedules, 500 groups); per-run bounds (5,000 scans / 50,000 findings, 5,000 items, 50 scheduled repositories per pass and no queuing above 200 waiting scans, 200 propagations per pass); reports cut at 10,000 rows with `truncated`; request rate limits | **[done]** |
| Duplicate bulk operations or scheduled scans | Idempotency key unique per organization; items idempotent; schedule runs unique per slot and scan job keys per slot | **[done]** |
| CSV formula injection in reports | Cells starting with `=`, `+`, `-`, `@`, tab or carriage return are prefixed; served as attachments | **[done]** |
| Report and search data leakage | Rows and results limited to repositories the requester can see on GitHub; policy change reports need `audit:read`; exports audited | **[done]** |
| Misleading compliance claims | Posture is explicit states with reasons; compliance is a defined fraction; reports state they are not SOC 2, ISO 27001 or any certification; unavailable enforcement, failed synchronisation and propagation errors put posture at risk | **[done]** |
| Removed member keeps a session | Removing a member's last membership deletes their sessions (next request is `401`); role changes apply on the next request because permissions are resolved server-side per request | **[done]** |
| Concurrent administrators overwriting each other | `expected_version` / `expected_revision` on settings, rules, drafts, schedules and policy targets; conflicts return `409` | **[done]** |
| Notification noise hiding real events | Optional hourly digest per rule for e-mail and webhooks; mandatory notifications (disconnects, unprotected repositories, emergency publish, rollout and propagation failures) are never aggregated or muted; acknowledgement never resolves anything | **[done]** |
| Single-instance scale limits | SQLite is single-writer; posture pages compute per request (about one second at 10,000 repositories); see [security-posture.md](../security-posture.md#performance) | limitation |

### Notifications, merge queue, re-runs and policy recovery (Phase 7)

```text
domain change ─▶ outbox event (same transaction) ─▶ dispatcher ─▶ inbox rows / delivery records
                                                                  ─▶ SMTP · signed HTTPS webhook
GitHub event ─▶ signature ─▶ event record (delivery ID) ─▶ normalise ─▶ execution ─▶ Check Run
```

| Threat | Mitigation / limitation | Status |
|---|---|---|
| Notification spoofing (a forged "policy rolled back" reaching a receiver) | Outbound webhooks are signed with a per-endpoint HMAC-SHA256 secret over timestamp and body (`X-CommitGuard-Signature`), with a documented verification recipe; secrets are derived from a deployment key and never stored; e-mail goes through the operator's configured relay | **[done]** |
| Webhook replay against a receiver | Signed timestamp with a five-minute acceptance window, plus a stable `X-CommitGuard-Delivery` idempotency key receivers use to discard repeats | **[done]** (receiver must check both) |
| Notification spam / duplicate flooding | Domain keys with coalescing windows (one notification per pull request, rule and hour, however many commits); unique outbox and delivery keys; replayed GitHub deliveries are duplicates; bounded retries; rate limits on settings and webhook changes | **[done]** |
| Notification IDOR | Inbox rows belong to one user; every read re-checks the row's user, the member's current role for the type, and the installation and repository GitHub reported for the session; anything else is `404` (tested across tenants and users) | **[done]** |
| Silencing security notifications | Personal preferences affect only the caller's inbox; organization settings need `notifications:manage`; mandatory types (critical violations, policy changes and rollbacks, installation disconnects) cannot be muted or turned off in-app; turning a delivery off needs explicit confirmation and is audited | **[done]** |
| Data exfiltration through a webhook endpoint | Only `notifications:manage` may add one, with explicit confirmation and a sign-in within 15 minutes; HTTPS only; the host is resolved at send time, refused unless public in production, and the connection is pinned to the checked address (no DNS rebinding); redirects are never followed; at most 10 endpoints; payloads carry no tokens or secrets | **[done]** |
| E-mail header and HTML injection | Plain-text messages built with `EmailMessage` (line breaks in headers rejected); notification text is sanitised and truncated when the event is created; webhook JSON escapes `<`, `>` and `&` | **[done]** |
| Policy rollback abuse (restoring a weak policy quietly) | Separate `policies:rollback` permission; required reason; explicit confirmation; recent sign-in when the rollback weakens a floor; optimistic concurrency; immutable versions (database triggers refuse `UPDATE`/`DELETE`); target integrity check by fingerprint; audit event and notification in the same transaction (tested, including concurrent rollback and publish) | **[done]** |
| Merge queue confusion (validating the wrong commit) | Merge group refs must be GitHub's read-only queue refs for the event's base branch and the head commit must match; the scan covers `base_sha..head_sha` and publishes to that exact SHA; each merge group SHA has its own scan; a destroyed group is never revived and its queued scan is cancelled | **[done]** |
| Stale merge group or check result | Check Run ownership per (repository, SHA, check name) with the newest execution winning; a re-run of a commit that is no longer the newest for its pull request or branch is refused and audited; an older scan finishing later changes no violation state | **[done]** |
| Re-run privilege escalation | A re-run only executes a scan: it cannot change policy, rules or repository settings, and it uses the same trust model (policy from the trusted base) and the current effective policy, which is recorded per execution | **[done]** |
| Replayed GitHub events | Delivery IDs are recorded with the payload hash; the same ID with the same payload is a duplicate, with a different payload is refused; one logical scan, notification and audit event result (tested) | **[done]** |
| Lost events after an outage | Event records track processing; a failed or abandoned delivery is processed again when GitHub redelivers, instead of being dropped as a duplicate | **[done]** (needs a redelivery from GitHub) |
| Installation disconnect going unnoticed | Connection transitions are detected against stored state and notified to `github:manage` members; repositories become **AT RISK** rather than "unprotected"; repeated events do not repeat the alert | **[done]** |
| Notification delivery failure hiding a security event | The scan result, violation, check and in-app notification are stored before delivery is attempted; failures only change the delivery record, are retried with backoff and are audited | **[done]** |
| Secrets in notification data | Payload text passes the secret redactor and is bounded; audit events mask e-mail addresses; provider credentials come from the environment or secret files and are registered for log redaction (tested) | **[done]** |

### Dashboard and control plane (Phase 6)

```text
browser ─▶ same-origin cookie session ─▶ CSRF (Origin + token) ─▶ rate limit
        ─▶ route permission ─▶ AccessScope (role ∩ GitHub-reported installations/repositories)
        ─▶ control plane service ─▶ parameterised SQL ─▶ state store
```

| Threat | Mitigation / limitation | Status |
|---|---|---|
| Unauthorized dashboard access | GitHub App user authorization with state bound to the browser and PKCE; every non-sign-in route returns `401` without a valid session (tested for every route) | **[done]** |
| IDOR (guessing scan, violation, repository, installation, policy, member or audit IDs) | Every lookup runs inside the caller's access scope; out-of-scope resources are `404`, indistinguishable from missing ones; writes resolve the target inside the scope before checking the permission (tested per route with two tenants) | **[done]** |
| Cross-tenant data leakage | Tenant = GitHub account; queries filter by installations whose account grants the permission **and** that GitHub listed for the user, and by the repositories GitHub listed; aggregates (overview, counts) use the same scope (tested) | **[done]** |
| Stale GitHub access (user removed from a repository or organization on GitHub) | Access lists are read at sign-in; sessions last at most 8 hours, 2 hours idle; revocable from settings | partially mitigated (up to the session lifetime) |
| Policy privilege escalation | `policies:write` checked on the server per organization; floors can only tighten repository policy; weakening needs confirmation, a reason and a sign-in within 15 minutes; optimistic concurrency; version and audit event written in one transaction (tested: viewer denied, security manager denied, admin audited, stale sign-in, conflict, forced audit failure rolls back) | **[done]** |
| Hiding a violation | No "resolve" or "ignore" action; resolution is computed from scans and GitHub events; acknowledgement does not change enforcement and is audited (tested) | **[done]** |
| Role escalation | Only owners manage members; nobody changes their own role; the last owner cannot be removed; grants use immutable GitHub user IDs; every change audited (tested) | **[done]** |
| XSS through Git metadata (author names, trailers, repository names, evidence) | Stored as text with control characters made visible and secrets redacted; React text rendering only (no raw HTML, enforced by lint rule and test); API responses are JSON with `nosniff`; dashboard CSP allows only same-origin scripts and styles, no `unsafe-inline` or `unsafe-eval` (tested in unit, API and browser tests) | **[done]** |
| CSRF | `SameSite=Lax` session cookie; writes require an allowed `Origin` and an `X-CSRF-Token` derived from the session; JSON-only bodies (tested: missing, wrong, other session's token, foreign and missing Origin, form content type) | **[done]** |
| CORS misuse | No cross-origin access by default; optional explicit allow-list; `*` refused at start-up (tested) | **[done]** |
| Stolen session | `HttpOnly`, `Secure`, `__Host-` cookie; only the SHA-256 is stored; absolute and idle expiry; server-side sign-out and revocation; HSTS in production; HTTPS required in production (tested) | **[done]** (a stolen cookie works until it expires or is revoked) |
| Open redirect after sign-in | `return_to` limited to same-origin application paths (tested) | **[done]** |
| SQL injection, command injection, path traversal | Parameterised SQL built only from constant fragments; allow-listed filters and sort keys; escaped `LIKE`; route parameters matched by strict patterns; static file serving resolves inside the build directory and rejects dot segments; no subprocess in the API (tested) | **[done]** |
| API abuse and large responses | Per-user rate limits by operation class; bounded page sizes (≤ 100); 64 KB request bodies; JSON depth and duplicate-key limits; performance test with 100k audit events | **[done]** |
| Secrets reaching the browser or logs | The browser receives view models only; GitHub user tokens are discarded after sign-in, installation tokens and keys stay server-side; request logs exclude headers, cookies and query strings (tested by inspecting responses, logs and the database) | **[done]** |
| Personal data in findings | Author and committer identities are stored only for commits with findings and removed with retention; commit messages are not stored | **[done]** (documented data inventory) |

### GitHub App (Phase 5)

Security boundaries, each validating its input before passing anything on:

```text
GitHub ─▶ webhook boundary (size, content type, rate limit)
       ─▶ authentication boundary (X-Hub-Signature-256, delivery ID)
       ─▶ event normalisation (typed events; raw JSON stops here)
       ─▶ authorization (installation state; down-scoped token; repository ID lookup)
       ─▶ scan service ─▶ CommitGuard core ─▶ policy engine
       ─▶ GitHub Check (exact scanned SHA, ownership-guarded writes)
```

| Threat | Mitigation / limitation | Status |
|---|---|---|
| Forged webhook | HMAC-SHA256 over the raw body with the webhook secret, constant-time comparison, verified before any parsing; missing, malformed or wrong signatures get `401` (tested: valid, invalid, missing, modified payload, wrong secret, empty, malformed) | **[done]** |
| Replayed webhook | `X-GitHub-Delivery` IDs stored with a payload digest: same ID and payload is ignored, same ID with a different payload is `409`; equivalent events under new IDs map to an existing scan job (tested). Delivery IDs expire with retention; a replay older than that re-runs a deterministic scan of the same SHA | **[done]** |
| Duplicate webhook | Idempotent job keys and one Check Run per repository, SHA and check name (tested: 5 deliveries give one scan and one run; 12 concurrent deliveries give one job) | **[done]** |
| Unauthorized repository access (spoofed installation, owner or repository in a payload) | Every scan mints a token down-scoped to that single repository ID; GitHub refuses when the installation does not cover it; the repository is then looked up by immutable ID with that token. Stored state rejects deleted or suspended installations early. Names are never used for authorization or paths (tested: repository outside the installation, spoofed installation ID, removed repository, uninstalled App) | **[done]** |
| Stolen installation token | Tokens live about 1 hour, are limited to one repository and to Checks write plus read-only Contents, Metadata and Pull requests, are kept only in memory, are dropped on uninstall or removal, and are never logged | **[done]** (short lifetime is GitHub's) |
| Stolen private key or webhook secret | Read from files or the environment, wrapped in `Secret`, registered for redaction, never logged or stored, key file permissions checked by `validate`. A stolen key lets the holder act as the App: rotate it in GitHub | partially mitigated (operational) |
| Secrets leaking into logs, errors, Checks or responses | Redaction of registered secrets and credential-shaped strings in logs, errors and stored text; generic HTTP errors; test forces credential-echoing failures and inspects every output surface | **[done]** |
| Malicious repository metadata (names, branches, PR text, commit messages, trailers) | Names validated against GitHub's formats; PR titles and bodies are never read; commit data only reaches detectors and escaped Markdown; no shell (tested with `$(touch …)`, backticks, `;`, `\|`, `&&`, `../../`) | **[done]** |
| Repository code execution | No checkout, no work tree, no hooks (`--template=`, null hooks path), no submodules, no builds or installs; only commit, tree and config-blob objects are fetched | **[done]** |
| Malicious Git server response or redirect | Protocol allow-list (HTTPS only in production), no redirects, no credential helpers, fetch timeout; token only in a header scoped to the remote URL | **[done]** (Git client bugs remain an upstream risk) |
| PR policy tampering | Trusted base or before policy (Phase 4 code); a weakening is reported as "Security policy modification detected" and audited (tested) | **[done]** |
| Rule tampering | Rules only from the installed package (tested with rule files in the PR) | **[done]** |
| Repository weakens organisation requirements | Optional mandatory policy applied after trusted config; can only tighten; `enabled: false` rejected (tested). Organization-hosted policy was added in Phase 8 (see above) | **[done]** |
| Stale scan overwrites newer result | Sequenced jobs; per-SHA check ownership with guarded writes; superseded PR scans cancelled (tested: B before A, A mid-flight while B completes) | **[done]** |
| Result attached to the wrong commit (TOCTOU) | Check Runs are created with the SHA that is fetched and scanned; the planned range head is verified before publishing; GitHub's returned head SHA is checked | **[done]** |
| GitHub API outage, errors or rate limits produce a false PASS | Bounded retries (3 attempts), bounded rate-limit waits; every failure publishes `failure` or `timed_out` when a check exists, otherwise no check (a required check stays unsatisfied) (tested: 5xx, timeouts, persistent 429, permission revoked mid-scan) | **[done]** |
| Reduced App permissions silently pass | Token requests ask for the required permissions and verify the granted ones; missing permissions stop the scan without publishing success (tested) | **[done]** |
| Denial of service | Request body limit (25 MB, checked from `Content-Length` before reading), JSON depth limit, duplicate-key rejection, per-client rate limit, bounded queue with durable recovery, commit limit per scan, fetch and Git timeouts, pagination page limit, output caps (20 findings, 60,000 characters) | **[done]** (per-message size limit: not implemented; normalisation of large messages was made faster on 2026-09-17, finding #3) |
| SSRF | No URLs are taken from payloads: the API base and Git host are fixed, paths are built from validated segments, pagination links must stay on the API host, only the HTTPS handler is installed | **[done]** |
| Cross-tenant data access | All storage keyed by installation and repository IDs; tenant-scoped listing APIs; mirrors under numeric installation and repository directories; the dashboard API adds per-session access scopes (see Phase 6) | **[done]** |
| Plain-HTTP interception of webhooks | The service binds to localhost; TLS is required at the reverse proxy (documented). GitHub requires HTTPS webhook URLs for signature verification to be meaningful | limitation documented |
| Check exists but merges are not blocked | Branch protection or rulesets must require `commitguard-app`; not configured or verified by CommitGuard | limitation documented |

### GitHub server-side enforcement (Phase 4)

| Threat | Mitigation / limitation | Status |
|---|---|---|
| Developer bypasses local hooks | GitHub Actions check analyses every commit the pull request / push introduces with the same engine | **[done]** |
| Developer modifies their local CommitGuard installation or rules | CI installs CommitGuard from the pinned Action commit (or, in this repository, a trusted commit) and uses its bundled rules | **[done]** |
| Pull request relaxes `.commitguard.yaml` to approve itself | Policy is read from the trusted base commit's tree; the change is reported as a notice and applies only after merge (tested) | **[done]** |
| Pull request edits `rules/*.yaml` to weaken detection | Repository rule files are never read; rules come from the installed package (tested) | **[done]** |
| Pull request edits the workflow that runs the check (e.g. `exit 0`) | **Limitation of GitHub's `pull_request` model.** Mitigate with CODEOWNERS review for `.github/workflows/` or organisation rulesets requiring a workflow from another repository; documented, not enforceable by CommitGuard | limitation documented |
| Malicious commit message, author, branch or trailer injects shell or workflow commands | No shell interpolation (env-only inputs, argument vectors); untrusted log text printed inside `::stop-commands::<random>`; annotations escaped; job summary Markdown/HTML-escaped; step outputs are enums/integers (tested with `$(touch /tmp/commitguard-pwned)`, `::set-output`, `::error::`) | **[done]** |
| Malicious YAML in trusted policy | Strict safe loader; object tags, aliases, duplicate keys rejected; failure fails the check (tested) | **[done]** |
| Malformed or spoofed event payload | Strict normalisation: validated SHAs, control-character checks, consistency checks (`deleted` vs `after`), unsupported events and `pull_request_target` fail | **[done]** |
| Fork pull request steals secrets or writes to the repository | `pull_request` only, `contents: read`, no secrets used, `persist-credentials: false`; no repository-controlled code executed | **[done]** |
| Third-party GitHub Action compromises CI | Actions pinned to verified commit SHAs (enforced by tests); minimal permissions | **[done]** |
| Dependency tampering or confusion during install | `--require-hashes` lock (`requirements/ci.txt`), `--no-build-isolation`, CommitGuard installed from source by path, never by index name | **[done]** for the Action and `commitguard.yml`. In this repository's `commitguard.yml`, a trusted commit without `requirements/ci.txt` or the CI command falls back to installing from the checked-out change (bootstrap path; see [ci-pipeline-security.md](ci-pipeline-security.md)) |
| CommitGuard cannot evaluate (bad config, missing commits, Git missing, import failure) and CI passes | Every error path exits non-zero and prints `Result: FAILED`; shallow clones fail with an actionable message (tested) | **[done]** |
| Direct push bypasses pull request validation | Push-triggered check detects it **after** the commits reach GitHub. Prevention requires protected branches, required pull requests and the required `commitguard` check | limitation documented |
| Cancelled push runs leave commits unchecked | No `cancel-in-progress` in shipped enforcement workflows; `doctor` warns about it | **[done]** |
| Branch protection not actually configured | CommitGuard cannot verify it locally; `doctor` and `github setup` say so explicitly | limitation documented |
| Squash/rebase merge message edited at merge time | Detected by the post-merge push check (after landing) | limitation documented |

### Local Git operations (Phase 3)

| Threat | Mitigation / limitation | Status |
|---|---|---|
| Developer **accidentally** commits or pushes an AI-attributed commit | pre-commit and commit-msg hooks block the commit; pre-push blocks every outgoing violating commit, including ones created with `--no-verify`, merges, rebases and tools that skip commit hooks | **[done]** |
| Developer **intentionally** bypasses local hooks (`--no-verify`, deleting hooks, `core.hooksPath`, editing `.commitguard.yaml`, another clone) | **Limitation:** local hooks are user-controlled; CommitGuard does not fight Git's bypass mechanism (tests assert it works). `commitguard doctor` makes these states visible. **Mitigation:** GitHub-side check | limitation documented; mitigation **[done, Phase 4]** (requires branch protection) |
| Malicious commit metadata or ref names attempt command injection (`$(…)`, backticks, `;`, `&&`, `\|`, quotes, Unicode) | Git metadata and pre-push input are untrusted data: no shell anywhere, argument vectors or stdin only, object IDs validated before use, ref names never interpolated; tests with hostile messages and branch names assert no execution | **[done]** |
| Existing hooks are destroyed by installation | Foreign hooks are renamed to `<hook>.pre-commitguard` and chained (never overwritten); conflicts refuse; uninstall removes only the managed block and restores the original; tests compare bytes | **[done]** |
| Repository content hijacks the hook (a `commitguard/` package in the work tree) | Hooks run `python -P -m commitguard`, so the current directory is not on `sys.path`; tested | **[done]** |
| CommitGuard unavailable (venv removed, not on PATH) silently allows operations | Wrapper falls back to `commitguard` on PATH, otherwise blocks with instructions (exit 2) | **[done]** (the `PATH` fallback does not check which project provides `commitguard`; see S-L-10) |
| Invalid configuration or internal errors allow operations | Hook commands map every exception to exit 2 (blocks Git) with "security check could not be completed"; not configurable | **[done]** |
| Malformed pre-push input | Strict parsing (four fields, valid object IDs, no control characters, consistent deletions); anything else blocks | **[done]** |
| Hook script tampering | Managed block checksum; `doctor` reports modifications, `install` repairs on request | **[done]** (detection, not prevention) |
| Shared `core.hooksPath` modified for all repositories | Install refuses hooks directories outside the repository's Git directory unless explicitly allowed; `--global` uses `init.templateDir` only when unset, never `core.hooksPath` | **[done]** |
| Huge pushes exhaust resources or are silently truncated | Only outgoing commits analysed, deduplicated, bounded by `max_push_commits`; exceeding it blocks | **[done]** |
| Stale remote-tracking refs exclude commits the remote no longer has | Accepted: those commits were checked when pushed; exclusion never uses unknown data | limitation documented |
| commit-msg cannot see `--cleanup` or the final commit object | Documented best effort; pre-push analyses real commit objects | limitation documented |

### Configuration, rules and crafted metadata (Phases 1 and 2)

**Weakening policy through configuration**

- A pull request adds `action: allow`: CI and the App read policy from the
  trusted base commit **[done, Phase 4]** (v1 listed this as planned).
- Typos or ambiguity silently disabling a policy: strict schema, unknown keys
  and policy IDs rejected, strict booleans, no nulls **[done]**.
- Duplicate YAML keys overriding earlier values: rejected **[done]**.
- Omitted policies: fall back to secure defaults, never to "off" **[done]**.

**Code execution via configuration or data**

- YAML object construction: strict `SafeLoader` subclass only (architecture
  test `test_yaml_is_only_loaded_through_the_strict_safe_loader`) **[done]**.
- Plugins or commands named in configuration: not supported by design;
  explicit in-code detector registry **[done]**.
- ReDoS via regex rule data: rules are literal values; the parser and matcher
  use no regular expressions **[done]**.
- Malicious rule files: strict schema, cross-validation, strict safe YAML
  **[done]**.

**Crafted commit metadata**

- **Terminal escape injection** (hide a trailer, spoof output): all untrusted
  text is sanitised before display; ESC, C0/C1 and bidi controls made visible
  **[done]**.
- **Option injection** via revisions (`--output=…`): revisions validated (no
  leading `-`, no control characters) and passed after `--end-of-options`
  **[done]**.
- **Shell injection**: no shell anywhere; argument vectors only **[done]**.
- **Parser confusion** (NULs, malformed dates/SHAs): NUL-delimited Git output
  with the free-form message last; every structured field validated; mismatch
  raises instead of guessing **[done]**.
- **`git replace` objects** substituting an innocent commit:
  `GIT_NO_REPLACE_OBJECTS=1` **[done]**.
- **`.mailmap` rewriting an AI identity** into a human one:
  `--no-use-mailmap` **[done]**.
- **Evasion by formatting**: key casing/spacing/underscores, missing colon,
  zero-width and bidi characters, fullwidth/mathematical letters,
  Cyrillic/Greek look-alikes, Unicode line separators, indented trailers,
  trailers outside the trailer block, symbols or punctuation before the key
  (finding #1): normalised and still detected **[done]**. Non-ASCII letters or
  numbers before the key (finding #5): fix in development.
- **Trailer flood** (hide attribution after thousands of trailers): parsing is
  bounded (`MAX_TRAILERS = 1000`) and exceeding the limit fails closed (BLOCK)
  **[done]**.
- **False positives** (humans named like an agent, employees at vendor
  domains): exact matching only, vendor domains never sufficient alone,
  ambiguous names need corroboration **[done]**; single-token alias names
  remain a documented medium-confidence risk.
- **Resource exhaustion**: configuration, rule and message files
  size-limited; linear-time parsing; `--max-commits` refuses oversized ranges
  **[done]**; bounded Git output capture **[planned]**.
- **Record-splitting in batched Git output**: records separated by a random
  per-call boundary and cross-checked against requested SHAs **[done]**.

**Detector failure**

- Any detector exception or invalid output → BLOCK (fail closed) **[done]**.
- Unexpected CLI errors exit 2 (never 1 = "blocked", never 0) without
  tracebacks **[done]**.

**Information disclosure**

The GitHub App stores IDs, repository names, SHAs, states, counts, rule IDs,
finding evidence and, for commits with findings, author and committer
identities for a bounded retention period (default 30 days); open violations
are kept while they are open, and organization policy versions are kept to
explain historical scans. Mirrors hold commit and tree objects (file names,
not file contents) of repositories while they are installed. See
[github-app.md](../github-app.md#operational-notes).

- No network access and no AI/LLM APIs in the local tool; no telemetry;
  enforced by architecture tests **[done]**.
- Reports and JSON contain concise metadata evidence only, never file contents
  or full messages **[done]**.
- Tracebacks never render local variables
  (`pretty_exceptions_show_locals=False`) **[done]**.
- Environment variables are never logged **[done — nothing logs them]**.
- GitHub Actions enforcement uses no token and no secrets **[done]**. The
  GitHub App keeps installation tokens and JWTs in memory only, redacts them
  from every output and never persists them **[done]**.

**Repository modification**

- Detectors receive data, not a repository handle; architecture tests forbid
  I/O imports in detection layers **[done]**.
- Git reads use `GIT_OPTIONAL_LOCKS=0` **[done]**.
- CommitGuard never rewrites commits or history; it never runs `git reset`,
  `rebase`, `commit --amend`, `filter-branch` or `filter-repo`. Remediation
  text explains the scope of any command it suggests **[by design]**.
- `init` never overwrites an existing file **[done]**.

## Maintaining this document

Update the version history and the affected sections when:

- a security finding is fixed (add it to the findings table here and in
  [vulnerability-response.md](vulnerability-response.md));
- a new deployment mode, input, credential or external service is added;
- a trust assumption changes (for example branch protection is enabled on
  this repository, or a distribution name is chosen);
- a mitigation's evidence moves (renamed or deleted tests).
