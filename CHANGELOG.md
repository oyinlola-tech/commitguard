# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
