# Compatibility policy

> Status: this is the project's intent for a pre-alpha tool with a single
> maintainer. CommitGuard is `0.1.0.dev0` and **no release has been
> published**. Until 1.0, anything below may change; the rules here describe
> how such changes are made and communicated, not a guarantee that they will
> not happen.

Related: [compatibility matrix](compatibility.md),
[maintenance policy](maintenance-policy.md).

## Versioning intent

CommitGuard intends to follow [Semantic Versioning](https://semver.org/) once
releases exist (`CHANGELOG.md` states this):

| Change | After 1.0 | Before 1.0 (`0.y.z`) |
|---|---|---|
| Incompatible change to a public interface listed below | major version | minor version (`0.y` → `0.y+1`) |
| Backwards-compatible feature | minor version | minor or patch version |
| Backwards-compatible fix | patch version | patch version |

Pre-release versions use PEP 440 spelling in `pyproject.toml`
(`0.1.0.dev0` today; release candidates `rcN`).

**Pre-1.0 caveat.** Before 1.0, incompatible changes can land in any minor
version, and on `main` at any commit. The project will still document them in
`CHANGELOG.md` and, where practical, follow the deprecation process below.
Security fixes take priority over compatibility: if keeping an interface would
keep a vulnerability or a fail-open behaviour, the interface changes.

**Security-relevant behaviour is never changed silently.** Any change that can
make CommitGuard allow something it blocked before (a default policy, a
detector, rule data, trusted policy source, a mandatory floor) is called out in
`CHANGELOG.md`, even when it is also a fix.

## Public interfaces

### CLI exit codes

The most stable contract, because Git hooks and CI depend on it
(`src/commitguard/cli/output.py`):

| Code | Meaning |
|---|---|
| `0` | allowed (no findings, or only `allow`/`warn` findings) |
| `1` | blocked (at least one finding or detector failure evaluated to BLOCK) |
| `2` | error (invalid configuration or rules, Git failure, bad arguments, any unexpected error) |

No other exit codes are used. Adding a new exit code is an incompatible
change. `commitguard doctor` uses the same codes (`2` for UNHEALTHY).

### CLI commands and options

- Command names, option names and their meaning are public interfaces.
  Removing or renaming them, or changing a default in a way that blocks less,
  is an incompatible change. Adding commands or options is compatible.
- `commitguard hook pre-commit | commit-msg | pre-push` is the stable interface
  called by installed hooks. Installed hook scripts embed the interpreter path
  and call `python -P -m commitguard hook <name>`. After an upgrade that
  changes the generated hook, `commitguard doctor` reports the hooks as
  outdated; run `commitguard install` again.
- Human-readable text output is **not** a stable interface. Scripts should use
  `--format json` or exit codes.
- `commitguard benchmark` and its result format are **experimental** before
  1.0.

### JSON reports

Reports from `scan`, `check` and `ci github` with `--format json` carry
`"schema_version": 1` (`src/commitguard/services/reports.py`). Within a schema
version, fields may be added but not removed or changed in meaning. Removing
or changing a field increments `schema_version`.

### Configuration (`.commitguard.yaml`)

- The file declares `version: 1`; it is the only accepted version.
- Validation is strict by design: unknown keys, unknown policy IDs, unknown
  actions, wrong types and duplicate keys are errors (exit `2`), never ignored.
  A consequence: **a configuration that uses a key added in a newer
  CommitGuard is rejected by an older CommitGuard.** Pin the same CommitGuard
  commit everywhere a repository's configuration is evaluated (hooks, Action,
  App).
- Omitted policies and fields fall back to built-in secure defaults, never to
  "off". Existing configuration files keep working when new policies are
  added; the new policy applies with its default action.
- An incompatible format change would introduce `version: 2`, with version 1
  accepted during a deprecation period.

### Policies

- Policy IDs (`ai_coauthor`, `ai_identity`, `ai_trailer`, `malformed_trailer`,
  `bot_identity`) and actions (`allow`, `warn`, `block`) are public
  interfaces.
- Precedence (`block` > `warn` > `allow`) and fail-closed evaluation (a
  finding without a policy, or a detector failure, blocks) do not change.
- Changing a built-in default action is a security-relevant change
  (see above). `config/default.yaml` must stay identical to the
  `commitguard init` template (`tests/unit/config/test_config_validation.py`)
  and state the same actions as `commitguard.policies.defaults`; the second
  part is not checked by a test today.
- Organization policy documents in the App (floors and defaults, versions,
  drafts) follow the API rules below.

### Rules (`rules/*.yaml`)

- Rule files carry `schema_version: 1` and are validated strictly. They ship
  inside the package; there is no supported way to load other rule files in
  the CLI, and repository-specific rule extensions are not supported.
- Rule **data** (known agents, domains, bots) changes between versions: adding
  an identity can block commits that were allowed before. Such additions are
  normal feature changes and are listed in `CHANGELOG.md`. Removing or
  weakening an identity is security-relevant.
