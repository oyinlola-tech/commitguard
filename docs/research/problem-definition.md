# Problem definition

## The research question

> Can a Git contribution policy - specifically, whether commits may carry AI agent
> attribution - be enforced across developer machines, repositories and an
> organization, in a way that is accurate, fast enough to sit in the commit path,
> and honest about its own limits?

Three parts, each measurable: **accuracy** (does it find what it claims to find,
without flagging humans), **cost** (can it run on every commit), and **honesty**
(does it ever report a safety it has not verified).

## Why this problem exists

AI coding agents write commits, and many record themselves in the commit
metadata: `Co-authored-by: Claude <noreply@anthropic.com>`, a tool footer, or an
author identity such as `Copilot <...@users.noreply.github.com>`.

Some organizations need to control that. The reasons are mundane and real:

- **Licensing and contributor agreements.** A CLA signed by a human does not
  obviously cover a commit attributed to a vendor's agent.
- **Provenance records.** If the metadata says something about origin, it should
  be accurate and consistent, or it should not be there.
- **Contribution policy.** Some projects want agent attribution; some forbid it;
  most have never decided. All three are legitimate, and none is enforceable by
  hand at scale.

Checking by hand does not scale, and the naive solution - `grep -i
"co-authored-by: claude"` - fails in both directions:

| Naive matching fails | Example |
|---|---|
| Case and spacing | `CO-AUTHORED-BY:Claude <noreply@anthropic.com>` |
| Symbols and quoting | `> Co-authored-by: ...` in a quoted mail body |
| Unicode look-alikes | a Cyrillic `а` inside `Co-authored-by` |
| Invisible characters | a variation selector inside the key |
| Compatibility forms | full-width characters that normalise to ASCII |
| False positives | a human named **Claude** Shannon; an employee at an AI vendor; "use AI for recommendations" in a subject |

Every row above is a case in the labelled dataset, and three of them were
**bypasses that worked** until these benchmarks found them.

## What this is not

It is not a way to tell whether code was written by an AI. Metadata is
self-asserted: anyone who does not want attribution recorded simply does not
record it. CommitGuard enforces what a commit **claims**; it does not prove how
the code was produced. That distinction is not a caveat added at the end - it
determines what the dataset labels mean and what the tool is allowed to say.

See [limitations.md](limitations.md), and
[roadmap.md](roadmap.md) for what stronger provenance would require
(attestations and signatures, not better string matching).

## What would count as a solution

| Criterion | Target | Where it is measured |
|---|---|---|
| Finds documented attribution | no false negatives on the labelled dataset | [detection-evaluation.md](detection-evaluation.md) |
| Does not flag humans | no false positives, including deliberate near-misses | same |
| Fast enough for the commit path | well under a second of CPU per commit | [performance-evaluation.md](performance-evaluation.md) |
| Works where developers work | Linux, macOS, Windows, with spaces and non-ASCII in paths | [cross-platform-validation.md](cross-platform-validation.md) |
| Cannot be bypassed server-side | no passing check for a violating commit | [bypass-resistance.md](bypass-resistance.md), [github-enforcement-validation.md](github-enforcement-validation.md) |
| Never claims unverified safety | failures produce errors, not passes | [reliability-evaluation.md](reliability-evaluation.md) |
| Someone else can check all of this | a documented, runnable reproduction | [reproducibility.md](reproducibility.md) |
