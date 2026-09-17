# `commitguard install`, `uninstall`, `hook`

## `commitguard install`

Install the `pre-commit`, `commit-msg` and `pre-push` hooks.

```text
commitguard install [--hook NAME]... [--global] [--allow-shared-hooks-path]
```

| Option | Meaning |
|---|---|
| `--hook pre-commit\|commit-msg\|pre-push` | Install only this hook (repeatable) |
| `--global` | Install into a Git template directory, so repositories created or cloned **afterwards** get the hooks |
| `--allow-shared-hooks-path` | Allow installing when `core.hooksPath` points outside this repository's Git directory |

What each hook checks:

| Hook | Checks |
|---|---|
| `pre-commit` | the pending author and committer identity |
| `commit-msg` | the message after Git's cleanup, plus the pending identity |
| `pre-push` | every commit the push would introduce |

An existing hook is preserved as `<hook>.pre-commitguard` and still runs.
CommitGuard's block is delimited by markers and checksummed, so `doctor` can tell
you if it was edited.

`--global` only affects **new** repositories: Git copies a template directory at
`git init` and `git clone`. Existing clones need `commitguard install`.

## `commitguard uninstall`

Removes only CommitGuard's block and restores a preserved hook.

```text
commitguard uninstall [--hook NAME]... [--global]
```

## `commitguard hook <name>`

```text
commitguard hook pre-commit
commitguard hook commit-msg <message-file>
commitguard hook pre-push          # ref updates are read from stdin
```

These are what the installed hooks call; you should not need to run them by hand.
They fail closed: if configuration or rules cannot be loaded, the hook blocks
(exit 2) rather than letting the commit through.

## Exit codes

| Code | When |
|---|---|
| `0` | installed, updated, unchanged or uninstalled |
| `1` | (`hook` only) the commit or push is blocked by policy |
| `2` | not a repository, hooks directory unusable, a foreign `core.hooksPath` without `--allow-shared-hooks-path`, or a failure to evaluate |

## Limitation

Local hooks are advisory: `git commit --no-verify`, `git push --no-verify`,
deleting the hook files or pointing `core.hooksPath` elsewhere all skip them, and
a fresh clone has no hooks at all. Every one of those is a recorded experiment
(`tests/integration/github/security/test_bypass_resistance.py`), and every one is
caught by the server-side check. Use both layers.
