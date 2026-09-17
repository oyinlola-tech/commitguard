# Phase 10 report: external adoption, open source validation and real-world readiness

CommitGuard 0.1.0.dev0 · 2026-09-17 · Linux 7.1.5 (x86_64), Intel i5-8350U, 8 CPUs,
16 GB, Python 3.13.15, Git 2.53.0

Every number here comes from a recorded result file, a test run or the GitHub API.
Where there is no evidence, the section says so.

## 1. Executive summary

Phase 10 moved CommitGuard from "measured by its author" to "installable,
reproducible and evaluable by someone else". The work produced two commands
(`commitguard reproduce`, `commitguard report security`), a security test suite
that fuzzes every parser handling untrusted input, verified examples, release
engineering, and the documentation an outsider needs.

It also found **five real defects**, three of them detection bypasses:

| Defect | Found by | Severity |
|---|---|---|
| Unicode letters and numbers before a trailer key hid AI attribution | property-based fuzzing | High |
| Default-ignorable characters (variation selectors, U+034F, Hangul fillers) hid a key or an agent alias | property-based fuzzing | High |
| Quadratic JWT redaction (4.6 s on 80 KB) reachable from untrusted text | ReDoS suite | Medium |
| Quadratic workflow `secrets.` scan (2.7 s on 60 KB) on repository-supplied files | ReDoS suite | Medium |
| `commitguard github serve` never loaded notification settings: notifications silently never delivered | documentation review against code | Medium |

Plus a documentation-level supply-chain problem: several pages instructed
`pip install 'commitguard[app]'`, which installs an **unrelated** PyPI project.

Final state: **1,385 tests pass** (1 skipped), 350 of them security tests; ruff,
`ruff format`, `mypy --strict` and `bandit -ll` are clean; detection measures **0
false negatives and 0 false positives** on 9,174 labelled cases.

## 2. Original project objective

Enforce a Git contribution policy - whether commits may carry AI agent
attribution - across developer machines, repositories and organizations,
accurately, fast enough for the commit path, and honestly about its limits.
Unchanged since Phase 1.

## 3. External adoption objective

Make the project usable and checkable without the author: installable from
documented instructions, reproducible evidence, an evaluation path, a
contribution path, and a release process. Phase 9 produced internal evidence;
Phase 10 produced the means for external evidence. **No external evidence exists
yet**, and every relevant page says so.

## 4. Developer experience

| Change | State |
|---|---|
| `commitguard init` prompts when run by a person, never in a script | Implemented |
| `--install-hooks` sets a repository up in one command | Implemented |
| `--non-interactive` for automation | Implemented |
| `doctor` reports PASS / WARNING / FAIL / NOT CONFIGURED / INFO | Implemented |
| `doctor --json` for automation | Implemented |
| `doctor` checks runtime dependencies and GitHub App configuration | Implemented |

`NOT CONFIGURED` is never a pass: an unconfigured integration enforces nothing,
and the presence of App settings is reported as INFO rather than as a working
installation, because only `commitguard github validate` can check that.

## 5. Installation

**CommitGuard is not on PyPI.** That name belongs to an unrelated project, so the
supported installation is from a pinned Git commit:

```bash
pipx install "git+https://github.com/oyinlola-tech/commitguard@<commit-sha>"
```

Documentation that said otherwise is corrected, the CLI's own message names the
Git source, and `tests/integration/test_examples.py` fails if any Markdown code
block reintroduces a PyPI install line. Recorded as
[ADR-008](../adr/008-not-published-to-pypi.md) and in the threat model.

## 6. Quick start

`docs/getting-started/quickstart.md`: install, initialise, verify, trigger a
violation, fix it, and understand what local hooks do not protect - in six steps,
ending with the GitHub check because that is the layer that actually blocks.

## 7. Deployment models

