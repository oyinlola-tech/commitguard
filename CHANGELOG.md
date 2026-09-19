# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.1] - 2026-09-19

### Added

- **`remediation.fix_on_push`** (opt-in, off by default): the `pre-push` hook
  removes AI, bot and agent attribution from *unpushed* commits that never
  passed through `commit-msg` - made with `git commit --no-verify`,
  `git cherry-pick`, `git rebase`, `git am`, or tools that skip hooks - then
  stops that push so the next `git push` sends the cleaned commits (a hook
  cannot change what a push in progress sends). Only the message changes: the
  tree, author, committer and timestamps are copied byte for byte, and the
  originals stay in the reflog. It refuses, and blocks as before, for any
  commit a remote-tracking branch contains, tags, signed commits, identity
  findings, a message that would be left empty, or an unfinished rebase, merge,
  cherry-pick, revert or bisect; if any commit cannot be fixed, none are
  rewritten; and nothing moves unless every rewritten commit re-analyses clean.
  Branches move atomically with a compare-and-swap. `commitguard doctor` warns
  while it is on, and a mandatory policy cannot enable it. Each of these guards
  is covered by a test that fails when the guard is removed.

### Changed

- The blocked-push message no longer says "CommitGuard never modifies
  commits", which is not true when `fix_on_push` is enabled. It now says the
  commits were not changed and points to the setting.

## [0.1.0] - 2026-09-18

### Packaging

- **Published to PyPI as `commitguardian`** (`pipx install commitguardian`). The
  product, import package and console script are all still `commitguard`; the
  distribution name differs because `commitguard` and `commitguard-cli` on PyPI
  belong to two unrelated projects by other authors. See
  [ADR-009](docs/adr/009-published-to-pypi-as-commitguardian.md), which
  supersedes ADR-008.
- Releases are published with **PyPI Trusted Publishing** (OIDC from the tagged
  release workflow). No API token is created, stored or rotated.
- **The sdist is now built from an allowlist.** The default configuration
  produced an 8.4 MB sdist containing an editor's scratch worktree under
  `.kilo/` (a second copy of the repository), the `.hypothesis/` cache and 22 MB
  of generated benchmark datasets. It is now 988 KB of source, tests, docs and
  metadata, and the release workflow fails if a local directory reappears.
- Version `0.1.0.dev0` -> `0.1.0`, so `pip install commitguardian` resolves it
  without `--pre`.
- Added `project.urls` (homepage, repository, documentation, changelog, issues)
  for the PyPI project page.
- **Releases publish automatically.** A final tag `vX.Y.Z` now validates,
  builds, smoke-tests on Linux, macOS and Windows, publishes the GitHub release
  and publishes to PyPI with no further action. A release candidate (`rcN`)
  stops at a draft prerelease and is never published to PyPI.
- The GitHub Marketplace listing stays manual, because GitHub provides no API
  for it: `gh release create` has no marketplace flag and the release object has
  no marketplace field. The release job instead **fails** if `action.yml` or the
  README would stop the listing checkbox appearing, and writes the link and the
  exact category values into the workflow run summary.

### Added

