# Git hooks

> Status: **implemented (Phase 3)**. `commitguard install` / `uninstall`,
> `commitguard hook pre-commit|commit-msg|pre-push`, hook integrity checks in
> `commitguard doctor`, and opt-in global installation.

> **Local Git hooks can be bypassed by someone who controls the local
> repository** (`--no-verify`, deleting hooks, another clone). Server-side
> enforcement is therefore required for authoritative repository protection;
> that is the GitHub check, see [github-enforcement.md](github-enforcement.md).

## Quick start

```bash
cd my-project
commitguard init        # optional: write .commitguard.yaml
commitguard install     # pre-commit, commit-msg and pre-push hooks
commitguard doctor      # verify: Status: HEALTHY

git add .
git commit -m "implement authentication"   # checked
git push                                   # every outgoing commit checked
```

## Architecture

```text
git commit ─▶ .git/hooks/pre-commit ─┐
git commit ─▶ .git/hooks/commit-msg ─┼─▶ commitguard hook <name>
git push   ─▶ .git/hooks/pre-push  ──┘          │
                                                ▼
                                services/hooks.py (build commits from Git)
                                                │
                                   Analyzer (Phase 2 engine + policies)
                                                │
                                    ALLOW / WARN ─▶ exit 0 ─▶ Git continues
                                    BLOCK        ─▶ exit 1 ─▶ Git stops
                                    error        ─▶ exit 2 ─▶ Git stops
```

The files in `.git/hooks` contain **no detection or policy logic**. They
locate CommitGuard and call the stable `commitguard hook <name>` interface.

## What each hook checks

| Hook | When Git runs it | Input | Analysed |
|---|---|---|---|
| `pre-commit` | before the commit message exists | pending author/committer (`git var`, honours `--author`) | identity rules (`ai_identity`, `bot_identity`) |
| `commit-msg` | after the message is written, before the commit object exists | message file after cleanup + pending identity | all message and identity rules |
| `pre-push` | before objects are sent | ref updates on stdin | every commit the push would introduce |

**commit-msg is best effort.** It sees the message before Git creates the
commit, and cannot see command-line options such as `--cleanup`. CommitGuard
approximates Git's cleanup: comment lines are stripped only when
`commit.cleanup=strip` is configured (with `-m`/`-F`, Git keeps `#` lines), and
everything below Git's scissors line (the `git commit -v` diff) is ignored.
**pre-push analyses the real commit objects** and is the authoritative local
check; it also catches commits created with `--no-verify`, by `git merge`,
`git rebase`, `git cherry-pick`, or tools that do not run commit hooks.

`pre-commit` has no staged-content policies yet (secret detection is planned for the security intelligence phase).

## Outgoing commit detection (pre-push)

Git writes one line per ref update:

```text
<local ref> <local sha> <remote ref> <remote sha>
```

For each line:

| Case | Detection | Handling |
|---|---|---|
| branch update | remote sha present | commits reachable from local sha, excluding the remote sha and the remote's tracking refs |
| new branch / tag | remote sha is all zeros | same, excluding the remote's tracking refs (never the whole history the remote already has) |
| deleted ref | local ref `(delete)`, local sha all zeros | nothing to analyse; allowed |
| annotated tag | local sha is a tag object | peeled to its commit |
| tag to tree/blob | peels to no commit | nothing to analyse; allowed |
| force push | remote sha not an ancestor | commits the remote does not have; the force itself is not a policy violation |
| remote sha unknown locally | not fetched | excluded via tracking refs only (may analyse more, never less) |
| detached HEAD | `HEAD:refs/heads/x` | uses the ref information Git provides |
| push to a URL | no named remote | no tracking refs to exclude; all commits not known to be on that remote |

All selected commits across all updates are **deduplicated by SHA** and
analysed once, oldest first. Merge commits are analysed like any other commit;
parents already on the remote are not rescanned. Commit IDs are passed to
`git rev-list --stdin` (no shell, no argument parsing). A push that would
introduce more than `enforcement.max_push_commits` (default 10000) commits is
blocked with an error rather than analysed partially.

