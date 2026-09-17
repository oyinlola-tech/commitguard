# ADR-002: Detection rules are data, not code

**Status:** Accepted (Phase 2) · **Applies to:** `rules/*.yaml`, `rules/matcher.py`

## Context

Every AI agent has its own identities: e-mail addresses, bot logins, display
names, tool footers. That list changes often, and it is exactly the sort of thing
that turns into a growing pile of `if` statements.

## Decision

Identities, domains, bots and patterns live in YAML (`rules/ai-identities.yaml`
and friends), compiled into matchers at load time. Rules always come from the
**installed package**, never from the repository being scanned.

## Consequences

Good:

- Adding an agent is a data change with a test, reviewable by someone who does
  not know the codebase.
- A pull request cannot edit the rules that judge it, because the repository's own
  `rules/` directory is never used (and `doctor` says so explicitly if one exists).
- Rule data is fingerprinted into every benchmark manifest, so a result names the
  rules that produced it.

Bad:

- Rule data ships with the package, so updating rules means updating CommitGuard.
- The matcher has to be conservative about what data can express, to avoid
  becoming a pattern language with its own injection and ReDoS surface.

## Enforcement

`tests/unit/rules/`; `doctor` reports when a repository's `rules/` directory would
be mistaken for the real one.