- **`remediation.auto_remove`** (opt-in, off by default): the `commit-msg` hook
  deletes prohibited AI, bot or agent attribution from the pending commit
  message instead of refusing the commit, reports every line it removed, and
  lets the commit through. It rewrites no history - `commit-msg` runs before Git
  creates the commit object. It refuses whatever it cannot provably fix:
  attribution in the author or committer identity, a message that would be left
  empty, or a detector failure. The stripped message is re-analysed and the
  commit proceeds only if it is then clean. `pre-push` never removes anything,
  and a mandatory organization policy cannot switch it on.
  See [docs/configuration.md](docs/configuration.md#remediation).

### Fixed (Phase 10 verification pass)

- The Git hook's fail-closed message escaped newlines, so a multi-line reason
  (an invalid `.commitguard.yaml` listing several fields) printed as one
  unreadable `\x0a` run. Reason blocks now keep their newlines and indent every
  continuation line, so text from a configuration file cannot start a line at
  column 0 and impersonate a CommitGuard status line.
- GitHub App state: `installation_repositories`, `known_repositories` and
  `installation_sync_status` were never deleted when an installation was purged,
  so an organization's repository names stayed in the database indefinitely,
  contradicting the documented retention behaviour.
- The repository's own enforcement workflow fell back to installing CommitGuard
  from `$GITHUB_WORKSPACE` - the change under review - when the trusted commit
  predated CI support. The check now fails instead.
- `.env.example` claimed CommitGuard reads no environment variables; it reads 30.
  It now lists them all, and a test keeps it in sync with the source.
- Removed pictographic characters from CommitGuard's own output (Check Run
  markdown, GitHub Actions summaries, CLI status lines) and documentation prose.
  They remain only in detection *inputs* (the adversarial dataset and fixtures).

### Security (Phase 10 — three detection bypasses and two ReDoS defects, found by this project's own testing)

- **Detection bypass**: a Unicode letter or number before a trailer key
  (`\u32acCo-authored-by:`, `\u2460Co-authored-by:`) hid AI attribution. The
  leading-character rule used Unicode-aware `isalnum()` and ran after NFKC, so
  such characters joined the key. Trailer keys are ASCII, so the prefix is now
  whatever precedes the first ASCII letter or digit, judged both before and after
  NFKC, with the shortest reading winning; Latin look-alikes remain part of the
  key. Found by property-based fuzzing; dataset 1.2.0; regression tests in
  `tests/unit/provenance/test_trailers.py`.
- **Detection bypass**: Unicode default-ignorable code points that are neither
  control nor format characters (variation selectors, U+034F, Hangul fillers)
  hid a trailer key or an agent alias — `Co-authored\ufe0f-by:` and
  `Clau\u034fde Code` were allowed. `normalize_text` now removes every
  default-ignorable code point (checked against the Unicode 18.0.0 data file).
  Found by property-based fuzzing; dataset 1.3.0.
- **ReDoS**: the JWT secret-redaction pattern was quadratic (4.6 s on 80 KB of
  `eyJ-eyJ-…`), and redaction runs on untrusted text before truncation. Replaced
  with a linear scan; equivalence checked over 300,000 random strings.
- **ReDoS**: the workflow `secrets.` check was quadratic (2.7 s on 60 KB of
  `${{${{…`), and the App inspects workflow files fetched from monitored
  repositories. Replaced with two linear steps.
- **Notification delivery**: `commitguard github serve` never loaded the
  notification settings, so e-mail and webhook notifications were silently never
  delivered. Both entry points now build the service through one factory.
- **Documentation-driven dependency confusion**: several pages instructed
  `pip install 'commitguard[app]'`, which resolves to an unrelated PyPI project.
  Corrected everywhere; a test now fails if any documentation reintroduces it.

### Added (Phase 10 — external adoption, reproducibility and open source readiness)

- `commitguard reproduce all|security|benchmark|integration|github`: re-runs the
  published evidence with PASS / FAIL / SKIPPED / NOT RUN statuses. A skipped step
  is never reported as a pass.
- `commitguard report security`: assembles `reports/security-report.{json,md}`
  and `benchmark-report.md` from recorded results and test evidence, labelling
  every statement Measured, Tested, Observed, Expected or Not tested.
- Security test suite (`tests/security/`): property-based fuzzing of every parser
  that handles untrusted input, a ReDoS suite covering all 33 regular expressions
  in the source, and invariant tests (determinism, detector-order independence,
  fail-closed behaviour, the mandatory-policy floor). 350 tests now carry the
  `security` marker; `hypothesis` added to the development dependencies.
- Dataset versions 1.2.0 (9,157 cases) and 1.3.0 (9,174 cases), written before the
  runs that exposed the corresponding bypasses.
