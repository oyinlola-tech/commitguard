# Phase 7: Operational features

**Completed:** 2026-09-15

## Objective

Make the system usable when things go wrong or move fast.

## Implementation

Notifications (in-app, e-mail, signed webhooks) with deduplication, merge queue handling, check re-run semantics, policy version history and audited rollback, retention and purge tasks.

## Evidence

docs/notifications.md, docs/merge-queue.md, docs/recovery.md

## Tests

Integration tests for merge groups, re-runs, notification delivery and rollback.

## Results

Stale re-runs ignored; merge group commits validated; rollback is a new audited version.

## Limitations

Notification delivery under `commitguard github serve` was silently disabled - found and fixed in Phase 10.
