# ADR-007: Benchmark results and dataset versions are immutable

**Status:** Accepted (Phase 9) · **Applies to:** `research/`, `benchmarks/`

## Context

The temptation with a benchmark you own is to re-run it until it looks good, and
to adjust a label when a case is "obviously fine". Both are invisible in the final
number.

## Decision

- A result is written once, to a timestamped file, and never overwritten.
- Dataset versions only add cases; an old version always rebuilds to the same
  fingerprint.
- Labels come from documented semantics and security requirements, never from the
  implementation's output.
- When a benchmark exposes a bug, the failing run is recorded **before** the fix.

## Consequences

Good:

- Thirteen detection runs exist, four of which failed, published together. The
  improvement is verifiable rather than asserted.
- A regression cannot be hidden by re-running.
- The evidence survives the author's future opinions about it.

Bad:

- The results directory grows and needs explaining to newcomers ("why is there a
  failing run in here?" - because that is the point).
- A genuinely mislabelled case takes a new dataset version to correct, which is
  deliberate friction.

## Enforcement

`write_result` raises rather than overwriting; `commitguard reproduce benchmark`
compares the rebuilt dataset with the published fingerprint.
