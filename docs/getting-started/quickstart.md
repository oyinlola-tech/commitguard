# Quick start (5 minutes)

By the end you will have blocked a real commit, seen why, fixed it, and know
what local hooks do and do not protect.

You need **Git 2.31+** and **Python 3.12+**.

## 1. Install CommitGuard

```bash
pipx install commitguardian
```

The PyPI name is **`commitguardian`**; the command you run is **`commitguard`**.
They differ because `commitguard` and `commitguard-cli` on PyPI are unrelated
projects by other authors - installing either gets you someone else's tool.

No pipx? Use a virtual environment:

```bash
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
python -m pip install commitguardian
```

To pin to an exact commit instead of a release:

```bash
pipx install "git+https://github.com/oyinlola-tech/commitguard@<commit-sha>"
```

Check it:

```bash
commitguard --version
```

## 2. Set up a repository

```bash
cd my-project
commitguard init --install-hooks
```

That writes `.commitguard.yaml` (AI attribution blocked, automation accounts
warned) and installs the `pre-commit`, `commit-msg` and `pre-push` hooks. An
existing hook of yours is kept and chained, never overwritten.

Run `commitguard init` on its own and it asks before doing anything; in a script
or CI it never prompts. `--non-interactive` forces that behaviour.

## 3. Confirm the setup

```bash
commitguard doctor
```

Every line is `PASS`, `WARNING`, `FAIL`, `NOT CONFIGURED` or `INFO`, and the last
two lines say what is actually enforced:

```text
Enforcement: LOCAL ENFORCEMENT ONLY
Status: HEALTHY
```

`NOT CONFIGURED` is never a pass. "GitHub enforcement: NOT CONFIGURED" means
nothing is checking your commits on GitHub yet (step 6).

## 4. Trigger a violation

```bash
git commit --allow-empty -m "feat: add payment service

Co-authored-by: Claude <noreply@anthropic.com>"
```

The commit is refused, nothing enters history, and your message is kept:

```text
CommitGuard
x BLOCKED: policy violation detected

AI coauthor detected
  Rule:        ai_coauthor
  Evidence:    Claude <noreply@anthropic.com>
  Source:      Co-authored-by trailer, line 3
  Remediation: Remove the AI co-author attribution from the commit message
```

Exit code 1. `commitguard scan HEAD` explains an existing commit the same way,
and `commitguard check --message-file <file>` checks a message before committing.

## 5. Fix it and commit

```bash
git commit --allow-empty -m "feat: add payment service"
```

Accepted, exit code 0. CommitGuard never edits your commits: removing the
attribution is your decision.

## 6. Understand what this does not protect

The hooks run on **your** machine, so anyone with the repository can skip them:

```bash
git commit --no-verify -m "feat: x

Co-authored-by: Claude <noreply@anthropic.com>"     # succeeds
```

That is expected, and measured: see the bypass experiments in
`tests/integration/github/security/test_bypass_resistance.py`. Local hooks catch
accidents quickly; they are not a control against someone who does not want to be
caught.

For enforcement a contributor cannot skip, add the GitHub check:

```bash
commitguard init --github \
  --action-repository oyinlola-tech/commitguard \
  --action-ref <40-character commit sha>
commitguard github setup      # prints the branch protection steps
```

Then, in **Settings -> Rules / Branches**, protect the branch, require a pull
request, and require the status check named `commitguard`. Only that last step
makes a failing check block a merge - CommitGuard cannot do it for you, and
`doctor` says so rather than pretending otherwise.

## Where to go next

| You want | Read |
|---|---|
| A worked example with sample commits | [examples/basic/](../../examples/basic/) |
| The GitHub Actions check in detail | [docs/github-enforcement.md](../github-enforcement.md) |
| One service for many repositories | [docs/deployment/github-app.md](../deployment/github-app.md) |
| What is detected, and what is not | [docs/detection-engine.md](../detection-engine.md) |
| Tuning the policy | [docs/configuration.md](../configuration.md) |
| Exit codes and command reference | [docs/cli/README.md](../cli/README.md) |
| Something went wrong | [docs/deployment/troubleshooting.md](../deployment/troubleshooting.md) |