- `examples/`: basic, github-actions, github-app, organization-policy, exceptions
  and policy-rollout, each with expected results — verified by
  `tests/integration/test_examples.py` against the real engine.
- `commitguard doctor`: PASS / WARNING / FAIL / NOT CONFIGURED / INFO statuses,
  runtime dependency and GitHub App configuration checks, and `--json`.
- `commitguard init`: interactive setup when run by a person, `--install-hooks`,
  and `--non-interactive` for scripts and CI.
- Release engineering: `.github/workflows/release.yml` (validation gate, one
  build, install and smoke test on three operating systems, `SHA256SUMS`, SBOM,
  draft release) and CI jobs for the security suite, cross-platform validation and
  package installation.
- Documentation: `docs/getting-started/quickstart.md`, `docs/cli/`, `docs/api/`,
  `docs/deployment/`, `docs/operations/runbook.md`, `docs/security/` (threat
  model, boundaries, authorization matrix, review guide, vulnerability and
  incident response, supply chain, CI pipeline review), `docs/research/` (18
  documents), `docs/adr/` (8 records), `docs/maintainers/`, `docs/community/`,
  `docs/support/`, `docs/evidence/`, `evidence/`, `award-evidence/`, `ROADMAP.md`,
  `CITATION.cff`, issue forms, pull request template and `CODEOWNERS`.

### Changed (Phase 10)

- `SECURITY.md`: response targets, disclosure policy, safe harbour and the list of
  fixes to date.
- The workflow policy test now allows write permissions only in a job of a
  workflow that no pull request can trigger, instead of banning them outright.
- README: an installation section (with the PyPI name-collision warning), the
  reproduction commands and the new documentation index.

### Added (Phase 8 — organization governance, central policy and enterprise security)

- Organization governance package (`commitguard.governance`) with 65 `/api/v1`
  routes (`commitguard.api.governance`), documented in `docs/dashboard.md`.
- Permissions `organization:read/manage`, `security:read/manage`,
  `exceptions:read/create/approve/revoke`, `policies:publish/approve/emergency`
  and `rules:manage`, assigned to the existing roles.
- Organization security settings (versioned, optimistic concurrency): security
  baseline, policy approval and separation of duties, exception limits and
  approval threshold, onboarding defaults, rollout thresholds, alert
  aggregation, time zone. Relaxing a control needs confirmation, a reason and a
  recent sign-in, and is audited and notified.
- Repository inventory with separate dimensions (connection, archived,
  onboarding, mode, enforcement, policy propagation); discovery from
  installation events and synchronisation; `enforce` and `monitor` modes;
  installation synchronisation health.
- Repository groups (archived, never deleted) and background bulk operations
  (group membership, onboarding, mode, monitoring, scans) that are bounded,
  idempotent, retryable, cancellable and audited.
- Policy targets for organization, repository groups and repositories, with
  **mandatory** (floor) and **default** entries; pure per-rule resolver
  (`commitguard.policies.governance`) with provenance, conflicts, exceptions and
  monitor mode; effective policy cache with same-transaction invalidation,
  background propagation and propagation status; governance versions and
  provenance recorded with every scan.
- Policy workflow: drafts, submission, approval bound to the document
  fingerprint, rejection, publication against the base version, emergency
  publication (owners, critical audit and notification).
- Read-only policy simulation of drafts against recorded scans and findings
  with the real policy evaluator, queued with leases and bounded.
- Staged policy rollouts (pilot repositories, cumulative percentages),
  pause/resume, automatic pause on error or block thresholds, optional
  automatic rollback through the Phase 7 rollback path.
- Policy exceptions scoped to a repository, group or organization, lowering one
  rule to `warn` or `allow` until expiry; approval rules, separation of duties,
  revocation, expiry worker, expiry warnings and notifications.
- Organization rules: versioned identity data (AI agents and bots) compiled into
  the trusted rule set without patterns or code; rules version recorded per scan.
