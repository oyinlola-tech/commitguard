# `commitguard policy`

```text
commitguard policy list [--config FILE]
```

Shows the effective policy for every rule and the configuration layer each value
came from, so you can see *why* something is blocked.

Layers, lowest precedence first:

1. built-in defaults
2. `~/.config/commitguard/config.yaml` (global)
3. `.commitguard.yaml` in the repository
4. `--config FILE`
5. a **mandatory policy**, when the GitHub App service or an organization sets
   one: applied last, and it can only make enforcement stricter

A policy you do not mention keeps its secure default. Invalid configuration is an
error (exit 2), never an implicit "allow".

The built-in policies:

| Rule | Default | What it means |
|---|---|---|
| `ai_coauthor` | block | a known AI agent recorded as a co-author |
| `ai_identity` | block | the author or committer is a known AI agent |
| `ai_trailer` | block | an agent's tool footer or generation trailer |
| `malformed_trailer` | warn | a trailer that is broken or disguised |
| `bot_identity` | warn | an automation account (a bot, not an AI agent) |

See [../configuration.md](../configuration.md) for the file format and
[../policy-engine.md](../policy-engine.md) for how decisions combine
(`block > warn > allow`, independent of detector order).

## Exit codes

| Code | When |
|---|---|
| `0` | the policy was printed |
| `2` | invalid configuration or rules |
