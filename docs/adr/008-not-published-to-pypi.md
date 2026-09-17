# ADR-008: CommitGuard is installed from Git, not PyPI

**Status:** Accepted (Phase 10) · **Applies to:** all installation documentation

## Context

The natural instruction for a Python tool is `pip install commitguard`. That name
on PyPI already belongs to an **unrelated** project: a Git hooks library by
another author, published up to version 2.2.0, which installs its own
`commitguard` console script.

Documentation in this repository had been telling people to run
`pip install 'commitguard[app]'`. Anyone following it would have installed someone
else's hook-management tool - a dependency-confusion problem created by our own
documentation.

## Decision

CommitGuard is installed from this repository, pinned to a commit:

```bash
pipx install "git+https://github.com/oyinlola-tech/commitguard@<commit-sha>"
```

No instruction anywhere may suggest installing `commitguard` from PyPI. Where the
collision is relevant - installation docs, the CLI's own "install the optional
dependencies" message, the threat model - it is stated explicitly.

## Consequences

Good:

- Nobody is sent to the wrong package.
- Pinning to a full commit SHA is a stronger provenance statement than a version
  range against an index.

Bad:

- Installation is less convenient, and `pipx install commitguard` will never be
  the instruction.
- Publishing later needs a different distribution name, which is an open decision
  deliberately left to the maintainer.

## Enforcement

`tests/integration/test_examples.py` fails if any Markdown code block in the
repository contains a `pip install`, `python -m pip install` or `pipx install`
command naming `commitguard` without a Git source.