Remote-tracking refs can be stale. Commits that exist in a tracking ref are
assumed to be on the remote already; they were checked when they were pushed.

### Push decision

Precedence is **BLOCK > WARN > ALLOW** across all outgoing commits. Any BLOCK
stops the whole push, so none of the refs are updated.

```text
CommitGuard
✗ PUSH BLOCKED
Remote: origin
Commits checked: 5
Violations: 3
Warnings: 1
Allowed: 1

✗ 8e71c2a  feat: implement authentication
    AI coauthor detected [ai_coauthor, high]
    Evidence: Claude <noreply@anthropic.com> (Co-authored-by trailer, line 3)
...
How to fix:
  CommitGuard never modifies commits. ...
No changes were pushed to the remote repository.
```

Clean pushes print a single line. `commitguard scan <sha>` or
`commitguard check --verbose <range>` show full evidence.

## Installation

`commitguard install [--hook NAME]... [--allow-shared-hooks-path]`

1. finds the repository and its Git directory (`git rev-parse`);
2. determines the hooks directory (`git rev-parse --git-path hooks`, which
   honours `core.hooksPath`);
3. refuses a hooks directory outside the repository's Git directory (a shared
   or tracked `core.hooksPath` would affect other repositories) unless
   `--allow-shared-hooks-path` is given;
4. writes each hook atomically, executable on POSIX systems;
5. never overwrites an existing hook (see below).

Running `install` again is idempotent; it repairs modified blocks and updates
hooks generated for a different interpreter.

### Existing hooks

An existing hook that CommitGuard does not manage is **preserved and
chained**, never overwritten:

```text
.git/hooks/pre-push                    ← CommitGuard wrapper
.git/hooks/pre-push.pre-commitguard    ← your original hook, byte-for-byte
```

Execution order:

1. **CommitGuard** runs first. If it blocks or errors, Git stops and the
   original hook does not run.
2. **The original hook** runs next with the same arguments and, for pre-push,
   the same standard input. Its exit status is respected: if it fails, Git
   stops. It only runs if it is executable, exactly as Git would treat it.

If `<hook>.pre-commitguard` already exists, installation refuses rather than
overwrite either file.

### Managed block

```sh
#!/bin/sh
# BEGIN COMMITGUARD
# ...
# commitguard-hook: pre-push
# commitguard-format: 1
# commitguard-checksum: sha256:<hex>
...
# END COMMITGUARD
```

The markers let `install` recognise its own hooks and `uninstall` remove only
its own content. The checksum lets `doctor` report edits
("pre-push hook appears to have been modified"); it never repairs them
automatically — `commitguard install` does, when you run it.

## Uninstallation

`commitguard uninstall [--hook NAME]...`

- removes only the managed block;
- if nothing else remains in the file, deletes it and moves
  `<hook>.pre-commitguard` back into place;
- if you added your own lines to the wrapper, keeps them (and leaves the
  preserved file for you to merge);
- leaves foreign hooks, `.sample` files and everything else untouched.

## How hooks find CommitGuard

Git runs hooks with a minimal environment: the virtualenv may not be active and
`PATH` may differ (GUI clients, IDEs). The wrapper therefore embeds the
interpreter that ran `commitguard install` and runs:

```sh
"<interpreter>" -P -m commitguard hook <name> "$@"
```

- `-P` prevents Python from importing a `commitguard/` directory from the
  repository being committed (hooks run in the work tree) instead of the
  installed package;
- if that interpreter no longer exists, the wrapper falls back to
  `commitguard` on `PATH`;
- if neither is available, the hook **fails closed**:

```text
CommitGuard is not available.
The repository's pre-push security hook could not execute.
Operation blocked because the security check could not be completed.
Run: commitguard doctor   (reinstall hooks with: commitguard install)
```

Limitation: the embedded path is absolute. Moving or deleting the virtualenv
requires `commitguard install` again (`doctor` reports it).

## Failure behaviour and exit codes

| Exit | Meaning | Git |
|---|---|---|
| 0 | allowed (including warnings), or enforcement disabled for this hook | continues |
| 1 | blocked by policy | stops |
| 2 | CommitGuard error: invalid configuration or rules, Git error, malformed hook input, too many commits, unexpected exception | stops |

