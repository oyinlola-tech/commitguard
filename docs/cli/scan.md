# `commitguard scan` and `commitguard check`

Both evaluate commits with the same engine. `scan` explains; `check` is for
scripts.

## `commitguard scan`

```text
commitguard scan [revision_range] [--config FILE] [--format text|json] [--max-commits N]
```

| Argument / option | Meaning |
|---|---|
| `revision_range` | A commit (`HEAD`, a SHA, a branch) or a range containing `..` (default: `HEAD`) |
| `--config, -c FILE` | An extra configuration file, applied after the global and repository files |
| `--format, -f text\|json` | Human-readable (default) or a JSON document with `schema_version` |
| `--max-commits N` | Refuse a range selecting more commits than this (default: 1000) |

```bash
commitguard scan                      # HEAD
commitguard scan origin/main..HEAD    # everything a pull request would add
commitguard scan --format json        # for tooling
```

Each finding shows the rule, severity, confidence, the exact evidence, where it
came from (trailer, author, committer) and how to fix it. Commit messages
themselves are never included in reports - only the matched evidence.

The `--max-commits` bound is a safety limit, not a performance one: scanning
100,000 commits is measured in the repository benchmark.

## `commitguard check`

```text
commitguard check [revision_range] [--message-file FILE] [--quiet] [--verbose]
                  [--config FILE] [--format text|json] [--max-commits N]
```

| Option | Meaning |
|---|---|
| `--message-file FILE` | Check a **pending** message that is not a commit yet |
| `--quiet, -q` | Print nothing; use the exit code |
| `--verbose, -v` | Full human-readable evidence |

Default output is one tab-separated line per finding and a summary line:

```text
BLOCK	4f71c92	coauthor	ai_coauthor	Claude <noreply@anthropic.com>
result=BLOCK commits=1 block=1 warn=0 allow=0
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | allowed (no findings, or only `allow`/`warn` findings) |
| `1` | blocked |
| `2` | error: invalid configuration or rules, a Git failure, an unreadable message file, or a range over `--max-commits` |

## Failure cases

- **`revision range selects more than N commits`** - raise `--max-commits`.
- **`invalid configuration`** - exit 2, never 0: an unreadable policy is not an
  absent policy.
- A detector that crashes on a hostile commit produces a **block**, not a pass.
