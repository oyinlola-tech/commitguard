# Research and evidence

CommitGuard is built as a security engineering project, which means the claims
about it have to be measurable and reproducible by someone else. These pages hold
the question, the method, the measurements and the limits.

| Page | Answers |
|---|---|
| [problem-definition.md](problem-definition.md) | What problem is this, and what would count as solving it? |
| [methodology.md](methodology.md) | How is anything here measured, and why is it trustworthy? |
| [detection-evaluation.md](detection-evaluation.md) | How accurate is detection? Precision, recall, and the bypasses found |
| [performance-evaluation.md](performance-evaluation.md) | How fast, at what size, and what does a hook cost a developer? |
| [cross-platform-validation.md](cross-platform-validation.md) | Does local enforcement actually work on each OS? |
| [bypass-resistance.md](bypass-resistance.md) | What happens when someone tries to get around it |
| [policy-tampering.md](policy-tampering.md) | Can a pull request weaken the policy that judges it? |
| [github-enforcement-validation.md](github-enforcement-validation.md) | Does the server-side layer hold? |
| [merge-queue-security.md](merge-queue-security.md) | Re-runs, stale results and merge queues |
| [reliability-evaluation.md](reliability-evaluation.md) | What happens when GitHub, the database or a worker fails |
| [fuzzing.md](fuzzing.md) | Property-based fuzzing, ReDoS, and the invariants |
| [reproducibility.md](reproducibility.md) | How to re-run all of it yourself |
| [limitations.md](limitations.md) | What this does not do, and what the numbers do not mean |
| [innovation.md](innovation.md) | What is actually new here |
| [impact.md](impact.md) | What has measurably changed, with real counts |
| [commitguard-architecture.md](commitguard-architecture.md) | The system, as a design paper |
| [roadmap.md](roadmap.md) | Open research directions |

## The short version

- Detection on the labelled dataset (9,174 cases, version 1.3.0): **0 false
  negatives, 0 false positives**, measured 2026-09-17 - *after* fixing three
  bypasses that these benchmarks found. All thirteen runs, including the failing
  ones, are kept in `benchmarks/results/raw/detection/`.
- **Three detection bypasses and two denial-of-service defects were found by this
  project's own dataset and fuzzers**, not by users. Each has a regression test
  and a dataset version.
- Local hooks are bypassable, and it is measured rather than argued: seven
  documented bypasses, each caught by the server-side check.
- Thirteen security experiments record what *actually happened* - including the
  ones where the attack succeeded against a layer.
- Every measurement here can be re-run with `commitguard reproduce` and
  `commitguard benchmark`.

## Reproducing everything

```bash
git clone https://github.com/oyinlola-tech/commitguard && cd commitguard
python -m pip install -e ".[dev]"
commitguard reproduce all --evidence-dir evidence/security --results benchmarks/results
commitguard report security --results benchmarks/results --evidence evidence/security
```

See [reproducibility.md](reproducibility.md) for what is fixed and what varies.
