# `commitguard init`

Set a repository up for enforcement. Existing files are never overwritten.

```text
commitguard init [--install-hooks] [--github --action-repository OWNER/REPO --action-ref SHA]
                 [--non-interactive]
```

| Option | Meaning |
|---|---|
| `--install-hooks` | Also install the Git hooks, like `commitguard install` |
| `--github` | Also write `.github/workflows/commitguard.yml` |
| `--action-repository OWNER/REPO` | Repository hosting the CommitGuard Action (required with `--github`) |
| `--action-ref <sha>` | Full 40-character commit SHA to pin the Action to (required with `--github`) |
| `--non-interactive` | Never prompt; do exactly what the options say |

## Interactive and non-interactive

With a terminal on both ends, `init` asks whether to add the GitHub check and
whether to install the hooks now. Without a terminal - a script, a CI job, a
pipe - it prompts for nothing and behaves exactly as `--non-interactive`. The
options always win over the prompts.

```bash
commitguard init                       # asks, when run by a person
commitguard init --install-hooks       # writes configuration and installs hooks
commitguard init --non-interactive     # writes configuration only, silently
```

## What it writes

`.commitguard.yaml` with the secure defaults: `ai_coauthor`, `ai_identity` and
`ai_trailer` blocked; `malformed_trailer` and `bot_identity` warn. Policies you
leave out keep their built-in defaults, so the file only has to say what differs.

With `--github`, `.github/workflows/commitguard.yml` pinned to the Action commit
you named. The workflow alone does not block merges: the check must be required
on the branch (`commitguard github setup`).

## Exit codes

| Code | When |
|---|---|
| `0` | files created, or an existing configuration was kept |
| `2` | not in a Git repository, a file already exists, `--github` without `--action-repository`/`--action-ref`, or the file could not be written |

## Failure cases

- **`configuration already exists`** - `init` will not overwrite it. Edit it, or
  delete it first.
- **`--github requires --action-repository and --action-ref`** - pinning to a tag
  or branch is not offered: an Action reference that can move is a supply-chain
  risk.
