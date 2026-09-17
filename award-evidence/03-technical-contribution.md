# 03 - Technical contribution

## What was built

**IMPLEMENTED.** One detection and policy engine, delivered through three
enforcement layers, governed centrally, with a research platform measuring all of
it.

| Component | What it does | Scale |
|---|---|---|
| Detection engine | Four detectors over commit metadata; rules as data; evidence with confidence levels | 15 AI agents, 6 automation accounts |
| Policy engine | Layered configuration; total precedence; mandatory floors | 5 built-in policies |
| Git hooks | pre-commit, commit-msg, pre-push; chaining; integrity checksums | 3 hooks, per-repository or global |
| GitHub Actions check | Scans what a pull request, merge group or push introduces | composite action, SHA-pinned |
| GitHub App service | Webhooks, Checks API, scan workers, SQLite store | 7 event types |
| Dashboard and API | Scans, violations, policies, audit, notifications, organization governance | 24 documented API areas |
| Organization governance | Groups, inheritance, simulation, approval, rollouts, exceptions, posture | Phase 8 |
| Research platform | Dataset, five benchmarks, immutable results, experiments, reproduction command | 4 dataset versions |

## Engineering choices worth noting

**A trailer parser with no regular expressions** (`provenance/trailers.py`).
Linear in message size, bounded in trailer count, and it records *why* a line was
odd rather than silently ignoring it. Commit messages are attacker-controlled and
unbounded; a regular expression here would be both a review problem and a
denial-of-service surface. **MEASURED**: a 10 MB commit message is processed in
752 ms.

**Normalisation designed against evasion, not for convenience.** NFKC, case
folding, removal of all 4,174 Unicode default-ignorable code points, and a small
explicit map of Cyrillic and Greek look-alikes - applied to comparison keys only,
never to the evidence shown to a user. **TESTED** against the Unicode 18.0.0 data
file.

**Fail-closed as an invariant, not a habit.** A detector that raises blocks. A
finding with no policy blocks. Unparseable configuration is exit 2. GitHub
unreachable is `error`, never `success`. **TESTED** as a property over generated
exception types and policy actions.

**Framework-free where it matters.** The API is a WSGI application with an
explicit route table; the store is SQLite with static SQL. Three runtime
dependencies (typer, pydantic, PyYAML), plus `cryptography` only for the App.
Fewer dependencies is a smaller supply-chain surface, which a security tool should
care about more than developer convenience.

**Types and immutability throughout.** `mypy --strict` passes; models are frozen
pydantic types; policy versions and benchmark results are append-only.

## Quality signals

| | |
|---|---|
| Tests | **1,343**, of which **350** carry the `security` marker |
| Static analysis | ruff (including bandit-derived rules), `mypy --strict`, bandit in CI |
| CI | Three operating systems, two Python versions, plus security, platform-validation and package jobs |
| Documentation | 107 files, and tests that fail when documentation contradicts the code |
