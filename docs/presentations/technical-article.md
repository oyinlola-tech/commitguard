# Building defence in depth for Git contribution security

*How a policy about commit metadata turns into a security engineering problem, and
what measuring it honestly revealed.*

Oluwayemi Oyinlola · 2026-09-17 · CommitGuard 0.1.0.dev0

---

## The line that started it

```text
feat: implement authentication

Co-authored-by: Claude <noreply@anthropic.com>
```

AI coding agents write commits, and many record themselves in the metadata. Some
organizations want that; some cannot accept it, for licensing or
contributor-agreement reasons; most have never decided. In all three cases the
situation today is the same: **there is no way to state a policy about it and have
that policy hold.**

The obvious fix is a grep in CI. This article is about why that fails, and what it
takes to do properly - including the three times my own implementation was wrong
and how I found out.

## Why string matching is not enough

Detection has to satisfy two requirements that pull against each other: find
attribution that has been disguised, and never flag a human.

The disguises are not exotic. `CO-AUTHORED-BY:Claude`. A quoted `> ` in front. A
Cyrillic `\u0421` that looks exactly like a `C`. Full-width characters. A variation
selector inside the key, which renders as nothing at all.

And the false positives are real people: Claude Shannon; an engineer whose employer
is an AI vendor, committing their own work; a commit message that says "use AI for
recommendations".

CommitGuard's answer: rules are **data**, matching is **exact after normalisation**
(NFKC, case folding, removal of every Unicode default-ignorable code point, and a
small explicit map of Latin look-alikes), and evidence is **weighted** - an exact
vendor e-mail or bot login is strong evidence, a bare first name is medium, a
vendor domain alone is never sufficient. `Claude` never matches `Claudette`.

## Any check can be avoided

The interesting problem is not detection, it is enforcement. Every layer has a way
around it:

| Layer | The way around it |
|---|---|
| Git hooks | `--no-verify`, delete the hooks, redirect `core.hooksPath`, clone fresh |
| A CI check | edit the policy file in the same pull request |
| A check that passed once | re-run it on an older, clean commit |
| A clean pull request | merge it in a queue with a dirty one |
| A service | wait for it to be offline, or for its permissions to be revoked |

So the design is three layers running **the same engine**, with different
authority:

```text
Git hooks (advisory) ─▶ GitHub Actions check ─▶ GitHub App (central policy)
```

and a set of rules that make the upper layers hold:

- the policy is read from the **base** commit, so a pull request cannot relax the
  rules that judge it;
- rules come from the installed package, never the repository, and the workflow
  installs the scanner itself from a trusted commit;
- re-runs are only honoured for the newest commit of a branch;
- merge groups are scanned as the merge commit that would actually land;
- a central mandatory policy is applied last and can only make enforcement
  *stricter*.

Local hooks stay in the design, but they are documented as what they are: fast
feedback, not a control. CommitGuard publishes the seven ways they can be bypassed.

## The part I would skip if I were being lazy

Any of the above can be written in a README and believed. I wanted numbers, so
Phase 9 built a labelled dataset - 9,174 commits across six classes, where every
case states **the decision the policy must reach**, derived from the documented
semantics and, for adversarial cases, from the security requirement that
*disguised attribution is still attribution*.

The rule that makes this worth anything: **labels are never changed to match the
implementation.** A mismatch is a failure.

The first run found one:

```text
False negatives   1   (violations allowed)
adversarial-replacement-characters: expected block, got allow
```

A replacement character - the thing you get from mangled UTF-8 - in front of
`Co-authored-by:` made the parser ignore the line entirely, while every human
reader still sees the attribution. One character, complete bypass.

I fixed it by skipping leading non-alphanumeric characters. The next run:

```text
False positives   2   (clean commits not allowed)
clean-bullet-reviewer, clean-dash-human-coauthor
```

The fix had broken ordinary bulleted human trailers. The dataset contained
false-positive probes precisely so that a security fix could not quietly create a
usability bug.

## Then I let a fuzzer look

