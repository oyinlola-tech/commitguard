# ADR-003: Every failure path blocks

**Status:** Accepted (Phase 1) · **Applies to:** everything

## Context

A security check that cannot run has two options: assume the best and let work
continue, or assume the worst and stop it. The first is more pleasant and is the
reason tools get trusted when they are not working.

## Decision

Anything that prevents a complete evaluation results in a block or an error,
never an allow:

- a detector that raises becomes a recorded failure, and failures block;
- a finding whose rule has no policy blocks;
- invalid configuration or rules exit **2**, and hooks block on exit 2;
- the GitHub check fails the job rather than passing it;
- the App publishes conclusion `error`, never `success`;
- the dashboard shows `ERROR`, `STALE`, `UNKNOWN`, `NOT CONFIGURED` or
  `DISCONNECTED` rather than a green tick.

## Consequences

Good:

- The only way to get a pass is a completed evaluation that found nothing.
- Outages become visible instead of becoming silent approvals - the four
  reliability experiments exist to prove exactly this.

Bad:

- A CommitGuard bug can block legitimate work. That is the deliberate trade, and
  it makes `commitguard uninstall` and clear error messages part of the safety
  story.
- Users occasionally see exit 2 for their own broken YAML and blame the tool.

## Enforcement

`tests/security/test_properties.py` (generated exception types, every policy
action, always BLOCK), the reliability experiments, and the exit-code tests.
