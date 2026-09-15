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

### Local Git operations (Phase 3)

| Threat | Mitigation / limitation | Status |
|---|---|---|
| Developer **accidentally** commits or pushes an AI-attributed commit | pre-commit and commit-msg hooks block the commit; pre-push blocks every outgoing violating commit, including ones created with `--no-verify`, merges, rebases and tools that skip commit hooks | **[done]** |
| Developer **intentionally** bypasses local hooks (`--no-verify`, deleting hooks, `core.hooksPath`, editing `.commitguard.yaml`, another clone) | **Limitation:** local hooks are user-controlled; CommitGuard does not fight Git's bypass mechanism (tests assert it works). `commitguard doctor` makes missing, modified, disabled or redirected hooks visible. **Future mitigation:** GitHub-side CI as a required check with branch protection, reading policy from the base branch | limitation documented; mitigation **[planned, Phase 4]** |
| Malicious commit metadata or ref names attempt command injection (`$(…)`, backticks, `;`, `&&`, `\|`, quotes, Unicode) | Git metadata and pre-push input are untrusted data: no shell anywhere, argument vectors or stdin only, object IDs validated before use, ref names never interpolated; tests with hostile messages and branch names assert no execution | **[done]** |
| Existing hooks are destroyed by installation | Foreign hooks are renamed to `<hook>.pre-commitguard` and chained (never overwritten); conflicts refuse; uninstall removes only the managed block and restores the original; tests compare bytes | **[done]** |
| Repository content hijacks the hook (a `commitguard/` package in the work tree) | Hooks run `python -P -m commitguard`, so the current directory is not on `sys.path`; tested | **[done]** |
| CommitGuard unavailable (venv removed, not on PATH) silently allows operations | Wrapper falls back to `commitguard` on PATH, otherwise blocks with instructions (exit 2) | **[done]** |
| Invalid configuration or internal errors allow operations | Hook commands map every exception to exit 2 (blocks Git) with "security check could not be completed"; not configurable | **[done]** |
| Malformed pre-push input | Strict parsing (four fields, valid object IDs, no control characters, consistent deletions); anything else blocks | **[done]** |
| Hook script tampering | Managed block checksum; `doctor` reports modifications, `install` repairs on request | **[done]** (detection, not prevention) |
| Shared `core.hooksPath` modified for all repositories | Install refuses hooks directories outside the repository's Git directory unless explicitly allowed; `--global` uses `init.templateDir` only when unset, never `core.hooksPath` | **[done]** |
| Huge pushes exhaust resources or are silently truncated | Only outgoing commits analysed, deduplicated, bounded by `max_push_commits`; exceeding it blocks | **[done]** |
| Stale remote-tracking refs exclude commits the remote no longer has | Accepted: those commits were checked when pushed; exclusion never uses unknown data | limitation documented |
| commit-msg cannot see `--cleanup` or the final commit object | Documented best effort; pre-push analyses real commit objects | limitation documented |

### Bypassing local enforcement
- `--no-verify`, removing hooks, other clones. **Accepted locally**; mitigated by
  server-side enforcement **[planned, Phase 4]**. `pre-push` analyses commits
  created with `--no-verify` **[done]**.

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
- ReDoS via regex rule data: rules are literal values; the parser and matcher use no regular expressions **[done]**.
- Malicious rule files: strict schema, cross-validation, strict safe YAML **[done]**.

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
- **Evasion by formatting**: key casing/spacing/underscores, missing colon,
  zero-width and bidi characters, fullwidth/mathematical letters, Cyrillic/Greek
  look-alikes, Unicode line separators, indented trailers, trailers outside the
  trailer block: normalised and still detected **[done]**.
- **Trailer flood** (hide attribution after thousands of trailers): parsing is
  bounded and exceeding the limit fails closed (BLOCK) **[done]**.
- **False positives** (humans named like an agent, employees at vendor domains):
  exact matching only, vendor domains never sufficient alone, ambiguous names
  need corroboration **[done]**; single-token alias names remain a documented
  medium-confidence risk.
- **Resource exhaustion**: config/rule/message files size-limited, linear-time
  parsing, `--max-commits` refuses oversized ranges **[done]**; bounded Git
  output capture **[planned]**.
- **Record-splitting in batched Git output**: records separated by a random
  per-call boundary and cross-checked against requested SHAs **[done]**.

### Detector failure
- Any detector exception or invalid output → BLOCK (fail closed) **[done]**.
- Unexpected CLI errors exit 2 (never 1 = "blocked", never 0) without tracebacks **[done]**.

### Information disclosure
- No network access and no AI/LLM APIs in the local tool; no telemetry; enforced by architecture tests **[done]**.
- Reports and JSON contain concise metadata evidence only, never file contents or full messages **[done]**.
- Tracebacks never render local variables (`pretty_exceptions_show_locals=False`) **[done]**.
- Environment variables are never logged **[done — nothing logs them]**.
- Tokens for Phase 4 read at call time, never persisted **[planned]**.

### Repository modification
- Detectors receive data, not a repository handle; architecture tests forbid
  I/O imports in detection layers **[done]**.
- Git reads use `GIT_OPTIONAL_LOCKS=0` **[done]**.
- CommitGuard never rewrites commits or history; it never runs `git reset`, `rebase`,
  `commit --amend`, `filter-branch` or `filter-repo`. Remediation text explains
  the scope of any command it suggests **[by design]**.
- `init` never overwrites an existing file **[done]**.
