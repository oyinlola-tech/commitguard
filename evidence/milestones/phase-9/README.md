# Phase 9: Research, benchmarking and validation

**Completed:** 2026-09-17

## Objective

Turn claims into measurements that someone else can reproduce.

## Implementation

A labelled dataset (9,115 cases at first), detection/performance/hooks/repository/platform benchmarks, immutable results with full environment manifests, and 13 security experiments recording observed outcomes.

## Evidence

docs/research/ (written in Phase 10)

## Tests

The benchmarks themselves, plus the security experiment suites.

## Results

Found and fixed a detection bypass (replacement character), two false positives, and a denial-of-service defect (10 MB message: 4.8 s to 0.75 s). Measured hook overhead, 100k-commit scanning and Linux platform validation.

## Limitations

All measurements are the author's own, on one machine; no external reproduction.
