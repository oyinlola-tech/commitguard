# Phase 3: Local Git hook enforcement

**Completed:** 2026-09-15

## Objective

Stop violations on the developer's machine, without destroying anyone's existing hooks.

## Implementation

pre-commit, commit-msg and pre-push hooks; installation per repository or globally via a template directory; chaining of existing hooks; integrity checksums; fail-closed wrappers; commitguard doctor.

## Evidence

docs/git-hooks.md

## Tests

Integration tests driving real git commands in real repositories.

## Results

Violations blocked at commit and push; existing hooks preserved and still run.

## Limitations

Bypassable by design (--no-verify, deleting hooks, core.hooksPath, a fresh clone) - later measured as seven recorded bypasses.
