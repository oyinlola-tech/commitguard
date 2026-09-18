# Release process

> Status: **not yet exercised — no release has been published.** CommitGuard
> has no tags and no GitHub releases yet. It is published to PyPI as
> `commitguardian` (the names `commitguard` and `commitguard-cli` belong to
> unrelated projects; see ADR-009). This page
> describes the release mechanism being added in Phase 10
> (`.github/workflows/release.yml`) and the manual steps around it. Until the
> first release, users install from a pinned commit.

## What a release is

A release is a **draft GitHub release**, created by the release workflow from a
tag and published by the maintainer, containing:

- a source distribution (sdist) and a wheel, built once on Linux;
- `SHA256SUMS` for those files;
- a CycloneDX SBOM.

There is **no PyPI publishing and there are no binaries or container images**.
Artifacts are not signed, and no build provenance attestation is generated.

## Versions and tags

| Item | Where |
|---|---|
| Package version | `pyproject.toml`, `[project].version` |
| Runtime version (`commitguard --version`) | `src/commitguard/__init__.py`, `__version__` |
| Dashboard package version | `web/package.json` (`"private": true`, not released separately; currently `0.1.0`, independent of the Python version) |

Both Python locations must be changed together; today they both say `0.1.0`.
`tests/integration/git/test_cli_commands.py` checks that `commitguard --version`
prints `__version__`; no test compares it with `pyproject.toml`, so check both by
hand. The name in `pyproject.toml` is pinned by
`tests/integration/test_examples.py`, because that name is what `twine upload`
claims on PyPI.

Tags:

| Tag | Meaning | Version in the files |
|---|---|---|
| `vMAJOR.MINOR.PATCH` | final release | `MAJOR.MINOR.PATCH` |
| `vMAJOR.MINOR.PATCHrcN` | release candidate | `MAJOR.MINOR.PATCHrcN` (PEP 440) |

A tag must point at a commit whose files carry exactly the tag's version.
Between releases `main` carries the next `.dev0` version.

## Flow

```text
development (main, X.Y.Z.dev0)
    │  feature complete for X.Y.Z; CHANGELOG [Unreleased] reviewed
    ▼
release candidate
    │  commit: version X.Y.ZrcN, CHANGELOG section "## [X.Y.ZrcN] - YYYY-MM-DD"
    │  tag vX.Y.ZrcN  ──▶ release.yml: validation gate, build, smoke tests, draft release
    ▼
validation
    │  automated gate green + manual checklist below
    │  problems: fix on main, next candidate rcN+1
    ▼
public release
    │  commit: version X.Y.Z, CHANGELOG "## [X.Y.Z] - YYYY-MM-DD"
    │  tag vX.Y.Z ──▶ release.yml ──▶ draft release
    │  maintainer verifies artifacts and publishes the draft
    ▼
back to development
       commit: version X.Y.(Z+1).dev0 (or X.(Y+1).0.dev0), new [Unreleased] section
```

### 1. Development

- All changes land on `main` through pull requests (or reviewed commits by the
  maintainer) with CI green.
- Keep `CHANGELOG.md` `[Unreleased]` current while working, not at release
  time.

### 2. Release candidate

1. Confirm `main` is green: CI, Security and CommitGuard workflows on the
   commit to be released.
2. Update the version in `pyproject.toml` and `src/commitguard/__init__.py` to
   `X.Y.ZrcN`.
3. Move the `[Unreleased]` entries into `## [X.Y.ZrcN] - <date>`. Call out
   anything that can change enforcement (defaults, rules, detectors, schema
   migrations, check names, App permissions).
4. Commit, then create an annotated tag on that commit and push it:

   ```bash
   git tag -a vX.Y.ZrcN -m "CommitGuard X.Y.ZrcN"
   git push origin vX.Y.ZrcN
   ```

