# ADR-006: A mandatory policy can only strengthen enforcement

**Status:** Accepted (Phase 5, extended in Phase 8) · **Applies to:** `policies/mandatory.py`, `governance/`

## Context

Central policy is only meaningful if a repository cannot opt out of it. But a
central policy that can also *loosen* local rules is a way to weaken a repository
from outside, which is a different and worse problem.

## Decision

The mandatory policy is applied after every other configuration layer and takes
the **more restrictive** of the two actions. It always enables the policies it
names. `enabled: false` in a mandatory policy is a configuration error, not a
silently ignored field.

## Consequences

Good:

- The direction of trust is fixed: central makes things stricter, local can only
  go further in the same direction.
- It is provable, not just tested by example: a property over generated
  repository and mandatory configurations asserts the effective action is never
  weaker than either input.

Bad:

- An organization cannot grant a blanket exemption through the mandatory policy;
  exemptions must go through scoped, expiring exceptions with approval - more
  process, deliberately.

## Enforcement

`tests/security/test_properties.py`, the `mandatory-policy-floor` experiment, and
`examples/organization-policy/`.
