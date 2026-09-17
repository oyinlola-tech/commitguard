# Policy simulation

Before publishing a policy change, an administrator can ask: *what would this
draft have decided on the scans we already ran?*

```text
Draft policy (organization, group or repository target)
     ↓
Policy resolver: for every repository in the target's scope
     current governance inputs       → effective policy A
     the same inputs with the draft  → effective policy B
     ↓
Historical scan data: the latest completed scan of each pull request, branch or
merge group in the period, with the findings it recorded
     ↓
Policy evaluation: the real PolicyEvaluator, under A and under B
     ↓
Simulation result (SIMULATION, an estimate)
```

## What a simulation never does

A simulation is read-only analysis. It never:

* publishes or changes a policy version, a draft's state or the effective policy;
* creates, updates or re-runs a GitHub check;
* changes violations, exposures, notifications or repository state;
* runs detectors or fetches Git data - it re-evaluates the findings the original
  scans recorded.

`test_simulation_is_read_only_and_uses_recorded_findings` compares policy
versions, the draft, GitHub check runs and violations before and after.

## One evaluator

Recorded findings are rebuilt as `Finding` objects (rule, detector, severity,
confidence, evidence) and evaluated by `commitguard.policies.evaluator.PolicyEvaluator`,
the evaluator every scan uses. There is no simulation-specific decision logic;
an architecture test allows the evaluator import only in
`commitguard.governance.simulation` within the governance package.

The repository's own `.commitguard.yaml` is part of the effective policy.
Phase 8 scans record the configuration overrides they read (for example
`{"ai_coauthor": {"action": "allow"}}`), so the simulation resolves each scan
exactly as the scan did, with only the draft changed.

## Result

```text
SIMULATION - an estimate from recorded scans in the selected period, not the current
security state and not a prediction of future commits.

Repositories analyzed:      42        Scans analyzed:   318     Findings analyzed: 1,204
Current policy:             v12       Draft policy:     v13 (not published)

Projected changes (findings)          Projected changes (scans)
  New blocks            7               Newly blocked            3
  New warnings         12               No longer blocked        1
  No longer blocked     4
  Unchanged         1,181

Most affected repositories: payments-api (5), web-console (3), mobile-app (2)
```

| Field | Meaning |
|---|---|
| `new_blocks` | findings that would become `block` |
| `new_warnings` | findings that would go from `allow` to `warn` |
| `no_longer_blocked` | findings that would no longer be `block` (or go from warn to allow) |
| `unchanged` | findings with the same action |
| `scans_newly_blocked`, `scans_no_longer_blocked` | whole-scan outcome changes |
| `repositories_analyzed`, `repositories_without_data` | coverage of the scope |
| `scans_assumed_defaults` | scans recorded before Phase 8, without their repository configuration: built-in defaults were assumed for it |
| `truncated` | the bounds were reached; the remaining repositories were not analyzed |
| `most_affected` | the ten repositories with the most changed findings (names hidden when the viewer cannot see a repository) |

## Honest limits

It is an **estimate**:

* it covers commits that were scanned in the period (7 to 90 days), not future
  commits;
* scans older than Phase 8 did not record the repository configuration; they are
  counted in `scans_assumed_defaults`;
* repositories with no scan in the period are "without data", not "unchanged";
* organization rules (identity data) are those of the recorded findings: adding an
  identity cannot be simulated, because nothing was detected for it.

## Running simulations

`POST /api/v1/policy-drafts/{draft_id}/simulations` (`policies:write`) with
`period_days` (default 30, 1-90) and optionally `repository_ids`. The request is
accepted with **202** and the simulation is queued; the maintenance loop claims
it with a 10-minute lease (a crashed run is retried) and stores the result.
`GET /api/v1/simulations/{id}` returns its state (`queued`, `running`,
`completed`, `failed`).

Bounds: at most 3 queued or running simulations per organization, 5,000 scans
and 50,000 findings per simulation (then `truncated`). With 10,000 repositories
and 100,000 scans, a bounded simulation (5,000 scans) took about 7 seconds in the
background on a development machine.

Completed simulations are audited (`policy_simulated`, with the projected counts).
