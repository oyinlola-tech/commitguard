# Benchmarking (maintainer)

## The rules

1. **Never edit a recorded result.** `benchmarks/results/raw/` is append-only;
   `write_result` refuses to overwrite. A regression must stay visible.
2. **Never relabel a dataset case to make a run pass.** Labels come from the
   documented detection semantics and the security requirement. If a case is
   genuinely mislabelled, that is a documented decision, not a convenience.
3. **Dataset versions only add.** `1.0.0` must always rebuild to the same
   fingerprint. New cases go in a new version.
4. **Record the failing run first.** When a benchmark exposes a bug: write the
   case, record the run that fails, then fix, then record again. Both runs stay.

## Recording

```bash
commitguard benchmark detection --record benchmarks/results
commitguard benchmark performance --record benchmarks/results
commitguard benchmark hooks --repetitions 20 --record benchmarks/results
commitguard benchmark repository --record benchmarks/results
commitguard benchmark platform --record benchmarks/results
```

Each result carries a manifest: versions, source revision and whether the tree was
dirty, OS, CPU, memory, rule and policy fingerprints, dataset version and
fingerprint, and the command. A result recorded from a dirty tree is marked as
such - prefer a clean tree for anything you intend to quote.

**The machine must be quiet.** Timings recorded while other work is running are
still real, but they are not comparable; note it if you cannot avoid it.

## Comparing across versions

The index (`benchmarks/results/processed/index.json`) lists every run with its
version and environment. A meaningful comparison keeps the machine, the dataset
version and the benchmark version fixed, and changes only CommitGuard.

Thresholds for judging a change (documented, not automated):

| Measure | Investigate a change over |
|---|---|
| Detection false negatives or false positives | any increase at all |
| Median detection latency per commit | 20% |
| Large-message (1 MB+) latency | 20% |
| Hook overhead | 25% |
| Peak memory | 25% |

A performance regression alone does not block a release; an unexplained one does.

### Performance thresholds compare across sessions, not across code

`commitguard benchmark compare` judges the latest result against an earlier
recorded one. Those runs happen at different times, and on a laptop the machine
itself moves more than 20% between sessions (CPU frequency scaling, thermal
state, background load). A flagged *performance* regression is therefore a
question, not an answer.

Answer it the way the Phase 10 verification pass did: check the baseline commit
out into a worktree and benchmark it **alternately** with the current code on the
one machine, several pairs each. That removes machine state from the comparison.
In that case a flagged +36% p50 regression turned out to be -2.7% once measured
this way (see
[phase-10-verification-report.md](../evidence/phase-10-verification-report.md),
section 12).

Correctness measures have a 0% threshold and are not affected by machine state:
a new false negative is a real regression whenever it appears.

## Reports

```bash
commitguard reproduce security --evidence-dir evidence/security
commitguard report security --results benchmarks/results --evidence evidence/security
```

The report restates recorded evidence and labels every line Measured, Tested,
Observed, Expected or Not tested. If a section says *Not tested*, the fix is to
run the benchmark, never to reword the report.