Documented in `docs/deployment/`: local only, GitHub Actions, GitHub App,
organization governance, self-hosted and production, each with its security
properties, its limits and an honest status. Local hooks and the Actions check are
**Implemented**; the App service and organization governance are **Experimental**;
multi-host operation is **Planned**.

## 8. Reproducibility

`commitguard reproduce all|security|benchmark|integration|github` with statuses
PASS / FAIL / SKIPPED / NOT RUN. A skipped step is never a pass - without GitHub
credentials the github step prints the missing variables.

The benchmark step rebuilds the 9,174-case dataset, compares its fingerprint with
the published files (`e0592f4287b13619…`) and re-measures detection. It needs no
credentials and takes about ten seconds.

## 9. External evaluation

`docs/community/external-evaluation.md`: what to evaluate, a 10-step script with
expected results, how to report, and consent rules for publication. An
`evaluation-feedback` issue form exists. **No external evaluations have been
recorded.**

## 10. Open source improvements

Issue forms (bug, feature, security question, documentation, evaluation, new AI
identity), pull request template, `CODEOWNERS`, `ROADMAP.md`, `CITATION.cff`,
maintainer documentation (8 pages), community documentation (7 pages), support and
compatibility policies, and drafted good first issues.

## 11. Security disclosure

`SECURITY.md` now states response targets (acknowledge 5 working days, triage 10,
fix Critical/High within 30 days of confirmation), coordinated disclosure, safe
harbour, and what never to include in a report. The full process, with severity
definitions and the requirement that every confirmed vulnerability gets a
regression test, is in `docs/security/vulnerability-response.md`.

## 12. Supply chain security

Implemented: few runtime dependencies, hash-pinned CI installs, `pip-audit`,
dependency review, SHA-pinned Actions, least-privilege tokens,
`persist-credentials: false`, a lockfile for the dashboard, bandit and ruff
security rules, and a scanner installed from a trusted commit.

Stated gaps: signed releases, published SBOM, artifact checksums (all written into
the release workflow but never executed), automated dependency updates, secret
scanning in CI, reproducible builds, third-party audit.

## 13. Release engineering

`.github/workflows/release.yml`: validation gate (lint, types, full suite,
security suite, benchmark smoke, evidence reproduction), one build, install and
smoke-test on Linux, macOS and Windows, `SHA256SUMS`, CycloneDX SBOM, and a
**draft** release. Tags `vX.Y.Z` and `vX.Y.ZrcN`. **Never executed: no release has
been published.**

## 14. Cross-platform validation

| Platform | Test suite | Platform validation |
|---|---|---|
| Linux | Passing in CI | **15/15 measured** |
| macOS | Passing in CI (run 35187790650) | Pending - job added, not yet run |
| Windows | 28 failures found and fixed locally | Pending - not yet verified in CI |

A `platform-validation` CI job now runs `commitguard benchmark platform --json` on
all three runners and uploads the result.

## 15. Real-world pilot

A consent-based process exists (`docs/community/pilot-program.md`): observe →
report-only → evaluate → tune → limited enforcement → evaluate → broader, with the
metrics to record and where each comes from. **No pilot has been run.**

## 16. Pilot metrics

Defined, not collected. Dashboard and audit log provide commits scanned,
violations, latency, policy changes and exceptions; false positives and
remediation time need human judgement. **No data exists.**

## 17. External feedback

Feedback categories, lifecycle states and the improvement loop are documented
(`docs/community/feedback-triage.md`). **0 issues and 0 pull requests have been
received** (GitHub, 2026-09-17).

## 18. Security incidents

None. No incident has occurred in this project, and no deployment exists in which
one could.

## 19. Vulnerabilities discovered

Five in Phase 10 (table in section 1), plus the documentation-driven dependency
confusion. Across Phases 9 and 10, **ten defects, eight security-relevant**, all
found by this project's own benchmarks, fuzzers and reviews. **None reported
externally, because there are no external users.**

## 20. Vulnerabilities fixed

All five, each with a regression test:

