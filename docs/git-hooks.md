# Git hooks

> Status: templates exist in `hooks/`. `commitguard install` / `uninstall` and
> the commands the hooks call (`check`, `scan`) are **not implemented yet**
> (Phase 3). Do not install the templates by hand yet: they will refuse every
> commit with a "not implemented" error.

## Why hooks

Developers should not have to remember to run a scan. Once installed, normal
Git usage is unchanged:

```bash
git add .
git commit -m "implement authentication"   # commit-msg hook checks attribution
git push                                   # pre-push hook scans pushed commits
```

## Hooks

| Hook | When | Planned command | Checks |
|---|---|---|---|
| `pre-commit` | before the message is written | `commitguard check --hook pre-commit` | author/committer identity, staged changes (later: secrets) |
| `commit-msg` | after the message is written, before the commit exists | `commitguard check --hook commit-msg --message-file "$1"` | trailers and attribution in the message |
| `pre-push` | before objects are sent | `commitguard scan --hook pre-push` | every commit being pushed (covers commits made with `--no-verify`, amended, or created by tools) |

`commit-msg` is where AI co-author trailers are best caught: the commit is
evaluated as a *pending* commit (`Commit.sha is None`).

## Template behaviour

- POSIX `sh`, `set -eu`, arguments passed quoted.
- **Fail closed**: if `commitguard` is not found, the hook exits 1.
- Marked with `# commitguard-managed-hook` so uninstall only removes our hooks.

## Planned installation behaviour (Phase 3)

- Install into the effective hooks directory (`git rev-parse --git-path hooks`,
  honouring `core.hooksPath`).
- Never overwrite an existing non-CommitGuard hook without an explicit option.
- Embed the absolute interpreter path so hooks work in GUI Git clients where
  the virtualenv is not on `PATH`.
- Atomic writes, executable permissions.
- Coexist with the `pre-commit` framework.

## Limits

Hooks are **not a security boundary**. `git commit --no-verify`, `git push
--no-verify`, deleting `.git/hooks/*`, or using another clone all bypass them.
They exist for fast feedback. Enforcement belongs on the server side — see
[github-enforcement.md](github-enforcement.md).
