# 02 - The problem

## Statement

> Can a Git contribution policy - specifically, whether commits may carry AI agent
> attribution - be enforced across developer machines, repositories and an
> organization, accurately, fast enough to sit in the commit path, and honestly
> about its own limits?

## Why it is not trivial

The naive solution is `grep -i "co-authored-by: claude"`. It fails in both
directions, and the failures are not exotic:

| Evasion | Example | Status in CommitGuard |
|---|---|---|
| Case and spacing | `CO-AUTHORED-BY:Claude <...>` | **TESTED** - dataset case |
| Symbol before the key | `\ufffdCo-authored-by: ...` | **TESTED** - was a real bypass, found 2026-09-17, fixed |
| Unicode letter/number before the key | `\u32acCo-authored-by: ...` | **TESTED** - real bypass, found by fuzzing, fixed |
| A character that renders as nothing | `Co-authored\ufe0f-by: ...` | **TESTED** - real bypass, found by fuzzing, fixed |
| Look-alike letters | Cyrillic `\u0421o-authored-by` | **TESTED** - explicit homoglyph folding |
| Compatibility forms | full-width characters | **TESTED** - NFKC |

And the false-positive direction, which matters just as much for a tool people
are asked to put in their commit path:

| Not a violation | Why |
|---|---|
| Claude Shannon, a human | exact matching after normalisation, never substring |
| `jane@anthropic.com`, a human employee | a vendor domain alone is never sufficient evidence |
| "feat: use AI for recommendations" | wording is not evidence |
| `- Reviewed-by: Grace Hopper <...>` | a bulleted human trailer - this was a real false positive, found and fixed |

## The harder half

Detection is the visible part. The security problem is that **any check can be
avoided**:

- hooks run on the contributor's machine and can be skipped;
- a pull request can edit the policy file that judges it;
- a check can be re-run on an older, clean commit;
- a merge queue can combine a clean pull request with a dirty one;
- the service can be offline, unauthorised, or looking at a suspended installation.

Each of those is an attack with a recorded experiment in this project, and each is
answered by design rather than by configuration advice.

## Scope, stated up front

CommitGuard enforces what a commit **claims**. Metadata is self-asserted: someone
who does not want attribution recorded simply does not record it. This is a policy
and provenance-record system, not proof of how code was produced - a distinction
maintained throughout the documentation, and the reason the dataset labels mean
what they mean.
