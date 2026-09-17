# `commitguard reproduce`

Re-run the published evidence on your own machine. This exists so that someone
who is not the author can check the claims.

```text
commitguard reproduce all|security|benchmark|integration|github [--json] [--output FILE]
                      [--evidence-dir DIR] [--results DIR]
```

| Area | What it does | Needs |
|---|---|---|
| `security` | the security regression suite (`pytest -m security`): 350 tests | the repository and pytest |
| `benchmark` | rebuilds the labelled dataset, checks its fingerprint against the published files, re-measures detection | nothing |
| `integration` | the integration suite: Git repositories, hooks, the CI check and the App service against a fake GitHub | the repository and pytest |
| `github` | validates a real GitHub App installation | GitHub App credentials |

## Statuses

| Status | Meaning |
|---|---|
| `PASS` | the step ran and its checks held |
| `FAIL` | the step ran and a check did not hold |
| `SKIPPED` | it could not run, with the reason |
| `NOT RUN` | not selected |

A skipped step is never reported as a pass. Without credentials you get:

```text
SKIPPED  github   GitHub App configuration and permissions
         GitHub credentials not configured (COMMITGUARD_GITHUB_APP_ID, ... not set)
```

Steps that need the test suites are skipped when CommitGuard was installed
without them; clone the repository and run it from there.

## Example

```bash
git clone https://github.com/oyinlola-tech/commitguard && cd commitguard
python -m pip install -e ".[dev]"
commitguard reproduce all --evidence-dir evidence/security --results benchmarks/results
commitguard report security --results benchmarks/results --evidence evidence/security
```

## Exit codes

| Code | When |
|---|---|
| `0` | nothing failed (steps may have been skipped) |
| `1` | a step failed |
| `2` | error |

See [../research/reproducibility.md](../research/reproducibility.md) for what is
fixed (dataset versions and fingerprints, immutable results) and what necessarily
varies (timings, which depend on the machine).