Hand-written cases cover what you thought of. In Phase 10 I added property-based
fuzzing with a deliberately narrow property: *the same attribution, with arbitrary
casing, spacing and any prefix character, must still block.*

It found 387 failures in 4,000 generated examples.

```text
\u32acCo-AUThorEd-By:Claude <noreply@anthropic.com>     allowed
\u2460Co-authored-by: Claude <noreply@anthropic.com>     allowed
```

My skip rule used Python's Unicode-aware `isalnum()`, and ran **after** NFKC
normalisation - so `\u2460` became `1`, `\u24de` became `o`, and they joined the
key. The fix: trailer keys are ASCII, so the prefix is whatever precedes the first
ASCII letter or digit, judged both before and after normalisation, with the
shortest reading winning. Look-alike letters stay part of the key, because there
they are a disguised key rather than a prefix.

Then the fuzzer found a third class:

```text
Co-authored\ufe0f-by: Claude <noreply@anthropic.com>     allowed
```

A variation selector. My normalisation stripped control and format characters -
which covers zero-width spaces - but Unicode marks **4,174** code points as
`Default_Ignorable_Code_Point`, and many of them are neither: variation selectors,
the combining grapheme joiner, Hangul fillers. Every one renders as nothing.

Three bypasses, all in the same conceptual gap: *what counts as "the same text" to
a human eye versus to a matcher.* Each is now a dataset version, a recorded
failing run, a fix and a regression test.

## Measuring the boring things finds security bugs too

The performance benchmark showed a 10 MB commit message taking **4.8 seconds**.
Commit messages are attacker-controlled and unbounded: that is a denial-of-service
surface in the commit path and in the server's worker pool, not a slow function.
An ASCII fast path took it to 752 ms, verified to produce identical output on
50,000 random strings.

A ReDoS suite - every regular expression in the codebase, collected automatically,
timed against 70 adversarial inputs of 50,000 characters - found two quadratic
patterns. The secret-redaction pattern for JWTs took **4.6 seconds** on 80 KB of
`eyJ-eyJ-…`, and redaction runs on log fields and Check Run output, *before*
truncation. The workflow `secrets.` scanner took 2.7 seconds on 60 KB, and it runs
on workflow files fetched from monitored repositories. Both are now linear scans.

## What I published

Thirteen detection runs, four of which failed. Two performance runs, before and
after. Hook overhead of **+947 ms per commit** - almost entirely Python interpreter
start-up - which is the least flattering number in the project and the one I was
most tempted to leave out.

Results are immutable: written once, never overwritten. A dataset version only ever
adds cases and always rebuilds to the same fingerprint. When a benchmark exposes a
bug, the failing run is recorded **before** the fix exists.

That discipline is the actual contribution here. A benchmark you own, that you can
re-run until it looks good, measures nothing.

## What it still cannot do

Metadata is self-asserted. Delete the trailer and the commit is indistinguishable
from a human's. CommitGuard enforces what a commit *claims* - it does not prove how
code was produced, and the documentation says so on every page where the question
arises. Stronger guarantees need attestations and signatures at the moment of
creation, which is research, not a better matcher.

Nor has anyone else verified any of this. The author wrote the software, the
dataset and the evaluation. That is why `commitguard reproduce all` exists, why it
needs no credentials for the accuracy claim, and why a skipped step never reports
as a pass.

## Lessons worth taking elsewhere

1. **Write the labels before the code passes them.** If the expected result can be
   edited when it fails, it is not a measurement.
2. **Keep false-positive probes next to the security cases.** My security fix broke
   ordinary commits, and only the probes caught it.
3. **Fuzz the property, not the input.** "Any disguise of this exact attribution
   must still block" found three bypasses; random bytes would have found none.
4. **Time every regular expression against hostile input.** Two of mine were
   quadratic and both were reachable from untrusted text.
5. **Publish the run that failed.** It is the only way anyone can tell whether the
   green number means anything.

---

*Source, evidence and reproduction: https://github.com/oyinlola-tech/commitguard.
Not published to PyPI - that name belongs to an unrelated project.*
