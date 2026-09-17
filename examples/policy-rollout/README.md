# Policy rollout: change enforcement in stages

What this shows: making a policy stricter across many repositories without
breaking everyone at once.

**Status: experimental** (organization governance).

## The stages

```text
draft ──▶ simulate ──▶ approve ──▶ pilot ──▶ staged rollout ──▶ complete
                                     │
                                     └─▶ pause / cancel / roll back
```

1. **Draft** the change (for example `bot_identity`: warn -> block).
2. **Simulate** it against recorded scans: how many past commits would have been
   blocked, and in which repositories.
3. **Approve** it - the approver must not be the author.
4. **Pilot** it on a few repositories.
5. **Roll out** in waves, watching the blocked-scan count.
6. **Roll back** if needed: a rollback is a new, audited version, never a silent
   edit; policy versions are immutable.

## Suggested pilot order

| Wave | Repositories | What to watch |
|---|---|---|
| 1 | one low-traffic repository you own | false positives, developer confusion |
| 2 | a repository group (5-10) | blocked scans per day, exception requests |
| 3 | the rest | notification volume, posture |

Report-only first: set the policy to `warn` and read the findings before you set
it to `block`. That is what a pilot is for
([docs/community/pilot-program.md](../../docs/community/pilot-program.md)).

Details: [docs/policy-rollouts.md](../../docs/policy-rollouts.md),
[docs/policy-simulation.md](../../docs/policy-simulation.md).