| Fix | Test |
|---|---|
| ASCII-only key start, shortest-reading selection | `test_non_ascii_letters_and_numbers_cannot_hide_a_trailer` |
| Look-alike letters stay part of the key | `test_look_alike_letters_stay_part_of_the_key` |
| Default-ignorable code points removed in normalisation | `test_default_ignorable_characters_cannot_hide_a_trailer_key` |
| Linear JWT redaction | `test_redaction_of_untrusted_text_is_linear`, `test_redaction_still_removes_jwts` |
| Linear workflow `secrets.` scan | `test_workflow_secret_check_is_linear_on_untrusted_workflows` |
| Notification settings loaded by both entry points | `tests/unit/github/app/test_service_entry_points.py` |

Each detection fix also has a new dataset version and a **published failing run**
recorded before the fix existed.

## 21. Performance regression results

`commitguard benchmark compare` (new) compares a result with an earlier one using
documented thresholds: correctness changes at any size; latency and throughput
beyond 20%; memory, hook overhead and rule loading beyond 25%.

Final Phase 10 code against the Phase 9 baseline: **no regression**.

| Measure | Phase 9 | Phase 10 |
|---|---|---|
| 10,000-commit batch, p50 per commit | 0.1162 ms | 0.1027 ms |
| Throughput | 6,901/s | 7,592/s |
| 10 MB message, p50 | 751.8 ms | 592.2 ms |
| Rule loading | 28.6 ms | 18.1 ms |
| Peak RSS | 82.0 MiB | 83.1 MiB (+1.3%, within threshold) |
| Detection accuracy | 0 FN / 0 FP | 0 FN / 0 FP |

## 22. Operational reliability

Four failure modes are recorded experiments: GitHub unreachable, permissions
revoked, database failure and installation suspended. All produce `error` or an
explicit outage state; none produces a passing check. Ten operational procedures
are documented in `docs/operations/runbook.md`.

## 23. Case studies

None. The template and confidentiality rules exist
(`docs/community/case-study-template.md`); `evidence/case-studies/` is empty.

## 24. Open source metrics (GitHub, 2026-09-17)

| | |
|---|---|
| Repository | public, MIT, created 2026-09-14 |
| Contributors | 1 |
| Stars / forks | 2 / 0 |
| Issues / pull requests | 0 / 0 |
| Releases / tags | 0 / 0 |
| Commits | 96 |

## 25. External contributions

None received. `docs/community/good-first-issues.md` drafts seven scoped issues
grounded in real gaps, for the maintainer to open.

## 26. Public documentation

125 documents under `docs/`, 171 including examples and evidence. New in Phase 10:
quick start, CLI reference (9), API reference (24), deployment (8), operations,
security (9), research (18), ADRs (8), maintainers (8), community (7), support (3),
evidence and award packages.

Documentation is tested, not just written: examples are executed against the real
engine, documented commands must exist, and no code block may recommend the PyPI
package.

## 27. Technical demonstration

Eight runnable demonstrations in `award-evidence/15-demonstrations.md`, each with
expected output. The video walkthrough is **planned**; its outline is in
`docs/presentations/commitguard-technical-overview.md`.

## 28. Technical article

`docs/presentations/technical-article.md`: *Building defence in depth for Git
contribution security* - the problem, why string matching fails, the enforcement
design, the three bypasses found in the author's own implementation, the
denial-of-service defects, and the lessons.

## 29. Evidence timeline

`docs/evidence/timeline.md`: dated milestones from the repository history and the
recorded results, plus an explicit list of what has **not** happened (release,
external evaluation, external contribution, deployment, independent reproduction).

## 30. Award evidence

`award-evidence/`, 17 documents plus an index, with every claim labelled
IMPLEMENTED, MEASURED, TESTED, EXTERNALLY VALIDATED, DEPLOYED or PLANNED. Two
sections - external validation and real-world deployment - state plainly that they
are empty.

## 31. Known limitations

