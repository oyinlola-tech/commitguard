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
| `enforcement.pre_commit` | boolean | no | default `true` |
| `enforcement.commit_msg` | boolean | no | default `true` |
| `enforcement.pre_push` | boolean | no | default `true` |
| `enforcement.max_push_commits` | integer 1–1000000 | no | default `10000`; larger pushes are blocked with an error |

Known policy IDs: `ai_coauthor`, `ai_identity`, `ai_trailer`,
`malformed_trailer`, `bot_identity`.

## Enforcement

```yaml
enforcement:
  pre_commit: true
  commit_msg: true
  pre_push: true
  max_push_commits: 10000
```

Controls which installed Git hooks run the analysis (see
[git-hooks.md](git-hooks.md)). Defaults are the conservative choice: every hook
enforces. Layers merge field by field like policies.

Disabling a hook is intentional and visible: the hook prints
"pre-push enforcement is disabled by configuration" and `commitguard doctor`
reports `! WARNING  pre-push enforcement disabled in configuration` and
"Security enforcement is incomplete." It never changes policies, and it is
never treated as a fully protected state.

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

## Policy sources and trust

Where configuration is read from depends on where CommitGuard runs:

| Context | Layers (lowest first) | Source kind |
|---|---|---|
| `scan`, `check`, `policy list`, Git hooks | built-in → global → work tree `.commitguard.yaml` → `--config` | `working_tree` |
| `commitguard ci github` | built-in → `.commitguard.yaml` **in the trusted commit's tree** (or the `--config` path resolved there) | `revision` |
| CI with no trusted commit (initial push) | built-in only | `builtin` |

The trusted commit is the pull request / merge queue base, the commit before a
push, or the default branch tip for new branches. The global configuration is
not used in CI. A change to `.commitguard.yaml` in a pull request is therefore
**not applied to that pull request**; CI reports it as a notice and it takes
effect once merged.

If the evaluated commits change the configuration in a way that would weaken a
policy (disable it or lower its action), CI and the GitHub App additionally
report **"Security policy modification detected"** listing each change, for
example `ai_coauthor: block -> allow`. The trusted policy is still the one
applied.

The GitHub App uses the same sources and can add a **mandatory policy**, see
below.

Locally, repository configuration is controlled by whoever can commit, and a
repository layer can loosen a global one; that is acceptable because local
checks are advisory. See [github-enforcement.md](github-enforcement.md#policy-trust-model)
and [threat-model.md](threat-model.md).

## Mandatory policy (GitHub App)

`COMMITGUARD_APP_MANDATORY_POLICY_FILE` points to a file with the same schema
as `.commitguard.yaml`. It is a **floor**, not a layer:

- it is applied after the trusted repository configuration;
- every policy it names is enabled, at the more restrictive of its own action
  and the repository's;
- `enabled: false` and `enforcement:` settings are rejected, because a mandatory
  policy can only enforce.

```yaml
version: 1
policies:
  ai_coauthor:
    action: block
  ai_identity: {}        # enabled, with the default action (block)
```

| Repository (trusted) | Mandatory | Effective |
|---|---|---|
| `ai_coauthor: allow` | `ai_coauthor: block` | **block** |
| `ai_identity: enabled: false` | `ai_identity: {}` | **enabled, block** |
| `bot_identity: block` | `bot_identity: warn` | **block** (a floor never relaxes) |

Reports list it in `config_sources` as `mandatory: <file name>`, and the policy
source reads `… + mandatory policy (…)`.

## Organization policy (dashboard)

Admins set an **organization policy** in the dashboard: per rule, a floor of
"Repository decides", "At least WARN" or "Always BLOCK". The GitHub App
combines it with the mandatory policy file (the stricter floor wins) and
applies the result exactly like a mandatory policy. The hierarchy is:

```text
service mandatory policy (file)  ─┐
organization policy (dashboard)  ─┴─ floors: only tighten
trusted repository configuration      .commitguard.yaml at the base / before commit
built-in defaults
```

| Repository (trusted) | Organization floor | Service floor | Effective |
|---|---|---|---|
| `ai_coauthor: allow` | Always BLOCK | — | **block** |
| `bot_identity: allow` | At least WARN | — | **warn** |
| `bot_identity: block` | At least WARN | — | **block** |
| — | Repository decides | `ai_identity: block` | **block** |

Organization policies are versioned; removing or lowering a floor needs a
confirmation, a reason and a recent sign-in. Local hooks and the GitHub
Action do not read the organization policy: it applies to App scans. See
[dashboard.md#policies](dashboard.md#policies).

## GitHub App and dashboard environment

The App is configured through environment variables only (App ID, private key,
webhook secret, data directory, mandatory policy, workers, retention, commit
limit). See [github-app.md](github-app.md#5-configure-the-environment).

The dashboard adds `COMMITGUARD_DASHBOARD_URL`, `COMMITGUARD_GITHUB_CLIENT_ID`,
`COMMITGUARD_GITHUB_CLIENT_SECRET` (or `..._FILE`),
`COMMITGUARD_DASHBOARD_STATIC_DIR`, `COMMITGUARD_DASHBOARD_ALLOWED_ORIGINS` and
`COMMITGUARD_ENV`. See [dashboard.md#running-the-dashboard](dashboard.md#running-the-dashboard).

Notification delivery is off unless configured:
`COMMITGUARD_NOTIFICATIONS_MODE` (`off`, `deliver`, `test`), the SMTP settings
(`COMMITGUARD_SMTP_HOST`, `_PORT`, `_SECURITY`, `_USERNAME`,
`_PASSWORD`/`_PASSWORD_FILE`, `_FROM`),
`COMMITGUARD_NOTIFICATION_SIGNING_KEY` (or `..._FILE`) for signed webhooks and
`COMMITGUARD_NOTIFICATION_RETENTION_DAYS`. In-app notifications need none of
them. `COMMITGUARD_ENV=test` always forces the recording test mode, so a test
environment cannot send real notifications. See
[notifications.md](notifications.md#configuration).

Invalid values stop the service at start-up; error messages name the variable,
never its value.

### Unknown fields

Unknown keys, policy IDs, actions and enforcement fields are **errors**, never
warnings, in every context. In CI an invalid trusted configuration fails the
check (exit 2).
