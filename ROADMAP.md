# CommitGuard roadmap

CommitGuard is pre-alpha (`0.1.0.dev0`) with a single maintainer. This roadmap
describes what exists, what is being worked on, and what may come later. It is
not a commitment: **Planned** items have no dates and may change or be
dropped. No release has been published yet, and no external production
deployments have been recorded.

Sources: [CHANGELOG.md](CHANGELOG.md), the documentation in [docs/](docs/),
and the code. When they disagree, the code is authoritative and the
disagreement is a documentation bug.

Status words used here:

| Status | Meaning |
|---|---|
| **Completed** | implemented and covered by the automated test suite |
| **In progress** | being implemented now (Phase 10) |
| **Planned** | intended, not started; no date |
| **Research** | an open question to investigate; may never become a feature |
| **Experimental** | implemented and tested offline, but not yet validated in a real deployment or on every platform |

## Completed

### Phase 1: foundation

Project structure and packaging, Typer CLI, strict configuration schema and
loader, built-in policy defaults with a fail-closed evaluator, core models,
detector interface and registry, hardened read-only Git wrapper, output
sanitisation and input validation.

### Phase 2: AI attribution detection

Commit model with parsed trailers; a lenient, bounded trailer and identity
parser that records malformed and evasive variants; rule data in `rules/*.yaml`;
detectors `coauthor`, `identity`, `trailer` and `bot`; layered configuration;
`commitguard scan` and `commitguard check`; exit codes `0`/`1`/`2`.

### Phase 3: Git hook enforcement

`commitguard install` / `uninstall` (per repository or `--global` through a
template directory) with chaining of existing hooks and integrity checksums;
`pre-commit`, `commit-msg` and `pre-push` enforcement that fails closed;
`commitguard doctor`; CI on Ubuntu, macOS and Windows.

### Phase 4: GitHub server-side enforcement

`commitguard ci github` for `pull_request`, `merge_group` and `push`; policy
read from the trusted base commit; composite `action.yml` with SHA-pinned
actions and hash-pinned dependencies; `commitguard init --github`;
`commitguard github setup`; this repository enforces CommitGuard on itself.

### Phase 5: GitHub App

Webhook service with signature verification and replay protection; App JWT and
down-scoped installation tokens; installation lifecycle; metadata-only Git
mirrors; `commitguard-app` Check Runs; SQLite state store with retention;
audit events; mandatory policy floor; policy-weakening detection;
`commitguard github validate`, `webhook-test` and `serve`.

### Phase 6: security dashboard and control plane

React dashboard and `/api/v1`; GitHub sign-in with PKCE; server-side sessions
and CSRF protection; roles and tenant isolation; scans, violations and their
lifecycle; versioned organization policy; enforcement evidence; audit log;
`commitguard dashboard members`.

### Phase 7: notifications, merge queue, re-runs and recovery

Transactional notification outbox with in-app, SMTP e-mail and signed webhook
channels; merge group validation in the App; GitHub "Re-run" handling with
scan executions; event processing records and automatic retries; organization
policy rollback and diff.

### Phase 8: organization governance

Organization, group and repository policies with mandatory and default
entries; drafts, approvals and emergency publication; read-only policy
simulation; staged rollouts with automatic pause and rollback; scoped expiring
exceptions; repository inventory with `enforce` and `monitor` modes; groups and
bulk operations; organization rules; scheduled scans; security posture, trends
and JSON/CSV compliance reports (explicitly not certifications).

### Phase 9: measurement and research

Recorded in Git history; `CHANGELOG.md` does not have an entry for it yet.

- `commitguard benchmark detection | performance | hooks | repository | platform | dataset`
  (`src/commitguard/research/`), with no network access and no credentials.
- Labelled, deterministic, versioned commit datasets in `benchmarks/datasets/`
  (`v1.0.0`, `v1.1.0`); versions only add cases.
- Immutable benchmark results in `benchmarks/results/raw/`, indexed in
  `benchmarks/results/processed/index.json`.
