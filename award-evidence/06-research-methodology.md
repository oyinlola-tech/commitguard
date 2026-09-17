# 06 - Research methodology

Full version: [docs/research/methodology.md](../docs/research/methodology.md).

The question this methodology answers: *how do you measure a security tool you
wrote yourself without flattering it?*

## The five rules

**1. Labels come from the requirement, not the implementation.** Each of the 9,174
dataset cases states the decision the policy **must** reach, derived from the
documented semantics and, for adversarial cases, from the security requirement
("disguised attribution is still attribution"). A mismatch is a failure, never a
labelling opportunity.

**2. Datasets only grow.** Four versions; each rebuilds to a fixed fingerprint, so
an old result stays meaningful. New cases go into a new version.

**3. Results are immutable.** Written once, never overwritten. **13 detection runs
are published, four of which failed.**

**4. The failing run is recorded before the fix exists.** When a benchmark exposes
a bug, the failure is recorded first. This is why the three bypasses are
verifiable rather than merely described.

**5. Experiments record what happened.** Security tests state `expected`, record
`observed`, and classify the outcome as `prevented`, `detected`, `bypassed` or
`not_applicable`. Seven local bypasses are recorded as succeeding.

## Self-correction in practice

Two experiments initially passed **for the wrong reason**: one used invalid
delivery identifiers, so a forged webhook was rejected before signature checking
was reached; another was defeated by a cached token, so a permission revocation
looked enforced when it was not. Both were found by inspecting *why* they passed,
and both were fixed in the experiment rather than the assertion.

That is the difference between a test suite and evidence.

## Labelling in reports

Every line of the generated security report is **Measured**, **Tested**,
**Observed**, **Expected** or **Not tested**, and a section with no evidence prints
*Not tested* rather than being omitted.

## Stated threats to validity

- The author wrote both the software and its evaluation. **No independent
  evaluation exists.**
- Headline accuracy is measured after fixing what the dataset exposed.
- The dataset is synthetic; it cannot estimate real-world frequencies.
- Most timings come from one machine, some recorded while it was busy - stated
  next to the numbers.
- GitHub behaviour, including branch protection, is **modelled**, not exercised
  against github.com.
