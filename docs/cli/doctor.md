# `commitguard doctor`

Report what is installed, what is valid and what is actually enforced.

```text
commitguard doctor [--json]
```

## What it checks

| Section | Checks |
|---|---|
| CommitGuard | version, Python version and the interpreter that will run the hooks |
| Runtime dependencies | `yaml`, `pydantic`, and `cryptography` (only needed for the App service) |
| Git | Git is available and new enough (2.31+) |
| Repository | a Git work tree was found, and where its Git directory is |
| Configuration | the layers that apply, and whether they parse |
| Detection engine | a self-test: a known AI co-author must be detected |
| Policies | how many are enabled, and how many block |
| Rules | the bundled rule data that is actually loaded |
| Hooks | presence, integrity (checksum), executability, and the interpreter each hook uses |
| Enforcement | hooks disabled in configuration |
| GitHub enforcement | whether a workflow runs CommitGuard, and problems with it |
| GitHub App | whether an App service is configured **on this machine** |

## Statuses

| Status | Meaning |
|---|---|
| `PASS` | the check succeeded |
| `WARNING` | works, but enforcement is weaker than it looks |
| `FAIL` | something is broken or missing |
| `NOT CONFIGURED` | the capability is simply not set up here |
| `INFO` | a neutral fact |

`NOT CONFIGURED` is deliberately not a pass. A GitHub integration that is not set
up enforces nothing, and `doctor` will not imply otherwise. Equally, the presence
of App settings is reported as `INFO`, not as a working installation - only
`commitguard github validate` can check that, because it talks to GitHub.

The last two lines summarise:

```text
Enforcement: LOCAL ENFORCEMENT ONLY
Status: HEALTHY
```

`Enforcement` is one of `LOCAL + GITHUB ENFORCEMENT READY (branch protection not
verified)`, `LOCAL ENFORCEMENT ONLY`, `GITHUB WORKFLOW READY, LOCAL ENFORCEMENT
INCOMPLETE (branch protection not verified)` or `NO COMPLETE ENFORCEMENT LAYER`.
Branch protection is never claimed: CommitGuard cannot see it from your machine.

`Status` is `HEALTHY`, `DEGRADED` (a warning) or `UNHEALTHY` (a failure).

## `--json`

```bash
commitguard doctor --json
```

```json
{
  "commitguard_version": "0.1.0.dev0",
  "status": "HEALTHY",
  "enforcement": "LOCAL ENFORCEMENT ONLY",
  "counts": {"PASS": 14, "INFO": 1, "NOT CONFIGURED": 2, "WARNING": 0, "FAIL": 0},
  "checks": [{"section": "Hooks", "status": "PASS", "detail": "pre-commit installed", "remediation": null}]
}
```

## Exit codes

| Code | When |
|---|---|
| `0` | `HEALTHY` or `DEGRADED` |
| `2` | `UNHEALTHY`: at least one check failed |
