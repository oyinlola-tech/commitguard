# Basic: local enforcement

What this shows: `commitguard install` puts three Git hooks in a repository, and
they stop a commit that records an AI agent as a contributor.

## Run it

```bash
mkdir demo && cd demo && git init
cp ../examples/basic/.commitguard.yaml .
commitguard install          # pre-commit, commit-msg, pre-push
commitguard doctor           # Status: HEALTHY
```

Make a violation:

```bash
git commit --allow-empty -F ../examples/basic/commits/ai-coauthor.txt
```

Expected result: the commit is refused, nothing is written to history, and the
message you typed is kept. Exit code 1.

```text
CommitGuard
x BLOCKED: policy violation detected

AI coauthor detected
  Rule:        ai_coauthor
  Evidence:    Claude <noreply@anthropic.com>
  Source:      Co-authored-by trailer, line 3
  Remediation: Remove the AI co-author attribution from the commit message
```

Fix it by removing the trailer, and the same commit is accepted (exit code 0).

## The sample commits

| File | Expected decision | Why |
|---|---|---|
| `commits/clean.txt` | allow | a human co-author and sign-off |
| `commits/ai-coauthor.txt` | block | `Co-authored-by: Claude <noreply@anthropic.com>` |
| `commits/ai-tool-footer.txt` | block | an agent's tool footer |
| `commits/disguised-coauthor.txt` | block | a bullet before the key and mixed case do not hide it |
| `commits/malformed-trailer.txt` | warn | a broken trailer is reported, not blocked |

Check any of them without committing:

```bash
commitguard check --message-file examples/basic/commits/ai-coauthor.txt   # exit 1
```

## Limitation

These hooks run on the developer's machine, so the developer can skip them
(`git commit --no-verify`, deleting the hook files). That is expected: use the
[github-actions](../github-actions/) example for enforcement that a contributor
cannot bypass.