- Security regression suite selected by the `security` marker
  (`pytest -m security`), and the `observe` fixture for recording what
  security experiments observed.
- `scripts/benchmark_governance.py` for governance operations at synthetic
  scale.

## In progress

### Phase 10: external adoption, reproducibility and release engineering

- **External adoption:** an external evaluation program, issue forms for
  feedback, a consent-based pilot program, a data policy and a case study
  template ([docs/community/](docs/community/README.md)). No external
  evaluations have been recorded yet.
- **Reproducibility:** `commitguard reproduce security | benchmark | integration | github`
  to rerun evidence (prerequisites that are missing are reported as SKIPPED
  with a reason, never PASS); `commitguard benchmark compare` to compare a new
  result with a recorded baseline; platform validation
  (`commitguard benchmark platform`) on macOS and Windows CI runners;
  property-based fuzzing of the parser.
- **Release engineering:** a tag-triggered release workflow that runs the
  validation gate, builds the sdist and wheel once, smoke-tests the built wheel
  on Linux, macOS and Windows, writes `SHA256SUMS` and a CycloneDX SBOM, and
  creates a draft GitHub release. See
  [docs/maintainers/release-process.md](docs/maintainers/release-process.md).
  No release has been published, and nothing is published to PyPI.
- **Support documentation:** compatibility matrix and policies
  ([docs/support/](docs/support/compatibility.md)), maintainer documentation
  ([docs/maintainers/](docs/maintainers/README.md)).

## Planned

No dates and no commitment. Order does not imply priority.

- **Security intelligence:** signed commit verification (a model exists in
  `src/commitguard/provenance/signatures.py`, not wired in), secret detection
  in changes (`src/commitguard/git/diff.py` is a placeholder), author and
  committer relationship analysis, more advanced bot detection.
- **Reporting:** SARIF export (findings already carry the data it needs), pull
  request comments (not implemented).
- **Hardening:** bounded capture of Git subprocess output
  (`utils/subprocess.py`; listed as planned in `docs/threat-model.md`).
- **Configuration:** repository-specific rule extensions (not supported today,
  `docs/configuration.md`).
- **Git:** support for bare repositories in the CLI.
- **App service scale:** a shared database implementation of the storage
  interfaces for multi-host deployments (today: SQLite on a single host).
- **API:** an OpenAPI document for `/api/v1`.
- **CI:** running the dashboard checks (`web/`) in CI.
- **Distribution:** a published release on GitHub. Publishing to PyPI is not
  planned under the name `commitguard`, which belongs to an unrelated project.

## Research

Open questions. Results, if any, will be published with their method and
limitations.

- How well attribution *evidence* in commit metadata reflects AI involvement.
  CommitGuard detects attribution in metadata; it does not and cannot
  determine how code was written.
- False positive and false negative rates on real public commit histories, as
  opposed to the labelled synthetic datasets.
- Evasion techniques beyond the adversarial dataset classes, found through
  fuzzing and external reports.
- The developer experience cost of local hooks (latency, interruptions,
  bypass behaviour), measured with `commitguard benchmark hooks` and with
  consenting pilot participants.
- Cross-platform behaviour of Git hooks (shells, paths with spaces and
  non-ASCII characters, line endings).

## Experimental

Implemented and tested, but not yet validated where it matters:

- **GitHub App service, dashboard, notifications, merge queue support and
  organization governance:** tested against real Git and an offline model of
  the GitHub API and OAuth flow, **not against github.com**. Treat a first
  deployment as a trial next to the GitHub Action.
- **Governance at scale:** performance figures come from synthetic data on one
  machine (`scripts/benchmark_governance.py`), not from a production
  organization.
- **GitHub Action on macOS and Windows runners:** the test suite runs on all
  three platforms, but the Action itself has only been exercised on Linux.
- **Benchmark result format** (`schema_version` 1) and the research CLI may
  change before 1.0.
