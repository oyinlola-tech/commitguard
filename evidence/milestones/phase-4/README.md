# Phase 4: GitHub Actions enforcement

**Completed:** 2026-09-15

## Objective

Server-side validation that a contributor cannot weaken.

## Implementation

commitguard ci github for pull_request, merge_group and push; policy read from the base commit; rules from the installed package; a composite action.yml with SHA-pinned actions and hash-pinned dependencies; annotations, job summary and JSON report.

## Evidence

docs/github-enforcement.md

## Tests

Integration tests including command-injection attempts through branch names.

## Results

A pull request that disables, allows or deletes the policy still fails the check.

## Limitations

The check only blocks merges when branch protection requires it, which CommitGuard cannot configure or verify.
