# 11 - External validation

## Status: none

**No external evaluations have been recorded.** No independent party has
installed, tested, reviewed or reproduced CommitGuard.

This page exists to say that plainly. It is the largest gap in the evidence
package, and inventing anything here would undermine the parts that are real.

## What exists to make it possible

| | State |
|---|---|
| A structured evaluation programme with a 10-step script and expected results | **IMPLEMENTED** - [docs/community/external-evaluation.md](../docs/community/external-evaluation.md) |
| An evaluation feedback issue form | **IMPLEMENTED** |
| A reproduction command that needs no credentials | **IMPLEMENTED** - `commitguard reproduce benchmark` |
| A security review orientation guide | **IMPLEMENTED** - [docs/security/review-guide.md](../docs/security/review-guide.md) |
| A place to record results, with consent rules | **IMPLEMENTED, empty** - `evidence/external-validation/` |
| Private vulnerability reporting | **IMPLEMENTED** |

## What an external evaluation would produce

Environment, tasks performed, issues discovered, feedback in the evaluator's own
words, and a result - published only with their permission, and with the option to
be anonymous.

## Why the reproduction path matters more than the claim

Anyone can check the central accuracy claim in about a minute, without credentials
and without trusting the author:

```bash
git clone https://github.com/oyinlola-tech/commitguard && cd commitguard
python -m pip install -e ".[dev]"
commitguard reproduce benchmark --results benchmarks/results
```

It rebuilds the 9,174-case dataset, verifies its fingerprint against the published
files, and re-measures detection. The failing historical runs are in the same
repository, so the claim "three bypasses were found and fixed" is checkable
too.

Until someone does that and says so, this section stays empty.
