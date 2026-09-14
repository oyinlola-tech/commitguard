# Policy engine

> Status: implemented (models, defaults, configuration merge, evaluator). It is
> not yet wired into `scan`/`check`, because the detectors that feed it are
> Phase 2.

## Detector finds, policy decides

A detector never blocks anything. It reports a `Finding` for a rule such as
`ai_coauthor`. The policy for that rule decides the action:

| Action | Meaning |
|---|---|
| `allow` | Finding is recorded and shown, operation proceeds |
| `warn` | Operation proceeds with a visible warning |
| `block` | Operation is refused (non-zero exit), with explanation and remediation |

## Built-in policies

| Policy | Default | Emitted by |
|---|---|---|
| `ai_coauthor` | block | `coauthor` |
| `ai_identity` | block | `identity` |
| `ai_trailer` | block | `trailer` |
| `malformed_trailer` | warn | `trailer` |
| `bot_identity` | warn | `bot` |

## Effective policy

`build_policy_set(config)` starts from the built-in defaults and applies each
configured override field by field. **Omitted policies keep their secure
defaults**; weakening a policy always requires an explicit entry.

## Evaluation rules

`PolicyEvaluator(policies).evaluate(scan_result) -> Decision`

| Situation | Action |
|---|---|
| finding, policy enabled | the policy's action |
| finding, policy `enabled: false` | allow (still listed in explanations) |
| finding for a rule with no policy | **block** (fail closed) |
| detector failure (exception, invalid output) | **block** (incomplete scan) |
| no findings, no failures | allow |

The overall decision is the most restrictive action. Every contributing
finding or failure produces an `Explanation` (action, reason, policy ID), so a
BLOCK can always be explained.

## Future

- severity thresholds per policy;
- scoped policies (branches, paths, specific identities allow-listed with expiry);
- organisation policy inheritance, where a repository may tighten but not
  loosen an organisation baseline.
