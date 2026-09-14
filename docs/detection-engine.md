# Detection engine

> Status: **implemented (Phase 2)**. All four detectors, the rule files, the
> matcher, the trailer parser and the engine are in use by `commitguard scan`
> and `commitguard check`.

## Scope: attribution evidence, not authorship

The engine answers exactly one question:

> Does this commit's **metadata** contain evidence that an AI agent is
> identified as a contributor?

It inspects author, committer, trailers and exact tool-inserted attribution
lines. It never inspects diffs or file contents, never calls a network service
or AI model, and never claims that code *was written* by an AI. Wording such as
`feat: use AI service for recommendations` is not evidence and is never flagged.

## Pipeline

```text
Repository.read_commits()           git log, NUL-delimited, random record boundary
        │
        ▼
Commit (git/commit.py)              sha, parents, author, committer, dates,
        │                           message, trailers (derived), signature (Phase 5)
        ▼
CommitContext (core/context.py)     commit + trigger
        │
        ▼
DetectionEngine (core/engine.py)    runs detectors in name order
        │
        ├── BotDetector       ─┐
        ├── CoauthorDetector   │    each uses CompiledRules
        ├── IdentityDetector   │    (rules/*.yaml → IdentityMatcher)
        └── TrailerDetector   ─┘
        │
        ▼
DetectionResult                     findings + detector failures
```

## The commit model

| Field | Source |
|---|---|
| `sha`, `parents` | validated full object IDs; `sha=None` for a pending commit (`check --message-file`) |
| `author`, `committer` | `Identity(name, email)` exactly as recorded (mailmap and replace refs disabled) |
| `authored_at`, `committed_at` (`timestamp` property) | timezone-aware datetimes |
| `message` | raw message |
| `trailers` | **derived** from `message` by the trailer parser; cannot be supplied separately |
| `trailers_truncated` | `True` if the message exceeded the trailer limit |
| `signature` | reserved, always `None` until Phase 5 |

## Trailer parser (`provenance/trailers.py`)

Each `Trailer` keeps `key`, `value`, `raw` line, `line_number`,
`in_trailer_block`, structural `issues`, and a parsed `identity` (`name`,
`email`, identity `issues`). The parser never raises, and it records the
following instead of dropping them:

| Input | Result |
|---|---|
| `Co-authored-by: Claude <noreply@anthropic.com>` | well-formed |
| `Co-authored-by:` | `EMPTY_VALUE`; identity `EMPTY` |
| `Co-authored-by: Claude` | identity `MISSING_EMAIL` (name preserved) |
| `Co-authored-by: <invalid>` | identity `MISSING_NAME`, `INVALID_EMAIL` |
| `Co-authored-by: Claude <>` | identity `MISSING_EMAIL` |
| `CO-AUTHORED-BY Claude noreply@anthropic.com` | `MISSING_SEPARATOR`; identity `MISSING_BRACKETS` |
| `Co authored by: …`, `Co_authored_by: …`, `Co-authored-by : …`, zero-width chars in key | `NONSTANDARD_KEY`, normalised key `co-authored-by` |
| trailer outside the final paragraph | `in_trailer_block=False` (still parsed) |
| indented line that is itself a trailer | parsed as a new trailer, not folded into the previous value |
| ` `, `\r` and other Unicode line separators | treated as line breaks |
| `Claude <…>\x1b[1A\x1b[2K` (escape codes after the address) | identity `TRAILING_CONTENT` |

The subject line is only treated as a trailer for `*-by` keys (so
`feat: …` is not a trailer). No regular expressions are used; work is linear
in the message size and at most 1000 trailers are collected. Beyond that,
`trailers_truncated` is set and the trailer-consuming detectors raise, which
the policy evaluator turns into BLOCK (an attacker cannot hide attribution
behind a trailer flood).

## Rules are data (`rules/`)

| File | Contents |
|---|---|
| `ai-identities.yaml` | AI agents: `names`, `ambiguous_names`, `name_prefixes`, `emails`, `github_logins`, `verified`, `reference` |
| `ai-domains.yaml` | vendor domains → agent, `automation_local_parts`, `local_parts: automation\|any` |
| `bot-identities.yaml` | automation accounts (not AI) |
| `patterns.yaml` | co-author keys, attribution trailers, malformed-trailer checks, exact message markers |

Files are loaded with the strict safe YAML loader, validated with strict
schemas (unknown keys rejected) and cross-checked (unique IDs, domains and
markers must reference existing agents, an alias may belong to only one
agent, co-author keys cannot double as attribution trailer keys). Invalid rules
are an error (exit code 2). Built-in rules ship inside the wheel as
`commitguard/rules/data`.