- Scheduled scans of default branches (daily/weekly, time zones) through the
  normal worker, skipping unchanged heads, with per-run records.
- Security posture with explicit states and reasons, compliance fraction,
  repository security matrix with drift, trends from history and daily
  snapshots, security events with acknowledgement, organization search,
  JSON/CSV compliance reports that state they are not certifications.
- Notification types `violation_digest`, `policy_approval_requested`,
  `policy_emergency_published`, `policy_rollout_failed`,
  `policy_propagation_failed`, `exception_requested`, `exception_approved`,
  `exception_expiring`, `exception_ended`, `repository_unprotected`,
  `organization_settings_changed`; optional hourly violation digests for
  e-mail and webhooks.
- Dashboard pages under `/organization` and `/settings/organization`.
- Documentation: organization governance, policy inheritance, simulation,
  exceptions, rollouts, repository management, security posture and compliance
  reporting; architecture, threat model, deployment, policy management,
  notifications and dashboard updated.
- Tests: resolver and governance unit tests; integration tests for policies,
  workflow, rollouts, operations, cross-tenant and role sweeps over every
  governance route, a 39-step end-to-end scenario and performance budgets with
  1,000 repositories; `scripts/benchmark_governance.py` for 10,000 repositories,
  100,000 scans and 1,000,000 findings.

### Changed (Phase 8)

- Database schema 4 (automatic migration; existing repositories become
  `onboarded` in `enforce` mode, so enforcement is unchanged).
- Audit events are immutable (update trigger); retention purging also removes
  finished simulations, bulk operations and scheduled scan runs.
- Removing a member's last membership ends their sessions immediately.
- With `require_policy_approval`, the direct organization policy save returns
  `APPROVAL_REQUIRED`.
- Policy drafts can be rebased on the current version, accept a publication
  reason, link the rollout they started, keep the diff they published, and
  expose `can_cancel`/`can_emergency_publish`; relaxed settings are listed in
  `error.details.changes`; security events reference their resource.
- `policy_source` lists the governance layers applied (for example
  `+ group Backend policy v2 + 1 exception(s)`).

### Added (Phase 7 — notifications, merge queue, check re-runs and policy recovery)

- Notification subsystem (`commitguard.notifications`): typed notification
  events written to a transactional outbox in the same database transaction as
  the change that caused them; a dispatcher that fans out to per-user in-app
  inbox rows and e-mail/webhook delivery records; a delivery worker with leases,
  bounded retries (1 m, 5 m, 30 m, 2 h; 5 attempts), idempotency keys and
  delivery audit events.
- Notification types: `critical_violation`, `high_violation` (newly blocked
  violations, one per rule per pull request/branch/merge group per hour),
  `policy_changed`, `policy_rolled_back`, `installation_disconnected`,
  `installation_reconnected` (state transitions only), `merge_queue_failure`,
  `check_rerun_failed`.
- Channels: in-app notification center (`/notifications`, bell with bounded
  unread counts), SMTP e-mail (`EmailProvider` interface, plain text), and
  HMAC-SHA256 signed webhooks with timestamps, per-endpoint derived secrets
  shown once, public-address checks with pinned connections, and no redirects.
  `COMMITGUARD_NOTIFICATIONS_MODE=test` records deliveries without sending.
- Preferences: versioned organization settings (`notifications:manage`,
  confirmation to turn deliveries off), organization e-mail recipients and
  webhooks, personal in-app mutes for non-mandatory types; delivery log.
- GitHub App merge queue support: `merge_group` `checks_requested` scans
  `base_sha..head_sha` and publishes `commitguard-app` on the merge group
  commit; `destroyed` cancels queued scans and ends exposures; refs and head
  commit validated; out-of-order and duplicate events handled; merge queue
  status (`enabled`/`not_enabled`/`unknown`) from rulesets; repository merge
  queue view and `GET /api/v1/repositories/{id}/merge-queue`.
