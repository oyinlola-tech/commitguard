# CLI reference

```text
commitguard [--version] <command> [options]
```

| Command | Purpose | Page |
|---|---|---|
| `init` | Set a repository up: configuration, hooks, GitHub workflow | [init.md](init.md) |
| `install` / `uninstall` | Manage the Git hooks | [install.md](install.md) |
| `scan` | Explain the findings for a commit or range | [scan.md](scan.md) |
| `check` | Machine-readable decision, including for a pending message | [scan.md](scan.md#commitguard-check) |
| `doctor` | Report what is installed, valid and actually enforced | [doctor.md](doctor.md) |
| `policy list` | Show the effective policy and where each value came from | [policy.md](policy.md) |
| `hook <name>` | The entry points the installed hooks call | [install.md](install.md#commitguard-hook) |
| `ci github` | The GitHub Actions check | [github.md](github.md#commitguard-ci-github) |
| `github` | Setup guidance, validation and the App service | [github.md](github.md) |
| `dashboard` | Dashboard administration | [../dashboard.md](../dashboard.md) |
| `benchmark` | Reproducible measurements | [benchmark.md](benchmark.md) |
| `reproduce` | Re-run the published evidence | [reproduce.md](reproduce.md) |
| `report security` | Build reports from recorded results | [benchmark.md](benchmark.md#commitguard-report-security) |

## Exit codes

The contract every hook, script and CI job can rely on:

| Code | Meaning |
|---|---|
| `0` | allowed: no findings, or only findings whose policy is `allow` or `warn` |
| `1` | blocked: a finding or a detector failure evaluated to `block` |
| `2` | error: invalid configuration or rules, a Git failure, bad arguments, or an unexpected failure |

Three codes, deliberately. Anything CommitGuard cannot evaluate is an error (2),
never a silent success, and hooks block on errors. Commands that are not a policy
decision (`init`, `install`, `doctor`, `benchmark`, `reproduce`) use `0` for
success and `2` for failure, except where a page below says otherwise.

These codes are part of the public interface: they are documented here, covered
by tests, and will not change meaning in a patch or minor release
([compatibility policy](../support/compatibility-policy.md)).

## Conventions

- **No network.** Only `github` subcommands and `ci github` talk to GitHub.
- **stdout is the result, stderr is diagnostics.** `--format json` prints one
  JSON document with `schema_version`.
- **Untrusted text is sanitised** before printing: control characters cannot
  rewrite your terminal.
- **Nothing is overwritten.** `init` refuses rather than replacing a file, and
  `install` preserves existing hooks by chaining them.
