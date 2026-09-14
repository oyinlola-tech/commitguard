# Detection engine

> Status: the engine, detector interface and registry are implemented. The
> four built-in detectors are **stubs** that raise `NotImplementedError`
> (Phase 2 / Phase 5); the engine records this as a detector failure, which
> the policy evaluator turns into BLOCK.

## Concepts

### Commit (`commitguard.git.commit.Commit`)

Normalised, immutable, untrusted:

| Field | Status |
|---|---|
| `sha`, `parents` | implemented (validated full object IDs; `sha=None` = pending commit) |
| `author`, `committer` (`Identity`) | implemented |
| `authored_at`, `committed_at` | implemented (timezone-aware) |
| `message` | implemented (raw) |
| `trailers` | Phase 2 |
| diff metadata | Phase 3 |
| `signature` | Phase 5 |

### Finding (`commitguard.core.result.Finding`)

```text
Finding
├── detector      "coauthor"
├── rule          "ai_coauthor"
├── severity      high
├── message       "AI agent attribution detected in commit metadata."
├── evidence      (Evidence(source="trailer:co-authored-by",
│                           value="Claude <noreply@anthropic.com>", line_number=3),)
├── commit_sha    "3f2a…"  (None for a pending commit)
└── remediation   "Remove the AI coauthor attribution before pushing."
```

Evidence is mandatory (at least one item). `Finding.fingerprint` is a stable
SHA-256 over detector, rule, commit and evidence for de-duplication and audit.

### ScanResult

`findings`, `failures` (`DetectorFailure`), `detectors_run`, `commit_sha`.

## Engine behaviour

`DetectionEngine(registry).run(context)`:

1. Runs detectors sorted by name (deterministic).
2. Validates each result: must be a `Finding`, attributed to that detector,
   using a rule the detector declared. Violations become failures.
3. Catches any exception from a detector and records a `DetectorFailure` with a
   sanitised message. The scan continues with the remaining detectors.

The engine knows nothing about CLI commands, hooks, configuration or policy.

## Writing a detector

```python
class ExampleDetector(Detector):
    name = "example"
    rules = frozenset({"example_rule"})
    description = "One-line description."

    def detect(self, context: ScanContext) -> Sequence[Finding]: ...
```

Contract (see `detectors/base.py`): pure, deterministic, declared rules only,
evidence on every finding, no decisions, safe on hostile input. Every new rule
needs a default in `policies/defaults.py` (a test enforces this).

## Built-in detectors (planned)

| Detector | Rules | Phase | Looks at |
|---|---|---|---|
| `coauthor` | `ai_coauthor` | 2 | `Co-authored-by` trailers vs. AI identity/domain rules |
| `identity` | `ai_identity` | 2 | author and committer identities |
| `trailer` | `ai_trailer`, `malformed_trailer` | 2 | other attribution trailers/phrases; malformed trailer-like lines |
| `bot` | `bot_identity` | 5 | automation accounts |

## Rule data (`rules/`)

YAML data files (not code): `ai-identities.yaml`, `ai-domains.yaml`,
`bot-identities.yaml`, `patterns.yaml`. They are **not loaded yet**. Phase 2
will add a validated loader and ship them as package data.

Design constraints already recorded in the files:

- literal, case-insensitive matching; **no regular expressions from data**
  (ReDoS on attacker-controlled messages);
- vendor domains alone are not evidence (employees use them) — combine with
  automation-style local parts;
- identities are marked `verified` only with a reference commit.

## Evasion cases Phase 2 must handle

Tracked as `xfail` specifications in `tests/unit/detectors/` using fixtures in
`tests/fixtures/commits/`:

- key casing (`CO-AUTHORED-BY`), extra whitespace;
- malformed trailers (`Co-authored-by Claude noreply@anthropic.com`);
- terminal escape sequences hiding a line in `git log` output;
- multiple co-authors with one AI agent among humans;
- unknown agents identified only by heuristics (lower severity).
