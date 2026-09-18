# Evidence

Artefacts that support the claims made about CommitGuard. Each has a date, a
CommitGuard version, a source and a way to verify it.

| Directory | Holds | State |
|---|---|---|
| `technical/` | What was built, per phase | Written |
| `security/` | Experiment records, fuzzing and ReDoS evidence from test runs | **Generated** by `commitguard reproduce security --evidence-dir evidence/security` |
| `benchmarks/` | Pointer to the immutable results in `benchmarks/results/` | Written |
| `milestones/` | One factual summary per phase: objective, implementation, evidence, tests, results, limitations | Written |
| `releases/` | Release artefacts and their checksums | **Empty** - no release has been published |
| `external-validation/` | Independent evaluations | **Empty** - none recorded |
| `deployments/` | Voluntarily disclosed deployments | **Empty** - none recorded |
| `contributions/` | External contributions | **Empty** - none received |
| `case-studies/` | Real deployments written up with permission | **Empty** - none exist |
| `timeline/` | Pointer to `docs/evidence/timeline.md` | Written |

Empty directories are kept, with a README explaining what would go in them. An
empty `external-validation/` is itself evidence: it is the honest state of a
project that is three days old.

Phase reports: [Phase 10](../docs/evidence/phase-10-report.md) (adoption and
reproducibility) and [Phase 10 verification](../docs/evidence/phase-10-verification-report.md)
(whole-system verification and repair).

## Verifying the evidence yourself

```bash
commitguard reproduce all --evidence-dir evidence/security --results benchmarks/results
commitguard report security --results benchmarks/results --evidence evidence/security
```

Reports land in `reports/`. Every line is labelled Measured, Tested, Observed,
Expected or Not tested.

## What each artefact carries

| Field | Where it comes from |
|---|---|
| Date | the result manifest, or the file header |
| CommitGuard version | the manifest |
| Source revision, and whether the tree was dirty | the manifest |
| Environment (OS, CPU, memory, Python, Git) | the manifest |
| Dataset version and fingerprint | the manifest |
| Command | the manifest |
| Verification method | `commitguard reproduce`, or the named test |
