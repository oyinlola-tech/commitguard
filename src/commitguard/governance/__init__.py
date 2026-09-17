"""Organization governance: central policy, groups, onboarding, exceptions and rollouts.

::

    Organization (GitHub account)            settings.py    baseline, approval, limits
      ├── repositories                       inventory.py   discovery, onboarding, mode
      ├── repository groups                  groups.py
      ├── policies (organization / group /   controlplane/policies.py  immutable versions
      │   repository targets)                workflow.py    draft -> approve -> publish
      │                                      simulation.py  historical, read-only
      │                                      rollouts.py    pilot -> stages -> active
      ├── exceptions                         exceptions.py  scoped, expiring, approved
      ├── scan schedules                     schedules.py
      ├── bulk operations                    bulk.py        background, bounded, idempotent
      └── posture, reports, analytics        posture.py
                        │
                        ▼
      GovernanceResolver (resolver.py) -> GovernanceInputs -> resolve_policy (pure)
                        │
                        ▼
      ScanService -> PolicyEvaluator -> decision -> GitHub check

The dashboard never makes a security decision: governance decides *which*
configuration applies, the policy engine decides what a finding means, and
GitHub enforces the check.
"""
