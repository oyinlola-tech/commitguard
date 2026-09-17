# Evidence timeline

Actual dates, taken from the repository's history (`git log`) and from recorded
benchmark results. The project is three days old at the time of writing, which is
itself a fact rather than something to obscure.

## Milestones

| Date | Milestone | Evidence |
|---|---|---|
| 2026-09-14 | Project started; first commit | `git log --reverse` |
| 2026-09-14 | Repository made public on GitHub | GitHub repository created 2026-09-14T21:14:04Z |
| 2026-09-14 | **Phase 1**: architecture, commit model, policy engine skeleton, test and architectural-boundary suites | commits of 2026-09-14 |
| 2026-09-14 | **Phase 2**: detection engine - identity and trailer parsing, rule-driven matching | `feat(detection): implement phase 2 AI attribution and robust provenance parsing` |
| 2026-09-15 | **Phase 3**: Git hook enforcement, chaining, integrity checksums | `feat(hooks): implement phase 3 git hook enforcement` |
| 2026-09-15 | **Phase 4**: GitHub Actions enforcement, trusted policy source, the composite Action | `feat(ci): implement GitHub server-side enforcement (Phase 4)` |
| 2026-09-15 | **Phase 5**: GitHub App service - webhooks, Checks API, scan service, storage | `feat(github): implement GitHub App service and centralized enforcement` |
| 2026-09-15 | **Phase 6**: dashboard and control plane - API, authorization, web UI, e2e harness | `docs: update documentation for Phase 6 dashboard and control plane` |
| 2026-09-15 | **Phase 7**: operational features - notifications, merge queue handling, policy recovery | `feat(controlplane): implement phase 7 operational features` |
| 2026-09-17 | **Phase 8**: organization governance - groups, inheritance, simulation, approvals, exceptions, staged rollouts, security posture | `feat(governance): implement full organization governance lifecycle` |
| 2026-09-17 | **Phase 9**: research and benchmarking platform - labelled dataset, benchmarks, experiments, immutable results | `feat(research): add benchmarking and research framework` |
| 2026-09-17 13:24 UTC | **First detection benchmark run**: found a bypass (1 false negative) | `benchmarks/results/raw/detection/20260917T132425…` |
| 2026-09-17 14:16 UTC | Fix introduced two false positives; caught by the next run | `…20260917T141632…` |
| 2026-09-17 14:35 UTC | Performance defect found and fixed: 10 MB message 4.8 s to 0.75 s | `benchmarks/results/raw/performance/` (both runs) |
| 2026-09-17 14:53 UTC | Hook overhead measured over 20 repetitions | `benchmarks/results/raw/hooks/` |
| 2026-09-17 14:54 UTC | 100,000-commit history scanned in 43.9 s | `benchmarks/results/raw/repository/` |
| 2026-09-17 14:54 UTC | Linux platform validation: 15/15 | `benchmarks/results/raw/platform/` |
| 2026-09-17 14:58 UTC | **Fuzzing found a second bypass class** (Unicode alphanumerics before a key) | `…20260917T145810…` (11 false negatives) |
| 2026-09-17 15:12 UTC | **Fuzzing found a third bypass class** (default-ignorable characters) | `…20260917T151244…` (15 false negatives) |
| 2026-09-17 15:15 UTC | All four dataset versions clean after the fixes | `…20260917T151509…` to `…151523…` |
| 2026-09-17 | **Phase 10**: external adoption - reproduction command, examples, release engineering, security and research documentation, two ReDoS fixes | this commit range |

## What has not happened yet

Recorded here so the timeline cannot be misread as an adoption story:

| Not yet | Would be recorded as |
|---|---|
| First public release | a tag and a GitHub release |
| First external evaluation | `evidence/external-validation/evaluation-001.md` |
| First external contribution | a merged pull request from another account |
| First external deployment | `evidence/deployments/` |
| First externally reported security issue | an advisory and a row in `docs/security/vulnerability-response.md` |
| Independent reproduction of the benchmarks | an evaluation report |

## Reading this timeline honestly

Three days of work produced a system with 1,343 tests and 13 recorded benchmark
runs, which says something about intensity and something about the use of AI
assistance in building it. What it does not say is that any of this has been
proven in use by other people. The evidence here is of engineering and
measurement discipline, not of adoption.