Built-in agents: Claude / Claude Code, ChatGPT, OpenAI Codex, GitHub Copilot,
Cursor, Gemini, Jules, Windsurf, Codeium, Cline, Roo Code, Amazon Q Developer,
Devin, aider, OpenHands. Only Claude is marked `verified: true`; the others use
the best-known identities and need reference commits.

## Matching (`rules/matcher.py`)

Values are normalised for comparison only (evidence keeps the original):
Unicode NFKC, removal of control/format characters (zero-width, bidi),
case folding, whitespace collapsing, and a small explicit map of Cyrillic and
Greek letters that look identical to Latin ones. Nothing else is stripped.

Matching is **exact on normalised values** — never substring or fuzzy.

| Evidence | Alone | Confidence |
|---|---|---|
| exact configured email (`noreply@anthropic.com`) | match | high |
| GitHub login from `id+login@users.noreply.github.com`, or a `…[bot]` name | match | high |
| distinctive name prefix (`Claude Opus` ⇒ `Claude Opus 4.5 (1M context)`) | match | high |
| automation local part at a vendor domain (`noreply@`, `bot@`, …) | match | high |
| full-name alias (`Claude`, `ChatGPT`) | match | medium |
| full-name alias + vendor domain (`Claude <x@anthropic.com>`) | match | high |
| ambiguous alias + vendor domain (`Devin <d@cognition.ai>`) | match | medium |
| ambiguous alias alone (`Devin <d@example.com>`) | **no match** | — |
| vendor domain alone (`Jane Doe <jane@anthropic.com>`) | **no match** | — |
| `Claudette`, `Claude Dupont`, `Nimbus AI Agent` (unlisted) | **no match** | — |

If several agents match, the highest confidence wins, then the most reasons,
then the rule ID — deterministic.

## Detectors

All detectors are pure: they receive a `CommitContext` and compiled rules, and
return findings. They never touch Git, the filesystem, the network, or the
commit, and never decide allow/warn/block.

| Detector | Rules | Looks at |
|---|---|---|
| `coauthor` | `ai_coauthor` | each co-author trailer (`co-authored-by` and configured aliases), independently — human co-authors never produce findings; malformed or out-of-block trailers still count |
| `identity` | `ai_identity` | author and committer; one finding per agent, with evidence for each role |
| `trailer` | `ai_trailer`, `malformed_trailer` | configured attribution trailers (`key_present` or `ai_identity` match), configured malformed-trailer checks, exact whole-line tool footers |
| `bot` | `bot_identity` | author, committer and co-authors against `bot-identities.yaml` only (no generic `[bot]` heuristic) |

`Reviewed-by`/`Signed-off-by` are never flagged for existing; only when their
value matches an AI identity. `Generated-by: protoc` is fine;
`Generated-by: Claude Code` is not. `AI-assisted: no` is ignored.

## Findings (`core/result.py`)

```text
Finding
├── detector      coauthor
├── rule_id       ai_coauthor
├── severity      high
├── confidence    high
├── title         AI coauthor detected
├── message       A co-author trailer names an identity associated with the AI agent Claude.
├── evidence[]    source=coauthor_trailer, value="Claude <noreply@anthropic.com>", line_number=4,
│                 matched=[name "claude" (ai-identities.yaml#claude),
│                          email "noreply@anthropic.com" (ai-identities.yaml#claude), …],
│                 notes=[…malformed/out-of-block remarks…]
├── commit_sha    4f71c92…
└── remediation   Remove the AI co-author attribution from the commit message before pushing …
```

Evidence is concise metadata only, truncated to 512 characters, and sanitised
at display time. `fingerprint` is a stable SHA-256 over detector, rule, commit
and evidence.

## Engine behaviour (`core/engine.py`)

- Runs detectors sorted by name; skips detectors none of whose rules are
  enabled by policy (listed in `detectors_skipped`).
- Validates output: `Finding` instances, attributed to the detector, declared
  rule IDs, and the scanned commit's SHA. Violations become failures.
- Catches any detector exception as a `DetectorFailure` (sanitised message);
  failures evaluate to BLOCK.

## Writing a detector or rule

Prefer adding **rule data** over code. A new detector subclasses `Detector`,
declares `name`, `rules` and `description`, takes compiled rules in its
constructor, and is added to `builtin_registry`. Every new rule ID needs a
default policy in `policies/defaults.py` (enforced by tests).

## Known limitations

- A human co-author whose entire name is exactly an alias (e.g. just `Claude`)
  is flagged with medium confidence.
- Agents not in the rules, or that leave no attribution, are not detected.
- Homoglyph folding covers common Cyrillic/Greek look-alikes only.
- Most identities are unverified pending reference commits.
- `check --message-file` does not yet strip Git comment lines (Phase 3).