5. The release workflow runs (see [Validation gate](#validation-gate)) and, if
   everything passes, creates a draft release.

### 3. Validation

Work through the automated results and the manual checklist. Any failure means
a fix on `main` and a new candidate (`rcN+1`); never move or reuse a tag.

### 4. Public release

1. Update the version to `X.Y.Z`, rename the changelog section, commit, tag
   `vX.Y.Z`, push the tag.
2. When the draft release appears, verify it ([Artifact
   verification](#artifact-verification)).
3. Write release notes from the changelog section, including installation from
   the tag: `pipx install "git+https://github.com/oyinlola-tech/commitguard@vX.Y.Z"`
   (or from the downloaded wheel after checking `SHA256SUMS`).
4. Publish the draft. Mark release candidates as pre-releases.
5. Bump `main` to the next `.dev0` version.

If a published release turns out to be broken, do not delete or retag it.
Publish a fixed patch release and mark the broken one in its release notes;
for a security issue follow
[../security/incident-response.md](../security/incident-response.md) and
[../operations/runbook.md](../operations/runbook.md).

## Validation gate

"Automated" names the workflow that runs the check. Items marked *planned* are
part of `release.yml` as designed and have not run yet.

| Gate | Status | Where / how |
|---|---|---|
| Lint and type check | **Automated** | `ci.yml` job *Lint and type check* (`ruff check .`, `ruff format --check .`, `mypy`) on every push to `main` and pull request; *planned* in `release.yml` |
| Unit tests | **Automated** | `ci.yml` job *Test* (`python -m pytest`) on Ubuntu, macOS, Windows × Python 3.12, 3.13; *planned*: full test run in `release.yml` |
| Integration tests | **Automated** | Same `pytest` run (everything under `tests/integration/`, real Git, offline GitHub model). The PyPI-dependent Action test runs in `ci.yml` on Linux / Python 3.12 only |
| Security tests | **Automated** | Part of every `pytest` run; *planned* as an explicit `pytest -m security` step in `release.yml`; static analysis in `security.yml` (ruff `S`/`BLE`, bandit) |
| Regression (detection) | **Automated** *(planned)* | `commitguard benchmark detection` smoke run in `release.yml` exits `1` on any dataset mismatch |
| Regression (benchmark comparison) | **Manual** | Run `commitguard benchmark compare --results benchmarks/results --benchmark <name>` against the previous recorded run; not part of the release workflow as designed. See [benchmarking.md](benchmarking.md) |
| Cross-platform | **Automated** | `ci.yml` test matrix; *planned*: installed-wheel smoke tests on ubuntu, macOS and Windows in `release.yml`; platform validation (`commitguard benchmark platform`) on macOS and Windows CI is pending |
| CLI | **Automated** | `tests/integration/git/test_cli_commands.py` and hook tests in CI; *planned* in `release.yml`: `--version`, `doctor`, `init`, `install` and a blocked commit with the installed wheel |
| Package | **Partly automated** | No CI job builds the sdist/wheel today (CI installs editable); this repository's `commitguard` workflow does a non-editable install from source on Linux. *Planned*: build sdist and wheel once in `release.yml` and install the wheel |
| Benchmark smoke | **Automated** *(planned)* | `commitguard benchmark detection` in `release.yml` |
| Dashboard (`web/`) | **Manual** | Not in CI: `npm test`, `npm run typecheck`, `npm run lint`, `npm run build`, `npm run e2e` |
| Documentation validation | **Partly automated** | `ruff format --check .` formats Python code blocks in Markdown. No link checker and no check that documented commands and flags match the CLI: **manual** review of `README.md`, changed docs, `CHANGELOG.md` and [../support/compatibility.md](../support/compatibility.md) |
| Secret scanning | **Automated on GitHub** | Repository secret scanning and push protection are enabled (repository settings, checked 2026-09-17). No secret scanner runs inside the workflows or on release artifacts: **manual** check that the sdist contains no local files (`tar tzf dist/*.tar.gz`) |
| Dependency checks | **Automated** | `security.yml`: `pip-audit` (push to `main`, pull requests, weekly), dependency review (pull requests). `web/` dependencies: **manual** (`npm audit`) |
| Artifact validation | **Automated** *(planned)* + **manual** | `release.yml` writes `SHA256SUMS`, generates the SBOM and smoke-tests the built wheel; the maintainer verifies checksums and contents before publishing |
| Self-enforcement | **Automated** | `commitguard.yml` check `commitguard` on the release commits |

## Publishing to PyPI

The distribution name is **`commitguardian`**. The import package and console
script stay `commitguard`, and `commitguard` / `commitguard-cli` on PyPI belong
to other authors ([ADR-009](../adr/009-published-to-pypi-as-commitguardian.md)).

Publishing uses **Trusted Publishing**: PyPI verifies the workflow's OIDC
identity, so there is no API token to create, store or rotate. The `publish` job
carries `id-token: write` and nothing else, and it runs only after the gate, the
build, the cross-platform install smoke test and the draft have all succeeded.
Release candidates (`rc` in the tag) are not published.

### One-time setup

1. On PyPI: **Your projects -> Publishing -> Add a pending publisher**

   | Field | Value |
   |---|---|
   | PyPI project name | `commitguardian` |
   | Owner / repository | `oyinlola-tech` / `commitguard` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

2. In the repository: **Settings -> Environments -> New environment -> `pypi`**.
   Add yourself as a required reviewer, so publishing waits for a human even
   though no credential is involved.
3. Optional but recommended: repeat both steps on
   [TestPyPI](https://test.pypi.org) and do a dry run first.

### What gets published

Only the wheel and the sdist. `SHA256SUMS`, the SBOM and the cross-platform
results travel with the *GitHub* release, not PyPI.

The sdist is built from an allowlist in `pyproject.toml`, and the build job
fails if it contains `.kilo/`, `.hypothesis/`, `node_modules/`, `.venv/`, a
`.env` file, a generated dataset or `.git/`, or if it exceeds 4 MB. This is not
hypothetical: the default configuration once produced an 8.4 MB sdist containing
an editor's scratch worktree - a second copy of the repository. A published sdist
is public and permanent.

### Verifying a publish

```bash
pipx install commitguardian
commitguard --version
commitguard doctor          # bundled rules must load from the installed package
```

## Listing the Action on the GitHub Marketplace

**This cannot be automated.** There is no REST or GraphQL API for an Actions
Marketplace listing, and `gh release create` has no flag for it: the release
object carries no marketplace or category field. Any claim that a workflow can
publish to the Marketplace is wrong.

It costs nothing extra, though, because `release.yml` creates a **draft**. The
checkbox lives on the same form as the **Publish release** button you press to
turn that draft into a release.

On that form:

| Field | Value |
|---|---|
| Publish this Action to the GitHub Marketplace | tick |
| Primary Category | Security |
| Another Category | Code review |

Requirements GitHub enforces before the checkbox appears:

- `action.yml` at the repository root, with `name`, `description` and `branding`
  (all present);
- a README;
- the repository is public;
- the action `name` is unique across the Marketplace;
- you accept the GitHub Marketplace Developer Agreement (once, on first publish).

After the first listing the checkbox stays ticked for later releases. The
Marketplace shows the tag, so a release candidate should **not** be listed.

## Manual release checklist

- [ ] CI, Security and CommitGuard workflows green on the tagged commit
- [ ] Release workflow green; draft release created
- [ ] Version identical in `pyproject.toml`, `src/commitguard/__init__.py`, tag and changelog heading
- [ ] `CHANGELOG.md` section complete; enforcement-affecting changes called out
- [ ] Schema migrations (if any) documented with upgrade and backup notes
- [ ] `pytest -m security` green; any new security fix has a regression test
- [ ] `commitguard benchmark compare` against the latest baseline reviewed; regressions explained or fixed
- [ ] Dashboard checks run locally if `web/` or `/api/v1` changed
- [ ] Documentation matches the release (README install instructions, compatibility matrix, status labels)
- [ ] `pip-audit` clean or findings assessed
- [ ] Artifacts verified (below)
- [ ] `twine check dist/*` passes
- [ ] sdist contains no local files (the build job checks; `tar tzf dist/*.tar.gz` to look)
- [ ] After publishing: `pipx install commitguardian` in a clean environment, then `commitguard doctor`
- [ ] Final releases only: Marketplace checkbox ticked when publishing the draft (not for an rc)

## Artifact verification

```bash
gh release download vX.Y.Z --dir release-check --pattern '*'
cd release-check
sha256sum -c SHA256SUMS
python -m venv /tmp/cg-check && /tmp/cg-check/bin/python -m pip install ./commitguard-*.whl
/tmp/cg-check/bin/commitguard --version
tar tzf commitguard-*.tar.gz | less      # no .env, keys, databases or local artefacts
```

Check that the wheel contains the bundled rules
(`commitguard/rules/data/*.yaml`), and that the SBOM lists the expected runtime
dependencies.

## Hotfixes

Before 1.0 there are no maintenance branches: a hotfix is a normal fix on
`main` followed by a patch release through the same flow. If `main` contains
unreleased changes that should not ship yet, a release branch
`release/X.Y` may be cut from the last release tag; record the decision in the
release notes.