- Scans record a rules version fingerprint, so a decision can be traced to the
  rule data that produced it.

### Database schema and migrations

The GitHub App service and dashboard keep their state in one SQLite database
(`commitguard-app.sqlite3` in `COMMITGUARD_APP_DATA_DIR`).

What exists today (`src/commitguard/github/storage.py`):

- A `meta` table holds `schema_version`; the current version is **4**.
- Migrations are an ordered tuple of SQL scripts (`_MIGRATIONS`). On start-up,
  pending migrations are applied **automatically, in order, inside one
  transaction** (`BEGIN IMMEDIATE`); a failure rolls the whole upgrade back.
- A database with a **newer** schema version than the running code, or an
  unreadable version, is **refused** ("unsupported schema version"). There
  are no down-migrations: **downgrading CommitGuard against an upgraded
  database is not supported.** Restore the backup taken before the upgrade
  instead.
- Some migrations add triggers that make history immutable (published policy
  versions, audit events). Migrations never delete audit history; retention
  purging is a separate, configured process.

Policy:

- Schema changes are always a new, numbered migration appended to the tuple.
  An existing migration is never edited after it has been on `main`, because
  deployed databases have already applied it.
- A new migration comes with a test that upgrades a database written by the
  previous schema. Today, upgrades to the current schema are tested from
  schema 1 (`test_phase5_database_is_upgraded_in_place` in
  `tests/unit/controlplane/test_controlplane.py`) and schema 2
  (`test_migration_from_schema_2_backfills_executions` in
  `tests/unit/github/app/test_phase7_events_and_storage.py`); there is no
  dedicated test that starts from schema 3. Refusal of a newer schema is
  tested by `test_newer_schema_is_refused`.
- A migration that changes behaviour for existing data (for example Phase 8
  marking existing repositories `onboarded` in `enforce` mode) is described in
  `CHANGELOG.md`, including what happens to enforcement.
- Operators should back up the data directory before every upgrade (see
  [../deployment/production.md](../deployment/production.md)).

### HTTP API (`/api/v1`)

- The path is versioned. Within `/api/v1`, the intent is additive change only:
  new routes, new optional parameters, new response fields. Clients must ignore
  unknown fields.
- Removing a route or field, changing a field's meaning, or tightening
  validation in a way that rejects previously valid requests is incompatible.
  Before 1.0 such changes can still happen in `/api/v1`; they are listed in
  `CHANGELOG.md`. After 1.0 they would require `/api/v2`.
- Security fixes (authorization, CSRF, rate limits, input validation) may
  reject requests that were previously accepted, at any time.
- Webhook payloads CommitGuard **sends** to notification endpoints are signed;
  the signature scheme and envelope follow the same rules.
- There is no OpenAPI document yet.

### GitHub integration

- **Check names** are interfaces, because branch protection and rulesets
  require them by name: `commitguard` (Action job), `commitguard-app` and
  `commitguard-app/push` (App). Renaming a check is incompatible and would
  silently stop a required check from being satisfied; it will not be done
  without a deprecation period and a documented migration.
- **Action inputs and outputs** (`config`, `fail-on`, `max-commits`,
  `python-version`; `result`, `commits`, `violations`, `warnings`) follow the
  CLI rules. Users pin the Action to a commit SHA, so an update is always an
  explicit change on their side.
- **App permissions:** adding a *required* permission forces every installation
  owner to approve the change on GitHub, so it is treated as incompatible.
  New features that need a permission use an optional permission first
  (as merge queue support did) and report it through
  `commitguard github validate`.
- **Webhook events:** new features subscribe to optional events; the required
  set stays `installation`, `installation_repositories`, `pull_request`,
  `push`.
- **GitHub REST API version:** requests send
  `X-GitHub-Api-Version: 2022-11-28`. Moving to a newer API version is done
  deliberately, with tests, and noted in `CHANGELOG.md`. See
  [../maintainers/repository-management.md](../maintainers/repository-management.md#github-api-change-monitoring).

### Benchmarks and datasets

- Datasets are versioned directories in `benchmarks/datasets/`; a version's
  cases never change, and new versions only add cases.
- Recorded benchmark results are immutable files with `schema_version: 1`.
  A new result format increments the schema version; old results stay as they
  were recorded.

## Deprecation process

1. **Announce.** The change is recorded under a `Deprecated` heading in
   `CHANGELOG.md`, and the affected documentation says what replaces it and
   from which version the old behaviour may be removed.
2. **Warn.** Where the software can detect use of the deprecated interface, it
   emits a warning (CLI stderr, `commitguard doctor`, `commitguard github
   validate`, or an API response field) without changing the exit code or the
   security decision.
3. **Keep working.** The deprecated interface keeps working for at least one
   minor release (before 1.0) or one major release (after 1.0).
4. **Remove.** Removal is listed under `Removed` in `CHANGELOG.md` with the
   migration steps.

Exceptions: security fixes, and interfaces that are labelled experimental,
may skip the deprecation period. The reason is stated in `CHANGELOG.md`.