Errors always block (fail closed):

```text
CommitGuard could not verify repository policy.
Reason: .commitguard.yaml: invalid configuration: ...
Push blocked because the security check could not be completed.
Run: commitguard doctor
```

This is not configurable. Git itself reports any failing hook with exit code 1
to its caller.

## Enforcement configuration

```yaml
enforcement:
  pre_commit: true
  commit_msg: true
  pre_push: true
  max_push_commits: 10000
```

All hooks enforce by default. A disabled hook prints a notice and exits 0;
`commitguard doctor` reports `! WARNING  pre-push enforcement disabled in configuration`
and "Security enforcement is incomplete." Disabling a hook does not change
any policy.

## Automatic removal (opt-in)

With `remediation.auto_remove: true` the `commit-msg` hook deletes prohibited
attribution from the pending message instead of refusing the commit:

```
$ git commit -m "feat: add login

Co-authored-by: Claude <noreply@anthropic.com>"
CommitGuard
! REMOVED prohibited attribution from the commit message (1 line)

    line 3: Co-authored-by: Claude <noreply@anthropic.com>  [ai_coauthor]

  The commit was created without them.
  Turn this off with `remediation: {auto_remove: false}` to block instead.
[main 8ebd243] feat: add login
```

It is off by default and never applies to identity-based findings, which cannot
be fixed by editing text. `pre-push` never removes anything. See
[configuration.md](configuration.md#remediation) for the full rules.

## Doctor

`commitguard doctor` checks Git, repository, Git directory, hooks directory,
`core.hooksPath`, CommitGuard version and interpreter, configuration, the
detection engine (self-test), policies, each hook's presence, integrity,
executability and interpreter, chained hooks, and enforcement settings. It ends
with `HEALTHY`, `DEGRADED` (warnings, exit 0) or `UNHEALTHY` (failures, exit 2).

## Global installation

`commitguard install --global` places hooks in
`~/.config/commitguard/git-template/hooks` and sets
`git config --global init.templateDir` to it **only if that setting is unset**.
New repositories created by `git init` or `git clone` then receive the hooks.

- Existing repositories are not changed; run `commitguard install` in them.
- A different, existing `init.templateDir` is never replaced (install refuses).
- `core.hooksPath` is deliberately **not** used: setting it globally would stop
  every repository's own `.git/hooks` from running.
- `commitguard uninstall --global` removes the template hooks and unsets
  `init.templateDir` only if it still points to CommitGuard's directory.

## Cross-platform behaviour

- Hooks are POSIX `sh` scripts with LF line endings. Git runs hooks through
  `sh` on Linux and macOS, and through Git for Windows' bundled `sh` on Windows,
  so one format serves all three. `.gitattributes` keeps them LF on checkout.
- On Windows, the embedded interpreter path is written with forward slashes
  (`C:/Users/.../python.exe`), which Git's `sh` accepts; executability is
  determined by Git from the `#!` line, so no permission bits are required.
- All Python code is platform-independent; platform differences are isolated
  in `commitguard.utils.platform`.
- Verification: the suite (including real `git commit`/`git push` tests) has
  been run on Linux. The CI workflow is configured to run it on Ubuntu, macOS
  and Windows; Windows and macOS results depend on that CI run.

## Bypass and limitations

- `git commit --no-verify` and `git push --no-verify` skip hooks. This is
  intentional Git behaviour; CommitGuard does not try to defeat it, and tests
  assert that the bypass works so the security model stays honest.
- Anyone with access to the clone can delete hooks, set `core.hooksPath`, or
  edit `.commitguard.yaml` to disable enforcement. `doctor` makes these
  states visible; it cannot prevent them.
- A repository's own `.commitguard.yaml` (and therefore a commit that changes
  it) controls local policy.
- Annotated tag objects' own messages are not analysed, only the commits they
  point to.

Authoritative protection requires server-side enforcement: the GitHub check
(`commitguard ci github`) required by branch protection. See
[github-enforcement.md](github-enforcement.md).
