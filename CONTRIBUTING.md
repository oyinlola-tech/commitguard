# Contributing to CommitGuard

Thanks for your interest. CommitGuard is a security tool, so correctness and
honest documentation matter more than speed.

CommitGuard is pre-alpha (`0.1.0.dev0`) and has a single maintainer
([@oyinlola-tech](https://github.com/oyinlola-tech)). Reviews happen when the
maintainer has time; there is no guaranteed response time.

- Security vulnerabilities and detection bypasses: **do not open a public
  issue**. Follow [SECURITY.md](SECURITY.md).
- Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- Maintainer documentation: [docs/maintainers/](docs/maintainers/README.md).

## Contents

- [Development setup](#development-setup)
- [Architecture overview](#architecture-overview)
- [Ground rules](#ground-rules)
- [Running tests](#running-tests)
- [Lint, format and type check (as CI runs them)](#lint-format-and-type-check-as-ci-runs-them)
- [Web dashboard development](#web-dashboard-development)
- [Security testing expectations](#security-testing-expectations)
- [Benchmarking](#benchmarking)
- [Documentation expectations](#documentation-expectations)
- [Commit requirements](#commit-requirements)
- [Pull request process](#pull-request-process)
- [Review requirements for high-risk components](#review-requirements-for-high-risk-components)
- [Dependencies and CI pins](#dependencies-and-ci-pins)
- [Adding an AI identity](#adding-an-ai-identity)
- [Licensing of contributions](#licensing-of-contributions)

## Development setup

Requirements: Python 3.12+ and Git 2.31+ (Git 2.45+ to run the GitHub App
service). Node.js 20.19+ only if you work on the dashboard in `web/`.

CommitGuard is **not published on PyPI**. The PyPI project named `commitguard`
is an unrelated project; never `pip install commitguard` expecting this
repository. Work from a clone:

```bash
git clone https://github.com/oyinlola-tech/commitguard.git
cd commitguard
./scripts/install-dev.sh          # creates .venv and runs pip install -e ".[dev]"
source .venv/bin/activate
commitguard --version             # commitguard 0.1.0.dev0
commitguard doctor
```

`scripts/install-dev.sh` is a bash script that uses `$PYTHON` or `python3`. On
Windows (or without bash) do the same by hand:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
```

The `dev` extra installs pytest, ruff, mypy, types-PyYAML and `cryptography`
(needed by the GitHub App code and its tests).

Optionally enforce CommitGuard on your own clone, which is what this repository
enforces in CI (see [Commit requirements](#commit-requirements)):

```bash
commitguard install
```

## Architecture overview

```text
src/commitguard/
  cli/            Typer CLI; the only layer that prints
  core/           detection engine, findings, decisions        (pure)
  detectors/      coauthor, identity, trailer, bot             (pure)
  policies/       policy model, evaluator, governance resolver (pure)
  provenance/     identity and trailer parsing                 (pure)
  rules/          rule models and matcher; rules/loader.py reads rules/*.yaml
  config/         layered, strictly validated .commitguard.yaml
  git/            hardened Git wrapper, hook rendering and installation
  services/       analysis, hooks, CI, scan and enforcement services
  ci/             provider-neutral CI context
  github/         Action support (ci github), GitHub App: webhooks, auth,
                  REST client, Check Runs, SQLite state store, workers
  api/            /api/v1 WSGI application and dashboard hosting
  controlplane/   dashboard services: access, sessions, results, policies
  governance/     organization policy hierarchy, rollouts, exceptions, posture
  notifications/  outbox, dispatcher, in-app / e-mail / webhook channels
  audit/, observability/, security/, exceptions/, utils/
  research/       benchmarks and datasets (commitguard benchmark ...)
web/              React 19 + TypeScript dashboard
rules/            bundled rule data (packaged into the wheel)
hooks/            generated reference copies of the hook scripts
benchmarks/       versioned datasets and immutable benchmark results
```

Start with [docs/architecture.md](docs/architecture.md). The package map and
its dependency rules for maintainers are in
[docs/maintainers/architecture.md](docs/maintainers/architecture.md).
Import boundaries are enforced by `tests/unit/test_architecture.py`.

## Ground rules

1. **Detectors find, policies decide.** A detector must never block, warn or
   read configuration. See `docs/detection-engine.md`.
2. **Hooks are thin.** Installed hook scripts and `commitguard hook` must not
   contain detection logic; they call `services/`.
3. **Detectors are pure.** No subprocesses, network, filesystem writes, or Git
   access. `tests/unit/test_architecture.py` enforces the import boundaries.
4. **Fail closed.** When in doubt, an error must lead to BLOCK, not ALLOW.
5. **Treat commit data as hostile.** Sanitise before display
   (`security.sanitization`), validate before use (`security.validation`).
6. **No shell.** External commands go through `utils.subprocess.run_command`
   (Git through `git.commands.run_git`) as argument lists. A test rejects
   `shell=True` anywhere in the package.
7. **No new dependencies** without discussion in an issue.
8. **Do not claim unbuilt features.** Unimplemented code raises
   `NotImplementedError` with its planned phase, and docs say so.
9. **Every new rule ID** needs a default policy in `policies/defaults.py`.
10. **Exit codes are a contract.** The CLI exits `0` (allowed), `1` (blocked)
    or `2` (error) only (`src/commitguard/cli/output.py`). Hooks and CI depend
    on it.
11. **No AI or LLM calls, no telemetry.** Detection is deterministic and
    offline; network access is confined to the GitHub client, the App's HTTP
    server and the notification channels (enforced by
    `test_network_access_is_confined_to_the_github_client_and_server`).

## Running tests

```bash
pytest                                   # everything (what CI runs)
pytest tests/unit                        # fast, no real repositories
pytest -m integration                    # tests under tests/integration/
pytest -m security                       # security regression suite
pytest -m "not integration"              # skip real Git
pytest tests/unit/test_architecture.py   # import boundaries only
```

Markers are declared in `pyproject.toml` (`--strict-markers` is on):

| Marker | How it is applied | Meaning |
|---|---|---|
| `integration` | automatically, for every test under `tests/integration/` (`tests/conftest.py`) | runs real Git or the filesystem beyond `tmp_path` basics |
| `security` | automatically, for every test under a path in `SECURITY_TEST_PATHS` (`tests/conftest.py`) | security regression suite |
| `network` | explicitly | needs PyPI; runs only with `COMMITGUARD_NETWORK_TESTS=1` |
| `phase2`, `phase3`, `phase5` | explicitly | behavioural specifications for a phase (none are open today) |

Conventions:

- Unit tests in `tests/unit/` must not need a real repository.
- Tests that run Git go in `tests/integration/` and use the isolated `git_repo`
  fixture. Every test runs with Git isolated from your global and system
  configuration (`isolated_git_environment` in `tests/conftest.py`).
- `xfail_strict = true`: a specification for unimplemented behaviour is a test
  marked `xfail(strict=True)`; when the feature lands, the test passes, strict
  xfail fails the run, and you remove the marker.
- Commit fixtures live in `tests/fixtures/commits/*.yaml`; policy fixtures in
  `tests/fixtures/policies/`; webhook payloads in `tests/fixtures/webhooks/`.
- GitHub behaviour is tested offline in `tests/integration/github/` with a bare
  "GitHub" remote and real event payloads. The App and dashboard are tested
  against an offline model of the GitHub API, not github.com.
- `tests/integration/github/test_action_scripts.py` runs the Action's shell
  steps and installs hash-pinned dependencies from PyPI; it runs only with
  `COMMITGUARD_NETWORK_TESTS=1` (CI runs it on Linux, Python 3.12).

## Lint, format and type check (as CI runs them)

`.github/workflows/ci.yml`, job **Lint and type check** (Ubuntu, Python 3.12):

```bash
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy
```

Job **Test** runs `python -m pytest` on `ubuntu-latest`, `macos-latest` and
`windows-latest` with Python 3.12 and 3.13, using bash as the shell on every
platform.

`.github/workflows/security.yml` additionally runs:

```bash
pip-audit --skip-editable        # after python -m pip install -e . pip-audit
ruff check --select S,BLE .
bandit -r src -ll
```

and, on pull requests, GitHub's dependency review action. Run the ruff and
bandit commands locally before opening a pull request that touches `src/`.

Markdown files are also formatted: Python code blocks in `.md` files are
checked by `ruff format --check`, so keep examples in them formatted (or use
`bash`/`text`/`yaml` blocks).

## Web dashboard development

The dashboard lives in `web/` (React 19, TypeScript, Vite; Node.js 20.19+).
See [web/README.md](web/README.md) and [docs/dashboard.md](docs/dashboard.md).

```bash
cd web
npm ci
npm run dev          # http://localhost:5173, proxies /api and /health to 127.0.0.1:8080
npm test             # Vitest + Testing Library
npm run typecheck    # tsc -b
npm run lint         # ESLint (react-hooks, jsx-a11y)
npm run build        # production bundle in web/dist
npm run e2e          # Playwright (Chromium) against tests/e2e/dashboard_harness.py; build first
```

`npm run e2e` starts the real service stack from `../.venv`, so run
`scripts/install-dev.sh` first. Set `CHROMIUM_PATH` to use an installed
Chromium. `npm run screenshots` regenerates the README images.

**The dashboard checks are not run by CI today.** Run all of the commands
above locally when you change `web/` or the `/api/v1` API, and say so in the
pull request.

A local demo stack (offline model of GitHub, no github.com access):

```bash
python tests/e2e/dashboard_harness.py --demo      # http://localhost:4173, after npm run build
```

## Security testing expectations

- **Every security fix needs a regression test that carries the `security`
  marker.** The marker is applied by location: put the test in a file or
  directory listed in `SECURITY_TEST_PATHS` in `tests/conftest.py` (for
  example `tests/unit/security/`, `tests/integration/github/security/`,
  `tests/integration/github/app/security/`), or add the new path to that
  tuple in the same pull request. Confirm with
  `pytest -m security --collect-only -q`.
- The test must fail without the fix. Say in the pull request that you checked
  this.
- Security experiments can record what they observed with the `observe`
  fixture (`tests/conftest.py`): outcome `prevented`, `detected`, `bypassed`
  (a documented limitation) or `not_applicable`, with every field filled from
  measured values.
- Detection bypasses: add the case to a **new** dataset version in
  `src/commitguard/research/datasets.py` before fixing the parser, so the
  benchmark shows the failure and then the fix (see
  [Benchmarking](#benchmarking)).
- Tests that demonstrate a limitation (for example `git commit --no-verify`
  skips hooks) must keep asserting the real behaviour. Do not weaken them to
  make the product look stronger.
- Never commit real tokens, private keys or webhook secrets, including in
  fixtures. Use obviously fake values.
- Reporting a vulnerability you found while contributing: follow
  [SECURITY.md](SECURITY.md) instead of opening a pull request that describes
  it.

## Benchmarking

`commitguard benchmark` measures CommitGuard itself; it needs no credentials
and does not contact the network.

```bash
commitguard benchmark detection                 # accuracy on the labelled dataset
commitguard benchmark detection --dataset benchmarks/datasets/v1.1.0
commitguard benchmark performance --quick       # latency, CPU, memory (smoke)
commitguard benchmark hooks                     # git commit/push with and without hooks
commitguard benchmark repository --sizes 100,1000
commitguard benchmark platform                  # local enforcement path on this OS
commitguard benchmark detection --record benchmarks/results
```

Rules:

- **Recorded results are immutable.** `--record` writes a new file under
  `benchmarks/results/raw/<benchmark>/` and refuses to overwrite an existing
  one. Never edit, rename, delete or "refresh" a recorded result. A newer run
  is a new file; `benchmarks/results/processed/index.json` is regenerated from
  `raw/` and is not edited by hand.
- **Datasets are versioned and only grow.** Do not change the cases of an
  existing version; add a version. Labels come from the documented semantics,
  not from what the implementation currently does.
- Record benchmarks only from a known source revision, and say in the pull
  request which machine and command produced them. The result manifest records
  the source revision and whether the tree had uncommitted changes.
- Do not present benchmark numbers from one machine as general performance
  claims.

Details: [docs/maintainers/benchmarking.md](docs/maintainers/benchmarking.md).

## Documentation expectations

- Documentation must match the implementation in the same pull request. If
  you change behaviour, flags, exit codes, API responses, permissions or
  defaults, update the docs that describe them.
- Label anything that is not implemented or not verified: **Planned**,
  **Experimental**, **In progress**, or "not tested against github.com". Do
  not describe designs as if they were built.
- Never claim users, deployments, evaluations, benchmarks or certifications
  that have not happened. No external production deployments have been
  recorded yet.
- Installation instructions install from source (a Git commit or a clone),
  never from PyPI.
- Prefer `bash`, `text` and `yaml` code blocks; Python blocks are formatted by
  ruff.
- Update `CHANGELOG.md` (by hand, "Keep a Changelog" format) under
  `[Unreleased]` for user-visible changes.

## Commit requirements

This repository enforces CommitGuard on itself.
`.github/workflows/commitguard.yml` runs the check named **`commitguard`** on
every `pull_request`, `merge_group` and `push`, installing CommitGuard from a
trusted commit (the pull request base), so a pull request cannot weaken the
scanner. The repository has no `.commitguard.yaml`, so the built-in defaults
apply:

| Policy | Action | Example that triggers it |
|---|---|---|
| `ai_coauthor` | block | `Co-authored-by: Claude <noreply@anthropic.com>` |
| `ai_identity` | block | an AI agent as commit author or committer |
| `ai_trailer` | block | `Generated-by:` / `Assisted-by:` trailers, AI tool footers |
| `malformed_trailer` | warn | a trailer-like line that does not parse |
| `bot_identity` | warn | a bot account as author, committer or co-author |

Therefore:

- **Do not add AI co-author trailers or other AI attribution trailers or
  footers** to commits in this repository. Many AI coding tools add them by
  default; turn that off or remove them before pushing. A pull request with a
  failing `commitguard` check is not merged.
- Commits must be authored under your own identity.
- To fix a blocked commit, rewrite it (`git commit --amend`, or
  `git rebase -i <commit>^` and `reword`), then force-push your branch.
  CommitGuard never rewrites commits itself.
- `commitguard install` in your clone gives you the same result locally before
  you push.
- History uses Conventional Commit prefixes (`feat:`, `fix:`, `docs:`,
  `test:`, `refactor:`, `chore:`). Please follow them; they are not enforced
  by tooling.
- Keep commits focused; do not mix formatting-only changes with behaviour
  changes.

## Pull request process

1. For anything beyond a small fix, open an issue first (bug report or feature
   request form) so scope can be agreed. New dependencies always need an issue.
2. Create a branch in your fork. Keep the pull request to one concern.
3. Run locally: `ruff check .`, `ruff format --check .`, `mypy`, `pytest`
   (and the `web/` checks if you touched the dashboard).
4. Fill in the pull request template: summary, tests, security impact,
   documentation.
5. CI must pass: **Lint and type check**, all **Test** matrix jobs,
   **commitguard**, and the **Security** workflow jobs.
6. The maintainer reviews. Expect questions on anything in the high-risk list
   below. Requests may be declined, including well-implemented ones, when they
   do not fit the project's scope.
7. The maintainer merges; there is no merge queue on this repository today.

## Review requirements for high-risk components

`.github/CODEOWNERS` lists the paths below. Pull requests that touch them get
a security-focused review; describe the security impact explicitly and include
tests.

| Component | Paths | What reviewers check |
|---|---|---|
| Detection and provenance | `src/commitguard/detectors/`, `provenance/`, `core/`, `rules/`, `rules/*.yaml` | bypasses, false positives, fail-closed behaviour, bounded parsing |
| Policy and configuration | `src/commitguard/policies/`, `config/`, `governance/` | silent weakening, precedence, mandatory floors, strict validation |
| Git and hooks | `src/commitguard/git/`, `services/`, `hooks/`, `utils/subprocess.py` | no shell, option injection, hook integrity, bypass honesty |
| GitHub integration | `src/commitguard/github/` (webhooks, auth, client, storage), `ci/`, `action.yml` | signature verification, token scope, trusted policy source, tenant isolation |
| Control plane | `src/commitguard/api/`, `controlplane/`, `notifications/channels/`, `web/src/api/`, `web/src/auth/` | authentication, authorization, CSRF, SSRF, secrets in responses and logs |
| Supply chain | `.github/workflows/`, `requirements/`, `pyproject.toml` | SHA pinning, hash pinning, permissions, untrusted interpolation |
| Security process | `SECURITY.md`, `tests/conftest.py`, `tests/unit/test_architecture.py`, `benchmarks/results/` | suite membership, boundary tests, result immutability |

## Dependencies and CI pins

- Changing runtime dependencies requires regenerating `requirements/ci.txt`
  (hashes for every published file of each pinned version; see
  `requirements/ci.in`).
- GitHub Actions must be pinned to full commit SHAs with the tag in a comment;
  `tests/unit/github/test_workflows_static.py` enforces this.
- Workflow `run:` scripts must receive untrusted values through `env:`, never
  `${{ }}` interpolation.

## Adding an AI identity

Open an "AI agent identity" issue with a link to a public commit showing the
attribution. Entries in `rules/ai-identities.yaml` are only `verified: true`
with such evidence.

## Licensing of contributions

CommitGuard is released under the [MIT License](LICENSE). By submitting a
contribution you agree that it is licensed under the same MIT License
(inbound = outbound). There is no Contributor License Agreement and no
Developer Certificate of Origin sign-off requirement. Only submit work you
have the right to contribute.
