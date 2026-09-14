# Configuration

> Status: **implemented**: layered loading, strict validation, `--config`,
> `commitguard init`, `commitguard policy list`.

## Precedence

Layers are applied lowest first; each later layer overrides only the policy
fields it sets:

| # | Layer | Location |
|---|---|---|
| 1 | built-in defaults | `commitguard.policies.defaults` (identical to `config/default.yaml`, enforced by tests) |
| 2 | global | `$XDG_CONFIG_HOME/commitguard/config.yaml`, default `~/.config/commitguard/config.yaml` |
| 3 | repository | `<repository root>/.commitguard.yaml` (or `.commitguard.yml`; both at once is an error) |
| 4 | explicit | `--config PATH` on `scan`, `check`, `policy list` |

Missing optional layers are skipped. An explicitly requested file that is
missing, or any layer that is invalid, is an error (exit code 2).
`commitguard policy list` prints the layers that were applied.

Parent directories are never searched for repository configuration.

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
| `version` | integer `1` | yes | `"1"` or `true` are rejected |
| `policies` | mapping | no | keys must be known policy IDs |
| `policies.<id>.enabled` | boolean | no | `true`/`false` only |
| `policies.<id>.action` | `allow` \| `warn` \| `block` | no | exact lowercase |

Known policy IDs: `ai_coauthor`, `ai_identity`, `ai_trailer`,
`malformed_trailer`, `bot_identity`.

## Validation (security-relevant)

- unknown top-level or policy keys (`acton: allow`) → error
- unknown policy IDs (`ai_coauthors`) → error listing the known IDs
- unknown actions (`deny`, `BLOCK`) → error naming the field
- wrong types or explicit nulls → error
- duplicate YAML keys → error (a later `action: allow` cannot silently win)
- YAML anchors/aliases, Python object tags → error
- files over 64 KiB or not regular files → error
- configuration never names code, commands or plugins to run

Example error:

```text
commitguard: error: .commitguard.yaml: invalid configuration:
  - policies.ai_coauthor.action: Input should be 'allow', 'warn' or 'block'
```

## Profiles

| File | Purpose |
|---|---|
| `config/default.yaml` | same as built-in defaults and `commitguard init` output |
| `config/strict.yaml` | everything blocks |
| `config/examples/allow-ai.yaml` | explicitly allow AI attribution (findings still reported) |
| `config/examples/block-ai-coauthors.yaml` | block AI co-authors, warn on the rest |
| `config/examples/enterprise.yaml` | organisation baseline |

Use one directly: `commitguard scan --config config/strict.yaml`.

## Rules vs. configuration

Configuration decides **what to do** with findings. **What is detected** comes
from the rule files in `rules/` (see [detection-engine.md](detection-engine.md)).
Repository-specific rule extensions are not supported yet.

## Trust caveat

Repository configuration is controlled by whoever can commit, including the
author of the commit being checked, and a repository layer can loosen a global
one. That is acceptable locally (local checks are advisory). For enforcement,
Phase 4 reads the policy from the protected base branch, never from the pull
request head. See [threat-model.md](threat-model.md).