- Check re-runs: `check_run` and `check_suite` `rerequested` create a new
  execution of the stored scan (matched by `external_id`, installation,
  repository, SHA and check name; other Apps ignored); stale re-runs of
  outdated commits refused and audited; duplicates collapse.
- Scan executions: `scan_key`, execution number, trigger (`push`,
  `pull_request`, `merge_group`, `manual`, `rerun`, `retry`) and previous
  execution on every job; `GET /api/v1/scans/{id}/executions`; scan detail
  shows execution history and flags differing policy or rules versions.
- Organization policy rollback (`POST /api/v1/policies/{id}/rollback`,
  `policies:rollback`): a new immutable version restoring an earlier document,
  with reason, confirmation, recent sign-in for weakening rollbacks, optimistic
  concurrency, integrity check, audit event and notification in one
  transaction; `GET /api/v1/policies/{id}/diff`; version history with status,
  lineage, change summaries, compare and rollback dialog in the dashboard.
- Event records: webhook deliveries store provider, action, processing status,
  attempts and tenant IDs; a failed or abandoned delivery is processed again
  on redelivery.
- Recovery service: marks abandoned event processing failed and schedules up
  to two automatic retry executions for infrastructure failures; jobs that
  exhaust their attempts are audited and fail their check.
- Metrics: `github_events_received`, `github_events_failed`,
  `github_events_replayed`, `check_reruns`, `scan_retries`,
  `merge_groups_scanned`, `merge_groups_failed`, `policy_rollbacks`,
  `policy_rollback_failures`, `notifications_created`, `notifications_sent`,
  `notifications_failed`, `notification_retries`.
- Audit events for scan starts and retries, re-run requests and rejections,
  merge groups, policy rollbacks, notification creation, delivery, failure,
  reads, settings, preferences and webhooks; audit events carry the API
  `request_id`.
- Documentation: `docs/notifications.md`, `docs/merge-queue.md`,
  `docs/policy-management.md`, `docs/recovery.md`.
- Tests: notification units, event normalisation, storage and migration,
  merge queue and re-run integration, notification and recovery API tests, the
  Phase 7 lifecycle scenario, Phase 7 performance volumes (100,000
  notifications, 100,000 event records, 10,000 policy versions), frontend
  tests and a browser scenario.

### Changed (Phase 7)

- State database schema 3, migrated automatically: scan executions (existing
  re-scans backfilled as manual executions), event processing status, merge
  groups, policy version kind and rollback lineage with triggers that refuse
  updates and deletes of published versions, notification tables.
- Repositories whose GitHub App installation is suspended or removed, or that
  are no longer granted, are `at_risk` instead of `unprotected`; the overview
  warns that GitHub enforcement is at risk.
- Superseded scans are shown with the result `stale` instead of `cancelled`.
- Dashboard **Scan again** creates a new execution of the same scan and is
  refused while one is already queued or running.
- A late `suspend`, `unsuspend` or `new_permissions_accepted` event no longer
  revives a deleted installation.
- The optional `merge_queues: read` permission and `check_run`, `check_suite`
  and `merge_group` subscriptions are reported by `commitguard github validate`
  as warnings when missing; installation tokens remain down-scoped to the
  required permissions.
- Policy page success messages survive the editor remounting after a save.

### Added (Phase 6 — security dashboard and control plane)

- Web dashboard (`web/`, React 19 + TypeScript + Vite) served by the App
  service from the same origin: landing page, sign-in, overview, repositories
  and repository detail, scans and scan detail, violations and violation
  detail, policies, rules and rule detail, audit log, GitHub installations and
  installation detail, settings, and a 404 page. Loading, empty, success and
  error states on every page; responsive layouts with a navigation drawer and
  stacked table cards; light and dark themes; WCAG 2.2 AA checks.
