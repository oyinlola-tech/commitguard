# 05 - Architecture

**IMPLEMENTED.** Full design: [docs/research/commitguard-architecture.md](../docs/research/commitguard-architecture.md).
Decisions and their costs: [docs/adr/](../docs/adr/).

```text
          Organization  ──▶ central policy, groups, exceptions, rollouts
                │
        Effective policy  ──▶ resolved per repository, mandatory floor applied last
                │
        CommitGuard core  ──▶ detection engine  +  policy engine   (pure, deterministic)
                │
   ┌────────────┼────────────┐
Git hooks   GitHub Actions   GitHub App
(advisory)  (authoritative)  (authoritative, central)
   │             │                │
   └─────────────┴────────────────┘
                │
     Findings · Audit · Notifications · Dashboard
```

## The four decisions that matter

**One engine, three delivery layers.** The same detection and policy code runs in
the hook, the Action and the service, so the three layers cannot disagree about
what a commit means - only about how much authority they have.

**Detection is separate from policy.** Detectors report evidence; policy decides.
A repository that permits AI attribution still gets findings, so the provenance
record survives the policy choice - and detection accuracy can be measured
independently of any policy. ([ADR-001](../docs/adr/001-detection-separate-from-policy.md))

**Trust boundaries are explicit.** Repository content is untrusted; policy comes
from the base commit; rules come from the installed package; the workflow installs
the scanner from a trusted commit; webhooks are verified before anything else;
GitHub remains the authority on who may see what.
([ADR-004](../docs/adr/004-trusted-policy-source.md))

**Central policy can only strengthen.** A mandatory floor is applied after every
other layer and takes the more restrictive action; `enabled: false` in a mandatory
policy is an error. **TESTED** as a property over generated configurations.
([ADR-006](../docs/adr/006-mandatory-policy-only-strengthens.md))

## Properties the design guarantees

| Property | How it is established |
|---|---|
| Decisions are deterministic | **TESTED** - two analyzers, identical reports |
| Detector order cannot change a decision | **TESTED** - shuffled registration |
| A failing detector blocks | **TESTED** - five exception types, every policy action |
| A finding with no policy blocks | **TESTED** |
| A mandatory floor is never weakened | **TESTED** - generated configurations |
| A pull request cannot weaken its own judgement | **OBSERVED** - recorded experiment |
| A scan that cannot complete never reports success | **OBSERVED** - four reliability experiments |

## Engineering constraints accepted

SQLite and an in-process queue mean **one service instance per database**. That is
adequate for the scale tested and is stated in the deployment documentation rather
than implied away. Horizontal scale is **PLANNED**, not built.
