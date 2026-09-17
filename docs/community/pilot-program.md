# Running a pilot

A pilot is a real team using CommitGuard on real repositories, with consent, on
purpose, and measured. It is the difference between "it works" and "it worked for
someone".

**No pilots have been run yet.** This page is the process, ready for the first one.

## Principles

- **Consent first.** Every developer affected should know it is happening, what
  it checks, and how to get an exception.
- **Small.** One repository, or a small group. Not the whole organization.
- **Report-only first.** Nothing blocks until the findings have been read.
- **Reversible.** Every stage can be rolled back; policy versions are immutable
  and a rollback is an audited new version.

## Stages

```text
Observe ─▶ Report-only ─▶ Evaluate findings ─▶ Tune policy ─▶ Limited enforcement ─▶ Evaluate ─▶ Broader enforcement
```

| Stage | How | What to decide before moving on |
|---|---|---|
| **Observe** | `commitguard scan origin/main~500..origin/main` on a clone | How much attribution is already in the history? Any false positives? |
| **Report-only** | Set every policy to `warn` | Are the findings correct? How many per week? |
| **Evaluate** | Read every finding | Which are genuine, which are noise? |
| **Tune** | Adjust policy, or add a rule; request exceptions for known automation | Is the rule the team actually wants? |
| **Limited enforcement** | `block` on one repository, with the GitHub check required | Do developers understand the message? How long does remediation take? |
| **Evaluate** | Review blocked scans and exceptions | Any surprise? Any workaround being used? |
| **Broader** | Staged rollout to a group, then the rest | Watch blocked scans per day and exception requests |

For the organization features (groups, staged rollouts, scoped exceptions,
simulation against recorded scans), see
[../policy-rollouts.md](../policy-rollouts.md) and
[../policy-simulation.md](../policy-simulation.md). Simulation is the honest way
to answer "what would this have blocked last month?" before enforcing it.

## Metrics to record

| Metric | Where it comes from |
|---|---|
| Commits scanned | Dashboard overview, or the `scans` API |
| Violations found | Violations view, by rule |
| False positives | **Manual**: a human must judge each finding |
| False negatives | **Manual and partial**: only discoverable by sampling history |
| Scan latency | Recorded per scan; `commitguard benchmark` for local numbers |
| Remediation time | First blocked scan to first passing scan on the same branch |
| Bypass attempts | `--no-verify` commits that the server-side check then catches |
| Policy changes | Audit log |
| Exceptions | Exceptions view: how many, scope, expiry |
| Incidents | Anything that surprised you, written down |

Do not collect what you do not need. Names of individual developers are not
required for any of the above; counts per repository are enough.

## Developer experience

Ask three questions after two weeks, in one message:

1. Did CommitGuard block something it should not have?
2. When it blocked you, did you know what to do?
3. Did it slow you down noticeably? (The hook costs about a second per commit -
   measured, see the benchmark report.)

A pilot of one team is not a representative sample of developers, and any write-up
must say so.

## Writing it up

[case-study-template.md](case-study-template.md), published only with permission,
with repository names and confidential details removed unless explicitly allowed.
