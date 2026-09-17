# 01 - Project origin

## What prompted it

AI coding agents now write commits, and most of them record that fact in the
commit metadata:

```text
feat: implement authentication

Co-authored-by: Claude <noreply@anthropic.com>
```

That line is a claim about who contributed. Whether it is welcome depends on the
organization - some require it, some forbid it, most have never decided - but in
all three cases it is currently unmanaged. There is no way to state a policy about
it and have that policy hold across developer machines, repositories and an
organization.

## The decision

Rather than building a script that greps for agent names, the project treats this
as a **security engineering** problem: a policy enforcement system with a threat
model, defence in depth, measurable accuracy, and an explicit account of what it
cannot do.

That framing is what produced the parts worth assessing: an adversarial dataset, a
record of the project's own failures, and documentation that states the limits of
each layer.

## Status

**IMPLEMENTED.** Ten phases between 2026-09-14 and 2026-09-17: detection engine,
Git hooks, a GitHub Actions check, a GitHub App service with a web dashboard,
organization policy governance, a research and benchmarking platform, and an
external-adoption phase.

**MEASURED.** 43,418 lines of Python, 19,634 lines of tests (1,343 tests, 350 of
them security tests), 15,361 lines of TypeScript, 107 documentation files.

## Author

Oluwayemi Oyinlola, sole author and maintainer. AI assistance was used during
development; the design decisions, the security analysis, the adversarial datasets
and the discipline of publishing failing results are the author's, and the
repository's own policy blocks commits that carry AI attribution - the project
enforces on itself the rule it implements.