- Dashboard API `/api/v1` (framework-free WSGI, `commitguard.api`):
  consistent `data`/`meta` and `error` envelopes, cursor pagination,
  server-side filtering, allow-listed sorting and search, security headers,
  CORS allow-list, per-user rate limits, request IDs and structured request logs.
- Sign-in with the GitHub App's user authorization (state bound to the
  browser, PKCE); server-side sessions with hashed tokens, absolute and idle
  expiry, revocation; session-derived CSRF tokens; re-authentication for
  weakening changes.
- Roles (viewer, security manager, admin, owner) with explicit permissions;
  tenant isolation by GitHub account plus the installations and repositories
  GitHub reports for the user; `commitguard dashboard members list|grant|revoke`.
- Control plane (`commitguard.controlplane`): scan result recording with
  findings, evidence and commit identities; violation lifecycle (open,
  acknowledged, resolved) driven by pull request and branch exposures;
  versioned organization policy floors with optimistic concurrency, applied to
  App scans through the mandatory-policy floor; reproducibility metadata per
  scan (organization policy version, effective policy, rules and CommitGuard
  versions); scan comparison; re-scans; repository monitoring pause/resume;
  enforcement evidence (Actions workflow detection, required check from
  rulesets and branch protection); installation repository sync; rule catalogue.
- Audit events with actors and organization scope for sign-in, sessions,
  members, policy changes, violation lifecycle, monitoring, sync, enforcement
  checks and re-scans; `SECURITY_ALERT_TYPES` for future notifications.
- Tests: two-tenant authorization and IDOR tests for every route, security
  tests (CSRF, CORS, injection, XSS, traversal, secrets in responses and logs,
  rate limits, headers), policy, lifecycle and end-to-end API tests, a
  performance test (100 repositories, 10k scans, 50k findings, 100k audit
  events), Vitest component and page tests, and Playwright browser tests
  (primary scenario, responsive, axe accessibility, CSP, cookies).
- Docs: `dashboard.md`; deployment, architecture, GitHub App, GitHub
  enforcement, configuration, threat model, README and SECURITY updated.

### Changed (Phase 6)

- The GitHub Action is listed as **CommitGuard AI Attribution Check** (the
  name "CommitGuard" is taken on GitHub Marketplace) and declares Marketplace
  branding (`shield`, `gray-dark`). The `uses:` reference is unchanged.
- State database schema 2 with ordered, transactional migrations; Phase 5
  databases are upgraded in place.
- The worker records results through `ScanResultRecorder` and evaluates each
  installation with its organization's policy floor.
- `CIRun` and `ScanResult` carry the effective policies; `Repository.is_ancestor`.
- Audit events gain `actor_type`, `actor_id`, `actor_login` and `account_id`;
  audit storage fills the account from the installation.
- Findings now store the author and committer of commits with findings
  (previously no identities were stored); commit messages are still never stored.
- `RequestRateLimiter` moved to `commitguard.security.rate_limit`.
- `commitguard github serve` and `wsgi_app_from_environment` serve the dashboard
  when `COMMITGUARD_DASHBOARD_URL` is set.

### Added (Phase 5 — GitHub App and centralised enforcement)

- GitHub App service (`commitguard github serve`, optional `app` extra):
  - WSGI webhook endpoint `POST /webhooks/github` with `GET /health` and `GET /ready`;
  - HMAC-SHA256 signature verification, delivery-ID replay protection, size,
    content-type, JSON (duplicate keys, depth) and per-client rate limits;
  - typed events for `installation`, `installation_repositories`, `push` and `pull_request`.
- App authentication: RS256 JWTs from the App ID and private key (fails closed
  on bad keys); installation tokens down-scoped to one repository and least
  privilege, cached in memory only.
- Small GitHub REST client:
  - HTTPS only, no redirects, same-origin pagination;
  - normalised errors (401/403/404/409/422/429/5xx/timeouts);
  - bounded retries and rate-limit backoff.
