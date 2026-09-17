# `commitguard benchmark` and `commitguard report`

Reproducible measurements. Benchmarks never need credentials and never use the
network.

```text
commitguard benchmark dataset --write DIR [--dataset-version V]
commitguard benchmark detection [--dataset-version V] [--dataset DIR] [--verbose]
commitguard benchmark performance [--quick]
commitguard benchmark hooks [--repetitions N]
commitguard benchmark repository [--sizes 100,1000,10000]
commitguard benchmark platform
```

Common options: `--json`, `--output FILE`, `--record DIR`.

| Benchmark | Measures |
|---|---|
| `detection` | precision, recall, false positive/negative rates against the labelled dataset |
| `performance` | latency percentiles, throughput, commit-size scaling, memory |
| `hooks` | the real overhead a hook adds to `git commit` and `git push` |
| `repository` | scanning histories of 100 to 100,000 commits |
| `platform` | that local enforcement actually works on this operating system |

## Recording results

`--record benchmarks/results` writes a new result file and refreshes the index.
**Results are immutable**: a run is never overwritten, so an improvement between
versions stays visible and a regression cannot be hidden. Each result carries a
manifest: CommitGuard, Python and Git versions, the source revision (and whether
the tree was dirty), OS, CPU, memory, rule and policy fingerprints, the dataset
version and fingerprint, and the command.

## Exit codes

| Code | When |
|---|---|
| `0` | the benchmark ran and every correctness check held |
| `1` | it ran, but a correctness check failed (a detection mismatch, a hook that did not enforce, a platform check that failed) |
| `2` | error |

A benchmark that cannot measure something reports it as such; it never
substitutes an estimate.

## `commitguard report security`

```text
commitguard report security [--results DIR] [--evidence DIR] [--output DIR]
```

Assembles `security-report.json`, `security-report.md` and `benchmark-report.md`
from recorded results and from evidence written by the test suites. It computes
nothing itself: every line is labelled **Measured**, **Tested**, **Observed**,
**Expected** or **Not tested**, and anything without evidence is reported as
*Not tested* rather than omitted.
