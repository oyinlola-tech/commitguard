# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
