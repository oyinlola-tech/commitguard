# Local deployment: Git hooks

> Status: **Implemented.** The hooks and `commitguard doctor` are covered by the
> test suite, including real `git commit` and `git push` runs on Linux. The CI
> workflow is configured to run the suite on Ubuntu, macOS and Windows.

```text
Developer ──git commit──▶ pre-commit, commit-msg ─┐
Developer ──git push────▶ pre-push ───────────────┼─▶ commitguard hook <name> ─▶ CommitGuard core
                                                  │
                                    exit 0 allow · exit 1 blocked by policy · exit 2 error
```

Hooks give feedback before a commit is created or pushed. They are **not
authoritative**: anyone who controls the clone can skip or remove them. Pair
them with a server-side check ([github-actions.md](github-actions.md) or
[github-app.md](github-app.md)) that branch protection requires.

Full reference: [../git-hooks.md](../git-hooks.md).

## Install

CommitGuard is not on PyPI (the PyPI name `commitguard` belongs to an unrelated
project). Install from source, pinned to a commit:

```bash
pipx install "git+https://github.com/oyinlola-tech/commitguard@<commit-sha>"
commitguard --version        # commitguard 0.1.0.dev0
```

Requirements: Python 3.12+, Git 2.31+.

## Enable the hooks in a repository

```bash
cd my-project
commitguard init        # optional: writes .commitguard.yaml, never overwrites
commitguard install     # pre-commit, commit-msg and pre-push
commitguard doctor      # ends with Status: HEALTHY, DEGRADED or UNHEALTHY
```

- Existing hooks are preserved as `<hook>.pre-commitguard` and chained; they run
  after CommitGuard.
- A `core.hooksPath` outside the repository's Git directory is refused unless
  you pass `--allow-shared-hooks-path`.
- `commitguard install --global` writes template hooks and sets
  `init.templateDir` only if it is unset; existing repositories are not changed.
- The hook embeds the absolute path of the Python interpreter that ran
  `install`. After moving or reinstalling the environment (for example a new
  pipx install), run `commitguard install` again; `doctor` reports the mismatch.

## What is checked

| Hook | Checks |
|---|---|
| `pre-commit` | the pending author and committer identity |
| `commit-msg` | the message and identity (best effort: Git's final cleanup is approximated) |
| `pre-push` | every commit the push would introduce, deduplicated; the authoritative local check |

Configuration comes from built-in defaults, the user configuration
(`$XDG_CONFIG_HOME/commitguard/config.yaml`) and the repository's
`.commitguard.yaml` in the working tree. The `enforcement` section can turn
individual hooks off; `doctor` then reports enforcement as incomplete.

## Exit codes

| Code | Meaning | Git |
|---|---|---|
| 0 | allowed (including warnings), or the hook is disabled in configuration | continues |
| 1 | blocked by policy | stops |
| 2 | error: invalid configuration, Git failure, malformed hook input, too many commits, CommitGuard unavailable | stops |

Errors always block. This is not configurable.

## Security properties

| Property | Local hooks |
|---|---|
| Runs | on the developer's machine, with the developer's permissions |
| Bypass | `git commit --no-verify`, `git push --no-verify`, deleting hooks, setting `core.hooksPath`, pushing from another clone, editing `.commitguard.yaml` |
| Tamper visibility | `commitguard doctor` reports missing, modified, foreign or non-executable hooks and disabled enforcement; it cannot prevent them |
| Network | none |
| Credentials | none |
| Merge prevention | none |

Tests assert that `--no-verify` bypasses the hooks, so the documented model stays
accurate.

## Remove

```bash
commitguard uninstall            # removes only CommitGuard's managed blocks, restores chained hooks
commitguard uninstall --global
```

## Troubleshooting

See [troubleshooting.md](troubleshooting.md#local-hooks-and-the-cli).
