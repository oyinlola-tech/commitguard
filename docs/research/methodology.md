# Methodology

How anything in these pages is measured, and the rules that stop the measurements
from flattering the implementation.

## 1. Labels come from the requirement, not the code

Every case in the labelled dataset states the decision the built-in policy
**must** reach, derived from the documented detection semantics
([../detection-engine.md](../detection-engine.md)) and, for adversarial cases,
from the security requirement: *disguised attribution is still attribution*.

Labels are never changed to match what the implementation does. A mismatch is a
measured failure. This is the single most important rule here, and it is what
turned three quiet bypasses into recorded failures.

## 2. Datasets are versioned and only ever grow

| Version | Cases | Added |
|---|---|---|
| 1.0.0 | 9,115 | the original set: clean, violations, variations, malformed, adversarial, generated |
| 1.1.0 | 9,142 | leading-character and Unicode evasions, plus quoted and bulleted **human** trailers as false-positive probes |
| 1.2.0 | 9,157 | Unicode letters and numbers before the key; numbered human trailers and non-ASCII prose |
| 1.3.0 | 9,174 | default-ignorable characters in keys and aliases; emoji variation selectors and a Hangul name |

An older version always rebuilds to the same fingerprint, so an old result stays
meaningful. New cases go into a new version; no case is edited to make a run pass.

Each version's cases were written **before** the run that exposed the
corresponding bug, and the failing run was recorded before the fix existed.

## 3. Results are immutable

`benchmarks/results/raw/<benchmark>/<UTC timestamp>_<version>.json`, written once
and never overwritten. Thirteen detection runs are kept, including the three that
failed. An improvement is visible because the worse number is still there.

Every result carries a manifest: CommitGuard, Python and Git versions, the source
revision and whether the working tree was dirty, OS, CPU model and count, memory,
rule and policy fingerprints, dataset version and fingerprint, and the command
that produced it.

## 4. Experiments record what happened, not what should happen

A security experiment is a test that states `expected`, records `observed`, and
classifies the `outcome` as one of:

| Outcome | Meaning |
|---|---|
| `prevented` | the attack did not achieve its goal at this layer |
| `detected` | it got through this layer, and a later layer reported or blocked it |
| `bypassed` | it succeeded - a documented limitation |
| `not_applicable` | the scenario does not apply |

The observed values are built from what the test measured (exit codes, HTTP
statuses, check conclusions), not from prose. Thirteen experiments are recorded in
`evidence/security/experiments.jsonl` by
`commitguard reproduce security --evidence-dir <dir>`.

Two experiments initially passed for the wrong reason - an invalid delivery ID
made a forged webhook fail before signature checking, and a cached token made a
permission revocation look enforced. Both were **fixed in the experiment**, not
in the assertion. That is the failure mode this method is designed to catch.

## 5. Timing method

- Wall-clock and CPU time with `time.perf_counter` / `time.process_time`;
  percentiles by nearest rank.
- Allocation profiling runs in a **separate pass**: measuring allocations with
  `tracemalloc` inflated latency by roughly 2.5x, so the two are never measured
  together.
- Hook overhead is measured by running real `git commit` and `git push`
  operations in a real repository, with and without hooks, 20 times each. The
  cost of starting a Python interpreter is deliberately *included*: it is what the
  developer waits for.
- Repository scaling builds histories with `git fast-import` and measures the
  three phases separately (listing, reading, analysing).

Timings depend on the machine and on what else it is doing. The machine, and
whether it was busy, is recorded with the result.

## 6. What is tested versus what is measured

The reports label every statement:

| Label | Means |
|---|---|
| **Measured** | a number from a recorded benchmark run on a stated machine |
| **Tested** | an automated test asserts it |
| **Observed** | an experiment recorded what happened, including failures |
| **Expected** | design intent, not covered by a test yet |
| **Not tested** | no evidence |

Nothing is labelled by generosity. A section with no evidence says *Not tested*
rather than being left out.

## 7. Threats to validity

- **The benchmarks and the implementation share an author.** Independent
  evaluation is the missing piece; the process for it exists
  ([../community/external-evaluation.md](../community/external-evaluation.md)) and
  nothing has been recorded yet.
- **Detection results are measured after fixing what the dataset exposed.** The
  headline "0 false negatives" is true of the current code on the current
  dataset; it is not a claim that no further bypass exists. The failing runs are
  kept precisely so this is visible.
- **The dataset is synthetic.** It was written to cover documented semantics and
  plausible evasions, not sampled from real repositories, so it cannot estimate
  how often these cases occur in practice.
- **One machine, one OS for most timings.** macOS and Windows numbers come from
  CI when the platform job runs.
- **GitHub behaviour is modelled** in the integration tests by a fake GitHub.
  Branch protection in particular is modelled, not exercised against github.com.