- Installation lifecycle and authorization: install, uninstall, suspend,
  repositories added and removed, and authorization by repository ID before
  every scan.
- Metadata-only partial Git mirrors: no checkout, hooks or blobs, and no lazy
  fetch during analysis. Requires Git 2.45+.
- `ScanService` (`ScanRequest` → `ScanResult` with statistics and a
  deterministic scan ID) and `EnforcementService` (exit codes and check
  conclusions). `commitguard ci github` now uses them.
- Check Runs `commitguard-app` (pull requests) and `commitguard-app/push`:
  - queued → in_progress → completed lifecycle, with bounded Markdown output;
  - ownership-guarded writes, so a stale scan never overwrites a newer result;
  - the exact scanned SHA, verified before publishing.
- SQLite state store for deliveries, installations, jobs, check ownership and
  audit events: tenant-scoped reads and configurable retention.
- Event queue abstraction with an in-process implementation, durable job recovery.
- Audit events (`audit.models`, `services.audit`); structured JSON logs with
  correlation IDs and secret redaction (`observability`); in-memory metrics.
- Mandatory policy floor (`policies.mandatory`,
  `COMMITGUARD_APP_MANDATORY_POLICY_FILE`) that repository configuration cannot weaken.
- Policy weakening detection: CI reports and Check Runs flag commits that would
  disable or relax a trusted policy.
- `commitguard github validate` (configuration, Git version, authentication,
  permissions, events, installation access) and `commitguard github webhook-test`.
- Docs: `github-app.md`, `deployment.md`; threat model, configuration,
  architecture and enforcement docs updated.

### Changed (Phase 5)

- `services.ci` split into `plan_ci` and `execute_ci_plan`. Commits are
  analysed in batches (`Repository.iter_commits`).
- `run_git` accepts additional (never overriding) environment variables;
  `Repository` can carry a per-repository Git environment.
- `CIReport` gains `policy_weakenings`; reports may list a `mandatory:` config source.
- Roadmap: security intelligence (signatures, secret detection) moves after the dashboard.

### Added (Phase 4 — GitHub server-side enforcement)

- `commitguard ci github`: analyses commits introduced by `pull_request`
  (`head ^base`), `merge_group` and `push` events (new branches, deletions,
  annotated tags, force pushes), fails closed (exit 2) on any error, supports
  `--config`, `--fail-on block|warn`, `--max-commits`, `--format json`,
  `--report-file`; writes annotations, job summary and step outputs in Actions.
- Trusted policy source: CI reads `.commitguard.yaml` from the base / before /
  default-branch commit, never from the change; config changes are reported.
- `CIContext` (provider-neutral), `GitHubPullRequestContext`,
  `GitHubPushContext`, `GitHubMergeGroupContext`, `CommitRange`, `PolicySource`.
