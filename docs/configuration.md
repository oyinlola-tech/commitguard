# Configuration

> Status: schema, loader and validation are implemented. `commitguard init`
> creates the file; `commitguard policy list` and `commitguard doctor` read it.

## Location

CommitGuard reads **one** file from the **repository root**:

- `.commitguard.yaml` (preferred), or
- `.commitguard.yml`

Having both is an error. Parent directories are never searched. With no file,
built-in defaults apply.

## Format

```yaml
version: 1

policies:
  ai_coauthor:
    enabled: true
    action: block
  bot_identity:
    enabled: true
    action: warn
```

| Key | Type | Required | Notes |
|---|---|---|---|
| `version` | integer `1` | yes | `"1"` (string) is rejected |
| `policies` | mapping | no | keys must be known policy IDs |
| `policies.<id>.enabled` | boolean | no | `true`/`false` only; `"yes"` is rejected |
| `policies.<id>.action` | `allow` \| `warn` \| `block` | no | exact lowercase |

Unset fields keep the built-in default for that policy.

## Validation (security-relevant)

Configuration can weaken enforcement, so anything ambiguous is an error:

- unknown top-level or policy keys (`acton: allow`) → error
- unknown policy IDs → error listing the known IDs
- wrong types, explicit nulls → error
- duplicate YAML keys (a second `action:` silently winning) → error
- YAML anchors/aliases → error
- Python-object YAML tags → error (`SafeLoader` only)
- files over 64 KiB, or not regular files → error
- configuration never names code, commands or plugins to run

## Profiles

| File | Purpose |
|---|---|
| `config/default.yaml` | Same as `commitguard init` output |
| `config/strict.yaml` | Everything blocks |
| `config/examples/allow-ai.yaml` | Explicitly allow AI attribution |
| `config/examples/block-ai-coauthors.yaml` | Block AI co-authors, warn on the rest |
| `config/examples/enterprise.yaml` | Organisation baseline |

All of them are validated by the test suite.

## Trust caveat

A repository configuration file is controlled by whoever can commit to the
repository — including the author of the commit being checked. Locally this is
acceptable (local hooks are advisory anyway). For enforcement, Phase 4 reads the
policy from the protected **base** branch, never from the pull request head.
See [threat-model.md](threat-model.md).
