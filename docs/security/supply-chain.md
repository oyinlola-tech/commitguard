# Supply chain

What protects the code CommitGuard is built from, and the code you install.
Only what exists today is listed as implemented; the gaps are listed as gaps.

## The name collision on PyPI

**CommitGuard is not published to PyPI.** The name `commitguard` on PyPI belongs
to an unrelated project (a Git hooks library by another author, versions up to
2.2.0), which also installs a `commitguard` console script. Anyone who follows a
`pip install commitguard` instruction gets that project instead of this one.

Mitigations in place:

- every install instruction names a Git source, pinned to a full commit SHA;
- a test fails if any documentation or example reintroduces a PyPI install line
  (`tests/integration/test_examples.py`);
- the CLI's own "install the optional dependencies" message names the Git source.

Open decision for the maintainer: if CommitGuard is ever published, it needs a
distribution name that is free. That choice is deliberately not made here.

## Dependencies

| | Implemented |
|---|---|
| Few runtime dependencies | typer, pydantic, PyYAML (+ `tzdata` on Windows); `cryptography` only for the App service |
| Version pinning for CI installs | `requirements/ci.in` pins exact versions |
| Hash pinning | `requirements/ci.txt` carries sha256 digests; installed with `--require-hashes --no-deps` |
| Vulnerability scanning | `pip-audit` on every push and pull request, and weekly (`.github/workflows/security.yml`) |
| Dependency review on pull requests | `actions/dependency-review-action`, pinned by SHA |
| Lockfile for the dashboard | `web/package-lock.json` |
| Static security analysis | `bandit -ll` and ruff's `S`/`BLE` rules in CI |

## CI integrity

| | Implemented |
|---|---|
| Every Action pinned by commit SHA | yes, with the version in a comment |
| Least-privilege tokens | `permissions: contents: read` at workflow level |
| No credentials in the checkout | `persist-credentials: false` everywhere |
| No `pull_request_target` | correct: fork pull requests never run with repository secrets |
| The scanner runs from trusted code | `.github/workflows/commitguard.yml` installs CommitGuard from the **base** commit via `git worktree`, so a pull request cannot weaken the scanner that judges it |
| Hash-pinned dependency install in the Action | `action.yml` installs with `--require-hashes` |

A review of the workflows against untrusted contributions is in
[ci-pipeline-security.md](ci-pipeline-security.md).

## Gaps (not implemented)

Stated plainly because a supply-chain page that only lists strengths is
marketing:

| Gap | Status |
|---|---|
| Signed releases (Sigstore or GPG) | **Planned.** No release has been published yet |
| Published SBOM | **Planned.** To be generated during the release workflow |
| Artifact checksums | **Planned.** `SHA256SUMS` alongside release artifacts |
| Automated dependency updates (Dependabot or Renovate) | **Not configured.** Updates are manual today |
| Secret scanning in CI | **Not configured.** GitHub's own secret scanning applies to the public repository |
| Reproducible builds | **Not attempted** |
| Third-party audit | **None** |

## Verifying what you installed

```bash
pip show commitguard                   # check the version and location
python -c "import commitguard, pathlib; print(pathlib.Path(commitguard.__file__).parent)"
commitguard --version
```

If you installed from a pinned commit, `git rev-parse HEAD` in the source you
built from is the whole provenance story today. That is a weaker guarantee than a
signed release, which is why the gap is listed above rather than glossed over.
