# 10 - Open source maturity

**IMPLEMENTED.** Public, MIT-licensed, at https://github.com/oyinlola-tech/commitguard
since 2026-09-14.

## What a newcomer finds

| | State |
|---|---|
| README with a working quick start | **IMPLEMENTED** |
| 5-minute getting-started guide | **IMPLEMENTED** - [docs/getting-started/quickstart.md](../docs/getting-started/quickstart.md) |
| Worked examples with expected results | **IMPLEMENTED** - `examples/`, and **TESTED**: a test runs each sample commit through the real engine and fails if the README claims a different decision |
| CLI reference with exit codes | **IMPLEMENTED** - [docs/cli/](../docs/cli/) |
| API reference | **IMPLEMENTED** - [docs/api/](../docs/api/) |
| Deployment guides per mode | **IMPLEMENTED** - [docs/deployment/](../docs/deployment/) |
| Contributing guide | **IMPLEMENTED** |
| Security policy with private reporting | **IMPLEMENTED** |
| Issue forms (bug, feature, security question, docs, evaluation) | **IMPLEMENTED** |
| Pull request template, CODEOWNERS | **IMPLEMENTED** |
| Maintainer documentation | **IMPLEMENTED** - [docs/maintainers/](../docs/maintainers/) |
| Roadmap, separating completed from planned | **IMPLEMENTED** |
| Compatibility and maintenance policies | **IMPLEMENTED** - [docs/support/](../docs/support/) |
| Scoped first issues | **IMPLEMENTED** as drafts - none opened yet |

## Reproducibility for outsiders

**IMPLEMENTED.** `commitguard reproduce all` re-runs the security suite, rebuilds
the dataset and checks its fingerprint against the published files, re-measures
detection, runs the integration suite, and validates a GitHub installation if
credentials exist. Missing prerequisites produce `SKIPPED` with a reason - never a
pass.

`commitguard report security` then rebuilds the reports from recorded evidence.

## Release engineering

**IMPLEMENTED, never executed.** `.github/workflows/release.yml`: a validation
gate (lint, types, full suite, security suite, benchmark smoke, evidence
reproduction), one build, install-and-smoke-test on Linux, macOS and Windows,
`SHA256SUMS`, a CycloneDX SBOM, and a **draft** release for the maintainer to
publish. No release has been made.

## Installation, and a real supply-chain problem

**The PyPI name `commitguard` belongs to an unrelated project.** Documentation here
previously said `pip install 'commitguard[app]'`, which would have installed
someone else's Git hooks library - dependency confusion created by our own
documentation. Corrected everywhere, recorded in the threat model, given an ADR,
and **TESTED**: a test fails if any Markdown code block reintroduces a PyPI
install line.

## Community metrics (2026-09-17)

| | Count |
|---|---|
| Contributors | 1 |
| Stars | 2 |
| Forks, issues, pull requests, releases | 0 |

Three days old, and stated rather than dressed up.
