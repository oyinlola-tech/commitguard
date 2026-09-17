# Architecture decision records

Decisions that shaped CommitGuard, why they were made, and what they cost. A
record is written when a decision was genuinely contested - not for every choice.

| ADR | Decision | Status |
|---|---|---|
| [001](001-detection-separate-from-policy.md) | Detection is separate from policy | Accepted |
| [002](002-rules-as-data.md) | Detection rules are data, not code | Accepted |
| [003](003-fail-closed.md) | Every failure path blocks | Accepted |
| [004](004-trusted-policy-source.md) | Policy comes from the base commit | Accepted |
| [005](005-no-regex-trailer-parser.md) | The trailer parser uses no regular expressions | Accepted |
| [006](006-mandatory-policy-only-strengthens.md) | A mandatory policy can only strengthen enforcement | Accepted |
| [007](007-immutable-evidence.md) | Benchmark results and dataset versions are immutable | Accepted |
| [008](008-not-published-to-pypi.md) | CommitGuard is installed from Git, not PyPI | Accepted |

Format: context, decision, consequences (including the bad ones), and how the
decision is enforced in code or tests.
