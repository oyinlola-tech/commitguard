# Threat model

> Living document. Status markers: **[done]** implemented and tested,
> **[planned]** designed but not built.

## What CommitGuard protects

The integrity of a repository's **contribution policy**: commits that violate
the policy (initially, AI agent attribution) should not reach protected
branches, and every decision should be explainable with preserved evidence.

## What CommitGuard does not do

- **Prove authorship.** Git metadata is self-asserted. Someone who removes an
  AI trailer and commits under their own name is indistinguishable from a
  human author by metadata alone. CommitGuard enforces policy over *claims*.
- **Detect AI-written code from its content.** "AI generated contribution
  detection" is a future research area and would be probabilistic; it must
  never be presented as deterministic.
- **Replace server-side enforcement.** Local checks are advisory.

## Assets

- the repository policy (`.commitguard.yaml`) and rule data;
- the decision and its evidence;
- the developer's machine and environment (CommitGuard runs inside Git hooks);
- repository contents (must not leave the machine).

## Adversaries

| Adversary | Goal |
|---|---|
| Contributor bypassing policy | get a violating commit merged |
| Malicious commit author (any repo you scan) | crash, mislead, or exploit CommitGuard via crafted metadata |
| Malicious repository content | abuse config/rules to execute code or weaken policy |
| Automated agent | add attribution that evades detection, or strip it |

## Threats and mitigations

### Bypassing local enforcement
- `--no-verify`, removing hooks, other clones. **Accepted locally**; mitigated by
  server-side enforcement **[planned, Phase 4]**. `pre-push` re-scans commits
  created with `--no-verify` **[planned, Phase 3]**.

### Weakening policy through configuration
- PR adds `action: allow`. Mitigation: CI reads policy from the base branch **[planned]**.
- Typos/ambiguity silently disabling a policy: strict schema, unknown keys and
  policy IDs rejected, strict booleans, no nulls **[done]**.
- Duplicate YAML keys overriding earlier values: rejected **[done]**.
- Omitted policies: fall back to secure defaults, never to "off" **[done]**.

### Code execution via configuration or data
- YAML object construction: `SafeLoader` only **[done]**.
- Plugins/commands named in config: not supported by design; explicit
  in-code detector registry **[done]**.
- ReDoS via regex rule data: rule files use literals only **[planned; recorded in rules/patterns.yaml]**.

### Crafted commit metadata
- **Terminal escape injection** (hide a trailer, spoof output): all untrusted
  text is sanitised before display; ESC, C0/C1 and bidi controls made visible **[done]**.
- **Option injection** via revisions (`--output=…`): revisions validated
  (no leading `-`, no control characters) and passed after `--end-of-options` **[done]**.
- **Shell injection**: no shell anywhere; argument vectors only **[done]**.
- **Parser confusion** (NULs, malformed dates/SHAs): NUL-delimited Git output
  with the free-form message last; every structured field validated; mismatch
  raises instead of guessing **[done]**.
- **`git replace` objects** substituting an innocent commit:
  `GIT_NO_REPLACE_OBJECTS=1` **[done]**.
- **`.mailmap` rewriting an AI identity** into a human one: `--no-use-mailmap` **[done]**.
- **Evasion by formatting** (casing, whitespace, malformed trailers, look-alike
  Unicode): **[planned, Phase 2]**, specified as xfail tests.
- **Resource exhaustion** (huge messages): config size-limited **[done]**;
  bounded Git output **[planned]**.

### Detector failure
- Any detector exception or invalid output → BLOCK (fail closed) **[done]**.

### Information disclosure
- No network access in the local tool; no telemetry **[done]**.
- Tracebacks never render local variables (`pretty_exceptions_show_locals=False`) **[done]**.
- Environment variables are never logged **[done — nothing logs them]**.
- Tokens for Phase 4 read at call time, never persisted **[planned]**.

### Repository modification
- Detectors receive data, not a repository handle; architecture tests forbid
  I/O imports in detection layers **[done]**.
- Git reads use `GIT_OPTIONAL_LOCKS=0` **[done]**.
- CommitGuard never rewrites commits or history **[by design]**.
- `init` never overwrites an existing file **[done]**.