Metadata is self-asserted and is not proof of authorship; local hooks are
bypassable (seven ways, measured); branch protection is outside CommitGuard and is
modelled rather than exercised; the dataset is synthetic; accuracy is measured
after fixing what the dataset exposed; hook overhead is about a second per commit;
the App service is experimental and single-instance; macOS and Windows platform
validation is pending; **no independent validation exists**.

## 32. Remaining risks

| Risk | Status |
|---|---|
| A further detection bypass in a category not yet imagined | Likely over time; the dataset, fuzzers and a private reporting path exist to catch it |
| The author is the only reviewer of his own security work | Unmitigated; a review guide exists for a future reviewer |
| GitHub changes an API or behaviour CommitGuard depends on | Monitoring documented; the fake GitHub must be updated to match |
| A release is never validated end to end until the first tag | The workflow is written but unexecuted |
| Windows support regressing unnoticed | Mitigated once the CI matrix run is verified |

## 33. Future research

`docs/research/roadmap.md`: commit attestations, verified developer identity,
formal policy verification, large-scale repository analysis, incremental scanning,
and reducing hook start-up cost. Telemetry is deliberately excluded.

## 34. Files changed

| Area | Change |
|---|---|
| New source modules | `research/reproduction.py`, `research/report.py`, `research/compare.py`, `cli/commands/reproduce.py`, `cli/commands/report.py` |
| Changed source | `provenance/trailers.py`, `provenance/normalization.py`, `security/secrets.py`, `github/workflow.py`, `github/app.py`, `cli/commands/github.py`, `cli/commands/doctor.py`, `cli/commands/init.py`, `cli/commands/benchmark.py`, `cli/app.py`, `research/datasets.py` |
| New tests | `tests/security/` (3 files), `tests/unit/research/test_compare.py`, `tests/unit/test_documented_commands.py`, `tests/unit/github/app/test_service_entry_points.py`, `tests/integration/test_examples.py` |
| New workflows | `.github/workflows/release.yml`; three new jobs in `ci.yml` |
| New documentation | see section 26 |
| New data | datasets 1.2.0 and 1.3.0; 8 new recorded benchmark runs |

## 35. Tests

| Suite | Result |
|---|---|
| Full Python suite | **1,385 passed, 1 skipped** (2m 55s) |
| Security-marked subset | **350 passed** (55s) |
| Dashboard unit tests | 99 passed |
| ruff, `ruff format --check`, `mypy --strict`, `bandit -ll` | clean |

The one skip is a network test that needs `COMMITGUARD_NETWORK_TESTS=1`.

## 36. Release artifacts

None. The workflow that would produce them exists and has never run. No tags, no
releases, nothing on PyPI, no binaries, no container images.

## 37. Deployment instructions

Quick start for local use; `docs/deployment/github-actions.md` for the check;
`docs/deployment/github-app.md` and `production.md` for the service, including what
the operator must provide and the single-instance constraint;
`docs/operations/runbook.md` for operating it.

## 38. Final verification

| Check | Result |
|---|---|
| Full test suite | 1,385 passed, 1 skipped |
| Security regression suite | 350 passed |
| `commitguard reproduce security` | PASS |
| `commitguard reproduce benchmark` | PASS - dataset fingerprint matches, 0 FN / 0 FP |
| Detection benchmark (dataset 1.3.0) | 0 false negatives, 0 false positives, 9,174 cases |
| Benchmark comparison against the Phase 9 baseline | no regression |
| Linux platform validation | 15/15 |
| Lint, format, types, static security analysis | clean |
| Documentation links | all relative links resolve |
| Documented commands | all 34 exist |
| Referenced test files and functions | all exist |

### Not verified

- macOS and Windows CI jobs, including the platform validation and package jobs:
  they need a push to GitHub, which is the maintainer's decision.
- The release workflow, which needs a tag.
- Anything requiring another person: evaluation, review, reproduction, deployment.
