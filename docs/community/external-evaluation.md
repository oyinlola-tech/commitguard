# External evaluation

An external evaluation is someone who is not the author installing CommitGuard,
using it, and writing down what actually happened. It is the most useful thing
you can contribute, and the hardest thing for the author to produce.

**No external evaluations have been recorded yet.**

## What to evaluate

Pick any of these; you do not need to do all of them.

| Area | The question |
|---|---|
| Installation | Does it install on your machine, from the documented instructions, without help? |
| Quick start | Does [the 5-minute quick start](../getting-started/quickstart.md) work end to end? |
| CLI usability | Are the messages understandable? Do the exit codes do what the docs say? |
| Policy configuration | Can you express the rule your team actually wants? |
| Hook behaviour | Do the hooks fire when you expect, and stay out of the way otherwise? |
| GitHub integration | Does the check appear, fail correctly, and block a merge once required? |
| Documentation | Does anything claim something the software does not do? |
| Performance | Is the hook delay acceptable on your repository? |
| Security behaviour | Can you get a violating commit past the server-side check? |
| Cross-platform | Does it work on your OS, shell and Git version? |

## A concrete evaluation script (about 30 minutes)

Record the result of each step, including anything confusing.

1. **Install** from a pinned commit, as in the quick start. Note the time taken
   and any error.
2. `commitguard --version`, then `commitguard doctor` outside a repository.
   *Expected:* doctor reports the repository check as a warning and does not crash.
3. In a scratch repository: `commitguard init --install-hooks`, then
   `commitguard doctor`. *Expected:* `Status: HEALTHY`,
   `Enforcement: LOCAL ENFORCEMENT ONLY`.
4. Commit with `Co-authored-by: Claude <noreply@anthropic.com>` in the message.
   *Expected:* refused, exit 1, nothing committed, message preserved.
5. Commit the same thing with `--no-verify`. *Expected:* it succeeds - a
   documented limitation, not a bug.
6. `commitguard scan HEAD` and `commitguard scan --format json`.
   *Expected:* the violation explained, with evidence; exit 1.
7. Try to make CommitGuard miss an attribution: change the case, add symbols or
   invisible characters, break the trailer. **Anything that gets through is a
   finding we want** - report it privately if it is a genuine evasion.
8. Point it at a real repository of yours:
   `commitguard scan origin/main~200..origin/main`. *Expected:* no false
   positives on human commits. Report any false positive with the (redacted)
   trailer.
9. If you use GitHub: add the check (`commitguard init --github ...`), open a
   pull request with a violating commit, and see whether the check fails.
10. Uninstall: `commitguard uninstall`, and check your own pre-existing hooks are
    back.

## How to report what you found

Open an issue with the **Evaluation feedback** template, which asks for your OS,
Git and CommitGuard versions, the install method, what you expected and what
happened. Please include:

- anything that did not work, with the exact command and output;
- anything in the documentation that was wrong or misleading;
- how long steps took, if that is interesting;
- what you would have needed to use this for real.

**Do not include** tokens, private repository content, or your employer's
confidential information. A redacted trailer (`Co-authored-by: X <x@example.com>`)
is enough for a detection issue. Security evasions go through
[private reporting](../../SECURITY.md), not a public issue.

## Publication and consent

Evaluations are recorded in `evidence/external-validation/` **only with the
evaluator's permission**, and only what the evaluator agreed to publish. You can
ask to be named, to be anonymous ("an engineer at a UK fintech"), or not to be
published at all - the feedback is still valuable.
