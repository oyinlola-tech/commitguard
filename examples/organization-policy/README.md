# Organization policy: a floor repositories cannot lower

What this shows: a central policy applied after every local configuration layer.
Repositories can be stricter, never weaker.

## The files

- `mandatory-policy.yaml` - the floor the service applies.
- `repository-attempt.yaml` - a repository trying to allow AI attribution.

## The rule

Configuration layers override each other (built-in defaults -> global -> repository
-> `--config`). The mandatory policy is applied **after** all of them and takes
the more restrictive of the two actions, so:

| Policy | Mandatory | Repository asks for | Effective |
|---|---|---|---|
| `ai_coauthor` | block | allow | **block** |
| `ai_identity` | block | disabled | **block** (and enabled) |
| `bot_identity` | warn | block | **block** (stricter is allowed) |

`enabled: false` in a *mandatory* policy is a configuration error: a mandatory
policy can only enforce.

## Using it

With the GitHub App service:

```bash
export COMMITGUARD_APP_MANDATORY_POLICY_FILE=/etc/commitguard/mandatory-policy.yaml
commitguard github serve
```

With organization governance (dashboard), the same floor is set per organization
or repository group, with versions, approval and audit:
[docs/organization-governance.md](../../docs/organization-governance.md),
[docs/policy-inheritance.md](../../docs/policy-inheritance.md).

This property is checked over generated configurations in
`tests/security/test_properties.py` and observed end to end in the
`mandatory-policy-floor` experiment.
