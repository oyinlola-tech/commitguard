# ADR-001: Detection is separate from policy

**Status:** Accepted (Phase 1) · **Applies to:** `detectors/`, `policies/`, `core/`

## Context

The obvious design is one function: "is this commit allowed?". It is shorter, and
it is what a first version usually looks like. But the two questions inside it are
different: *what does this commit's metadata show* and *is that acceptable here*.

## Decision

Detectors report `Finding`s (rule, severity, confidence, evidence, remediation)
and know nothing about configuration. A separate policy evaluator maps findings to
a `Decision` (allow / warn / block). Neither has side effects.

## Consequences

Good:

- The evidence does not change when the policy does. A repository that permits AI
  attribution still records findings, so the provenance record exists even where
  enforcement is off.
- Detection accuracy can be measured independently of any policy choice - the
  entire labelled dataset depends on this.
- Policy precedence (`block > warn > allow`) is total and order-independent,
  which is testable as a property.

Bad:

- Two concepts to learn instead of one, and users occasionally ask why a finding
  appeared when nothing was blocked.
- More types and more plumbing than a single predicate would need.

## Enforcement

`tests/unit/core/test_engine.py` rejects a detector that emits an undeclared rule,
attributes a finding to another detector, or refers to another commit. Architecture
tests keep `detectors/` from importing `policies/`.
