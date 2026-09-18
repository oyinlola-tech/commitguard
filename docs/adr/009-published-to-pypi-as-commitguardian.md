# ADR-009: Published to PyPI as `commitguardian`

**Status:** Accepted (Phase 10) · **Supersedes:** [ADR-008](008-not-published-to-pypi.md)
· **Applies to:** packaging and all installation documentation

## Context

ADR-008 stopped CommitGuard telling people to `pip install commitguard`, because
that name belongs to someone else, and left the publishing question open:

> Publishing later needs a different distribution name, which is an open decision
> deliberately left to the maintainer.

Installing from a pinned Git commit works, but it is a poor first experience: it
needs a SHA, it is slow, and it looks unfinished.

Checked against PyPI on 2026-09-18:

| Name | Owner |
|---|---|
| `commitguard` | Yasser Tahiri - a git-hooks library, v2.2.0 ([yezz123/CommitGuard](https://github.com/yezz123/CommitGuard)) |
| `commitguard-cli` | PierrunoYT - an AI commit analyser, v0.2.0 |

Two unrelated projects already hold CommitGuard names. `commit-guard` was free,
but one hyphen from an existing project is the same dependency-confusion trap as
ADR-008, pointed the other way, so it was rejected.

## Decision

The **distribution name is `commitguardian`**. The product is still CommitGuard:

| | Name |
|---|---|
| Product | CommitGuard |
| PyPI distribution (`pip install`) | `commitguardian` |
| Import package | `commitguard` |
| Console script | `commitguard` |

```bash
pipx install commitguardian
commitguard install
```

Releases are published from the tagged release workflow using **PyPI Trusted
Publishing** (OIDC). No API token is created, stored or rotated, so there is no
long-lived credential to leak - consistent with how the rest of the release
pipeline is hardened.

The sdist is built from an **allowlist**. Hatchling's default swept in an
editor's scratch worktree under `.kilo/` (a second copy of the repository), the
`.hypothesis/` cache and 22 MB of generated benchmark datasets. A published sdist
is public and permanent, so its contents are enumerated rather than filtered.

## Consequences

Good:

- `pipx install commitguardian` is a real, one-line install.
- The import package and console script are unchanged, so every existing
  document, hook and script keeps working.
- Trusted Publishing means no token to steal.
- The sdist is 988 KB instead of 8.4 MB and contains nothing local.

Bad:

- The name you install is not the name you type, which needs explaining once in
  the README.
- A machine that installs *both* `commitguardian` and someone else's
  `commitguard` gets one `commitguard` console script, whichever landed last.
  Uncommon, and pip warns, but it is real.

## Enforcement

`tests/integration/test_examples.py`:

- `test_no_documentation_installs_a_name_that_belongs_to_someone_else` fails if
  any Markdown code block installs `commitguard` or `commitguard-cli` from an
  index rather than a Git source;
- `test_the_distribution_name_is_not_one_that_belongs_to_someone_else` pins the
  name in `pyproject.toml`, which is what `twine upload` would claim.

The release workflow rejects an sdist containing `.kilo`, `.hypothesis`,
`node_modules` or a generated dataset.