- Composite `action.yml` (setup-python pinned, hash-pinned dependencies,
  install from the Action's own source) and `requirements/ci.txt`.
- `.github/workflows/commitguard.yml` now enforces on this repository,
  installing CommitGuard from a trusted commit via `git worktree`.
- `commitguard init --github` (pinned Action required, never overwrites) and
  `commitguard github setup` (read-only guidance).
- `commitguard doctor`: rules provenance, GitHub workflow inspection
  (triggers, permissions, pinning, fetch-depth, secrets, cancel-in-progress)
  and an enforcement summary that never claims branch protection.

### Changed

- All workflows pin third-party actions to commit SHAs.
- `Repository.read_blob_at`, `ref_commit`, `object_exists`; pre-push planning
  uses `CommitRange`.
- Reports gain an optional `ci` section.

### Added (Phase 3 — Git hook enforcement)

- `commitguard install` / `uninstall`: managed hooks with `# BEGIN/END COMMITGUARD`
  markers and checksums; existing hooks preserved as `<hook>.pre-commitguard`
  and chained (CommitGuard first, then the original hook with the same stdin);
  refuses shared `core.hooksPath` unless `--allow-shared-hooks-path`.
- `--global` installation through a Git template directory (`init.templateDir`
  set only when unset; `core.hooksPath` never touched).
- `commitguard hook pre-commit | commit-msg <file> | pre-push [remote] [url]`,
  all fail closed (exit 2 on any error).
- pre-push outgoing commit planning: multiple refs, new branches, deletions,
  annotated tags, tags to non-commits, force pushes, merges, detached HEAD,
  pushes to URLs, SHA deduplication, `max_push_commits` bound.
- commit-msg message cleanup honouring `commit.cleanup` and scissors lines.
- `enforcement:` configuration section (per-hook switches, push commit limit).
- `commitguard doctor` rewritten: sections, hook presence/integrity/
  executability/interpreter checks, engine self-test, HEALTHY/DEGRADED/UNHEALTHY.
- `commitguard check --verbose`; commit subjects in reports.
- CI matrix on Ubuntu, macOS and Windows; bandit in the security workflow.
- `.gitattributes` keeping hooks and shell scripts LF.

### Security (Phase 3)

- Hooks run `python -P` so a `commitguard/` directory in the repository cannot
  shadow the installed package.
- Hook reference copies in `hooks/` are generated and verified by tests.

### Added (Phase 2 — AI attribution detection)

- Commit model with derived trailers, pending commits and reserved signature field.
- Lenient, bounded trailer and identity parser recording malformed and evasive
  variants (missing separators, odd key spellings, invisible characters,
  Unicode line separators, trailing escape codes).
- Rule files as data (`rules/*.yaml`) with strict schemas, cross-validation and
  packaging into the wheel; deterministic identity matcher with confidence levels.
- Detectors: `coauthor` (`ai_coauthor`), `identity` (`ai_identity`), `trailer`
  (`ai_trailer`, `malformed_trailer`), `bot` (`bot_identity`).
- Engine skips detectors with no enabled rules and validates finding commit SHAs.
- Layered configuration: built-in → global → repository → `--config`.
- Working `commitguard scan` and `commitguard check` (revision ranges,
  `--message-file`, `--format json`, `--quiet`, `--max-commits`).
- Shared analysis service and JSON/audit-ready report models.

### Changed

- Exit codes: `0` allowed, `1` blocked, `2` any error (previously 3/4/5/70).
- `Finding.rule` renamed to `rule_id`; findings gain `title` and `confidence`;
  `ScanContext` → `CommitContext`, `ScanResult` → `DetectionResult`.
- Unknown agents are no longer expected to be inferred from name wording.
- `commit-msg` hook template now calls `commitguard check --quiet --message-file`.

### Security

- Invisible and look-alike characters in source files replaced with code points.
- Unexpected CLI exceptions map to exit code 2 without tracebacks.

## [0.1.0.dev0] - Phase 1

### Added

- Phase 1 foundation: project structure, packaging (`pyproject.toml`), CI,
  security and placeholder CommitGuard workflows.
- CLI skeleton (Typer): `init`, `policy list` and `doctor` are functional;
  `install`, `uninstall`, `scan` and `check` expose their interfaces and exit
  with a "not implemented" status.
- Strict configuration schema and loader (`.commitguard.yaml`), rejecting
  unknown keys/policies, wrong types, duplicate keys and YAML aliases.
- Built-in policy defaults and fail-closed policy evaluator.
- Core models (`Commit`, `Finding`, `Evidence`, `ScanResult`, `Decision`),
  detector interface, explicit detector registry and detection engine.
- Hardened, read-only Git wrapper: repository discovery and commit reading.
- Terminal output sanitisation, input validation and hashing helpers.
- Stub detectors (`coauthor`, `identity`, `trailer`, `bot`), rule data files,
  hook templates, and xfail specifications for Phase 2 and Phase 3.
- Documentation: architecture, detection engine, policy engine,
  configuration, Git hooks, GitHub enforcement and threat model.
