# Policy engine

> Status: **implemented** and used by `commitguard scan` / `commitguard check`.

## Detector finds, policy decides

A detector never blocks anything. It reports a `Finding` for a rule such as
`ai_coauthor`; the effective policy for that rule decides the action.

| Action | Meaning | Exit code contribution |
|---|---|---|
| `allow` | finding is recorded and shown, operation may proceed | 0 |
| `warn` | operation may proceed, warning shown | 0 |
| `block` | operation must not proceed; explanation and remediation shown | 1 |

## Built-in policies

| Policy | Default | Emitted by |
|---|---|---|
| `ai_coauthor` | block | `coauthor` |
| `ai_identity` | block | `identity` |
| `ai_trailer` | block | `trailer` |
| `malformed_trailer` | warn | `trailer` |
| `bot_identity` | warn | `bot` |

```yaml
version: 1
policies:
  ai_coauthor:
    enabled: true
    action: block
  ai_identity:
    enabled: true
    action: block
  bot_identity:
    enabled: true
    action: warn
```

## Effective policy

`build_policy_set(*layers)` starts from the built-in defaults and applies each
configuration layer in precedence order, field by field (see
[configuration.md](configuration.md)). Omitted policies and fields keep the
value from the layer below and ultimately the secure default.

## Evaluation (`policies/evaluator.py`)

`PolicyEvaluator(policies).evaluate(detection_result) -> Decision`

| Situation | Action |
|---|---|
| finding, policy enabled | the policy's action |
| finding, policy `enabled: false` | allow (still explained) |
| finding for a rule with no policy | **block** (fail closed) |
| detector failure (exception, invalid output, trailer flood) | **block** (incomplete analysis) |
| no findings, no failures | allow |

When every rule of a detector is disabled, the analyzer does not run that
detector at all, so a disabled policy can never block through a detector failure.

### Precedence

**block > warn > allow.** The overall decision is the most restrictive action
of any finding or failure. It is computed from the set of actions, so it does
not depend on detector execution order or finding order.

| Findings | Decision |
|---|---|
| none | ALLOW |
| bot_identity (warn) | WARN |
| ai_coauthor (block) | BLOCK |
| bot_identity (warn) + ai_coauthor (block) | BLOCK |
| ai_coauthor (block) + ai_identity (block) | BLOCK |

For multiple commits (`scan a..b`), the report's action is the most restrictive
commit decision.

## Explanations and reports

Every finding and failure yields an `Explanation` (action, reason, policy ID).
The service layer turns detection results and decisions into a `CommitReport`
(per commit) and `ScanReport` (per invocation): the stable contract for
`--format json` and for the future audit log. Reports include timestamp,
repository path, commit SHAs, detector, rule, evidence, action and reason —
never file contents or full commit messages.

## Future

- confidence/severity thresholds per policy;
- scoped exceptions (paths, branches, allow-listed identities with expiry);
- organisation baselines that repositories may tighten but not loosen.
