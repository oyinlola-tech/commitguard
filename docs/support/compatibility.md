# Compatibility matrix

> Status: pre-alpha (`0.1.0.dev0`). Written 2026-09-17 from the code, the CI
> configuration and the CI and benchmark results that existed on that date.
> CI results change with every push: the live status is on the
> [Actions page](https://github.com/oyinlola-tech/commitguard/actions).
> The rules behind this matrix are in
> [compatibility-policy.md](compatibility-policy.md) and
> [maintenance-policy.md](maintenance-policy.md).

## What the categories mean

| Category | Meaning |
|---|---|
| **Supported** | Within the project's stated requirements; bugs are accepted and fixed on `main` as maintainer time allows. Implies nothing about a warranty or response time. |
| **Tested** | An automated test run on that configuration is recorded (a CI job, or a recorded benchmark result), with the source cited. |
| **Expected to work** | Meets the minimum the code checks for, but no test run on that configuration is recorded. |
| **Experimental** | Implemented, but not validated in a real environment (for example not against github.com), or behaviour may change without notice. |
| **Unsupported** | Known not to work, refused by CommitGuard, or out of scope. Reports are welcome but may be closed. |

"Supported" and "Tested" are separate on purpose: something can be supported
without a recorded test run, and a test run does not by itself make a
configuration supported.

## CommitGuard versions

| Version | Status | Notes |
|---|---|---|
| `main` branch (`0.1.0.dev0`) | **Supported**, pre-alpha | The only supported line. Install from a specific commit. |
| Tagged releases | none exist | No release or tag has been published. The planned release process is described in [../maintainers/release-process.md](../maintainers/release-process.md) (not yet exercised). |
| `commitguard` on PyPI | **Unsupported: a different project** | The PyPI name `commitguard` belongs to an unrelated project. Do not install it expecting this repository. |

Installation methods:

| Method | Status |
|---|---|
| `pipx install "git+https://github.com/oyinlola-tech/commitguard@<commit-sha>"` | Supported (pin a full commit SHA) |
| `python -m pip install "git+https://github.com/oyinlola-tech/commitguard@<commit-sha>"` in a virtual environment | Supported |
| Clone and `python -m pip install -e ".[dev]"` (`scripts/install-dev.sh`) | Supported, **Tested** (this is what CI does) |
| GitHub Action `uses: oyinlola-tech/commitguard@<commit-sha>` | Supported; installs from the Action's own source with hash-pinned dependencies |
| GitHub App extra (`cryptography`) | Supported from a clone or Git URL with the `app` extra, for example `pip install "commitguard[app] @ git+https://github.com/oyinlola-tech/commitguard@<commit-sha>"` |
| Container images, OS packages, binaries | Unsupported: none are provided |

## Python

`pyproject.toml`: `requires-python = ">=3.12"`; classifiers list 3.12 and
3.13. CI (`.github/workflows/ci.yml`) runs the test suite with Python 3.12 and
3.13 on Ubuntu, macOS and Windows.

| Python | Status | Evidence |
|---|---|---|
| 3.12 | **Supported, Tested** | CI run [35235896924](https://github.com/oyinlola-tech/commitguard/actions/runs/35235896924) (commit `a89d591`, 2026-09-17): pytest passed on `ubuntu-24.04` (3.12.14), `macos-26-arm64` (3.12.10) and `windows-2025-vs2026` (3.12.10). The lint and type-check job is also pinned to 3.12. |
| 3.13 | **Supported, Tested** | Same CI run: pytest passed with 3.13.15 on all three images. Local benchmark results in `benchmarks/results/raw/` were recorded with 3.13.15 on Linux. |
| 3.14 and later | Not tested | `requires-python` has no upper bound, so pip will install it; no test run is recorded and it is not claimed to work. |
| 3.11 and earlier | **Unsupported** | Refused by `requires-python`; `scripts/install-dev.sh` exits with an error; the Action's `python-version` input documents 3.12 or newer. |
| PyPy and other implementations | Not tested | Only CPython is used in CI. |

## Git

| Component | Minimum | Enforced where |
|---|---|---|
| CLI and local hooks | **2.31** | `MINIMUM_GIT_VERSION = (2, 31)` in `src/commitguard/git/commands.py` (`rev-parse --end-of-options` and `--path-format=absolute`); `commitguard doctor` reports older Git as a failure |
| GitHub App service (partial mirrors) | **2.45** | `MINIMUM_MIRROR_GIT_VERSION = (2, 45)` in `src/commitguard/github/repositories.py` (`GIT_NO_LAZY_FETCH`); checked by `commitguard github validate` |

The Git version on CI runners is **not pinned**: the workflow prints
`git --version` and uses whatever the runner image provides.

| Git | CLI and hooks | App service |
|---|---|---|
| 2.55.0 (Linux, macOS Homebrew) and 2.55.0.windows.5 | **Tested**: CI run 35235896924 | **Tested** offline (App integration tests in the same run) |
| 2.53.0 | **Tested** locally: recorded `commitguard benchmark platform` result (Linux) in `benchmarks/results/raw/platform/` | not recorded |
| 2.45 – 2.54 | Expected to work | Expected to work |
| 2.31 – 2.44 | Expected to work | **Unsupported** (the service needs 2.45) |
| below 2.31 | **Unsupported** | **Unsupported** |

## Operating systems

Local enforcement uses POSIX `sh` hook scripts that Git runs through `sh` on
Linux and macOS and through Git for Windows' bundled `sh` on Windows (see
[../git-hooks.md](../git-hooks.md#cross-platform-behaviour)).

| OS | CLI and local hooks | Evidence and notes |
|---|---|---|
| Linux (x86-64) | **Supported, Tested** | CI `ubuntu-latest` (`ubuntu-24.04`) test suite; a local `commitguard benchmark platform` run on Linux recorded 15 PASS, 0 FAIL, 0 SKIPPED (`benchmarks/results/raw/platform/`, recorded from a working tree with uncommitted changes) |
| macOS (Apple silicon) | **Supported, Tested** by the test suite | CI `macos-latest` (`macos-26-arm64`) test suite passed. The platform validation benchmark on macOS is **pending** until the platform CI job runs. |
| macOS (Intel) | Expected to work | No run recorded. |
| Windows (x86-64, Git for Windows) | **Supported, Tested** by the test suite | CI `windows-latest` (`windows-2025-vs2026`) test suite passed, with tests run under bash; a few POSIX-only tests are skipped there (executable bits, a POSIX editor script). The platform validation benchmark on Windows is **pending** until the platform CI job runs. |
| Windows without Git for Windows' `sh` | **Unsupported** | Hooks need the `sh` that Git for Windows ships. |
| WSL | Expected to work as Linux | No run recorded. |
| Other Unix (BSDs) | Not tested | |

| Component | Linux | macOS | Windows |
|---|---|---|---|
| GitHub Action (`action.yml`, bash steps) | **Tested** (this repository's `commitguard` workflow runs on `ubuntu-latest`; `test_action_scripts.py` in CI on Linux) | Experimental: not exercised | Experimental: not exercised |
| GitHub App service (`commitguard github serve`) | **Experimental**: tested offline in CI; deployment documentation targets Linux hosts (systemd, gunicorn) | Experimental | Experimental |
| Dashboard build (`web/`, Node.js 20.19+ at build time) | Tested locally: built for the Vitest and Playwright runs recorded in `docs/evidence/tests.json` (2026-09-15) | not recorded | not recorded |

## GitHub integration

Only **github.com** is supported. The API base (`https://api.github.com`) is
not configurable at deployment time, so GitHub Enterprise Server and GitHub
Enterprise Cloud with data residency are **Unsupported**.

The App service and dashboard are **Experimental**: they are tested against an
offline model of the GitHub API, webhooks and OAuth flow, not against
github.com ([../github-app.md](../github-app.md)).

### GitHub Action

| Requirement | Value |
|---|---|
| Workflow permissions | `contents: read` only; no secrets or token |
| Checkout | `actions/checkout` with `fetch-depth: 0` |
| Events | `pull_request`, `merge_group`, `push` (`pull_request_target` is refused) |
| Reference | pinned to a full commit SHA of this repository |
| Required check name | `commitguard` (only blocks merges when branch protection or a ruleset requires it) |

### GitHub App permissions

From `src/commitguard/github/permissions.py`:

| Permission | Level | Required |
|---|---|---|
| Checks | read and write | yes |
| Contents | read | yes |
| Metadata | read | yes |
| Pull requests | read | yes |
| Merge queues | read | optional (merge queue validation) |

Anything more is reported as unnecessary by `commitguard github validate`.
Installation tokens are down-scoped to the four required permissions and to
one repository.

### GitHub App webhook events

| Event | Required |
|---|---|
| `installation`, `installation_repositories` | yes (always sent to Apps) |
| `pull_request`, `push` | yes |
| `check_run`, `check_suite` | optional (GitHub "Re-run" buttons) |
| `merge_group` | optional (merge queues) |

Check Runs published: `commitguard-app` (pull requests and merge groups) and
`commitguard-app/push` (informational). The client sends
`X-GitHub-Api-Version: 2022-11-28` (`src/commitguard/github/client.py`).
Dashboard sign-in uses the App's user authorization (OAuth with PKCE) and
needs the App's client ID and client secret.

## Database (GitHub App service and dashboard)

| Item | Status |
|---|---|
| SQLite through Python's standard-library `sqlite3` | **Supported** (the only backend) |
| SQLite version | 3.38 or newer is documented in the deployment documentation ([../deployment/self-hosted.md](../deployment/self-hosted.md)) (JSON functions `json_each` and `json_extract` are used; WAL mode and `ON CONFLICT` upserts). **There is no runtime check of the SQLite version.** The SQLite bundled with the Python builds used in CI passed the tests; its version is not recorded in the CI logs. Local runs used SQLite 3.53.4. |
| Schema | version 4 (`SCHEMA_VERSION` in `src/commitguard/github/storage.py`), migrated automatically; see [compatibility-policy.md](compatibility-policy.md#database-schema-and-migrations) |
| Several processes on one host | Supported (WAL, `BEGIN IMMEDIATE`) |
| Several hosts sharing one database, network file systems | **Unsupported** |
| PostgreSQL or other databases | **Unsupported** (the storage interfaces allow another implementation later; none exists) |

## Dashboard browsers

| Browser | Status | Evidence |
|---|---|---|
| Chromium / Google Chrome (desktop) | **Tested** locally (16 Playwright tests passed on Linux, `docs/evidence/tests.json`, 2026-09-15) | `web/playwright.config.ts` defines a single `chromium` project (Playwright "Desktop Chrome"); the end-to-end, responsive (including a 390 px wide viewport), accessibility (axe) and security specs run there. The browser tests are **not run in CI**. |
| Microsoft Edge and other Chromium-based browsers | Expected to work | Same engine; not run. |
| Firefox | Expected to work, not tested | The bundle targets ES2022; no Firefox run is configured. |
| Safari (WebKit), iOS and Android browsers | Expected to work, not tested | No WebKit or mobile browser run is configured. |
| Browsers without ES2022 support | **Unsupported** | Build target `es2022` (`web/vite.config.ts`). |

The dashboard also requires cookies and JavaScript, and runs under a strict
Content-Security-Policy served by the App service.

## How to add evidence to this matrix

- Run `commitguard benchmark platform --json` on the platform in question and
  report the result with the
  [evaluation feedback form](../community/external-evaluation.md#how-to-submit-feedback).
- A configuration moves to **Tested** only with a cited CI run or a recorded
  result; it is never promoted on the basis of a report that it "seems to
  work".
