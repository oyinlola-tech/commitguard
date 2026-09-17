# Incident response (maintainer)

The procedures live in two places, by audience:

| Situation | Page |
|---|---|
| A security bug, a compromised key, a leaked credential, a bad release, a false security state | [../security/incident-response.md](../security/incident-response.md) |
| A deployment that is misbehaving: webhooks failing, scans stale, the database unavailable, propagation stuck | [../operations/runbook.md](../operations/runbook.md) |
| A vulnerability report arriving | [../security/vulnerability-response.md](../security/vulnerability-response.md) |

## Maintainer-specific duties

- **Decide severity early.** Critical and High are anything that lets a violating
  commit get a passing check, or exposes another tenant's data.
- **Write the advisory before the fix ships**, and publish it with the fix.
- **Tell operators what to rotate**, in the advisory, in plain terms.
- **Add the regression test.** No exceptions: an incident without a test is an
  incident that will recur.
- **Update the threat model** if the incident revealed a surface it did not cover,
  and add a row to the version history.
