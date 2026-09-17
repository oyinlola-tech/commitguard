# ADR-005: The trailer parser uses no regular expressions

**Status:** Accepted (Phase 2) · **Applies to:** `provenance/trailers.py`

## Context

Trailer parsing is the most hostile input path in the project: commit messages are
attacker-controlled, unbounded, and full of Unicode. A regular expression for
"a trailer key, possibly disguised" would be long, hard to review, and a natural
home for catastrophic backtracking.

## Decision

The parser is written as an explicit, linear scan: splitting on every Unicode line
separator, bounded key length, bounded trailer count, bounded prefix skipping, and
explicit issue flags (`MISSING_SEPARATOR`, `NONSTANDARD_KEY`, `EMPTY_VALUE`,
`LEADING_CHARACTERS`). It never raises.

## Consequences

Good:

- Work is linear in message size, which matters: a 10 MB commit message is a
  denial-of-service attempt, and this path is measured (751 ms after the
  normalisation fix).
- Each disguise gets a named flag that detectors and reports can use, rather than
  a silent match-or-not.
- When three bypasses were found, each fix was a readable change to a scan, not a
  new branch in an expression.

Bad:

- More code than a regular expression, and the prefix rules needed two revisions
  to get right (ADR-002 of the dataset versions, in effect).
- The parser deliberately treats a quoted `> Co-authored-by:` line as a trailer,
  which is the documented trade-off in
  [../research/limitations.md](../research/limitations.md).

## Enforcement

`tests/unit/provenance/test_trailers.py` (including three bypass regressions),
the labelled dataset's adversarial class, and the ReDoS suite, which measures
every regular expression that *does* exist elsewhere.
