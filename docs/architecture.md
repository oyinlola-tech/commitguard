# Architecture

> Status: Phase 2. Detection and policy are implemented; hooks (Phase 3) and
> GitHub enforcement (Phase 4) are not.

## Goals

1. **Provenance engine, not a keyword checker.** AI co-author detection is the
   first policy; bot identities, signatures, secrets and organisation policies
   must fit without redesign.
2. **Detection is separate from policy.** Detectors report facts with evidence;
   policies decide.
3. **Two enforcement layers.** Local hooks for fast feedback; repository-level
   enforcement for a real boundary.
4. **Security tool from day one.** Untrusted input everywhere, fail closed,
   explainable decisions, no network by default.

## Flow

```text
                 ┌───────────────────────┐
                 │  git commit / push    │   or  CI on a pull request (Phase 4)
                 └───────────┬───────────┘
                             │
                   ┌─────────▼─────────┐
                   │  CLI (cli/)       │  parses args, finds repo, loads config
                   └─────────┬─────────┘
                             │ Repository.read_commit() / pending commit
                   ┌─────────▼─────────┐
                   │  Commit (git/)    │  normalised, immutable, untrusted data
                   └─────────┬─────────┘
                             │ CommitContext
                   ┌─────────▼─────────┐
                   │ DetectionEngine   │  runs DetectorRegistry in name order
                   │  + rules/*.yaml   │  (via services.analysis.Analyzer)
                   │  (core/)          │
                   └─────────┬─────────┘
          ┌─────────────┬────┴────────┬──────────────┐
          ▼             ▼             ▼              ▼
     coauthor       identity       trailer          bot        (detectors/)
          └─────────────┴──────┬──────┴──────────────┘
                               │ DetectionResult (findings + detector failures)
                   ┌───────────▼───────────┐
                   │ PolicyEvaluator       │  PolicySet from config + defaults
                   │  (policies/)          │
                   └───────────┬───────────┘
                               │ Decision (ALLOW / WARN / BLOCK + explanations)
                   ┌───────────▼───────────┐
                   │ CLI output / exit code│  sanitised text or JSON; exit 0, 1 or 2
                   └───────────────────────┘
```

## Packages and dependency rules

| Package | Responsibility | Status |
|---|---|---|
| `cli` | Typer app, commands, text/JSON rendering, exit codes | `scan`, `check`, `init`, `policy list`, `doctor` work; `install`/`uninstall` are Phase 3 |
| `services` | the one analysis pipeline (config + rules + engine + evaluator + Git reading) shared by every entry point; report models | implemented |
| `core` | `CommitContext`, `Finding`, `Evidence`, `DetectionResult`, `Decision`, `DetectionEngine` | implemented |
| `detectors` | pure detectors + explicit registry | implemented (4 detectors) |
| `rules` | rule schemas, compiled matcher (pure); loader (reads packaged YAML) | implemented |
| `policies` | `Policy`, `PolicySet`, defaults, layered merge, evaluator | implemented |
| `provenance` | identities, trailer parsing, normalisation, signatures model | implemented (signature verification: Phase 5) |
| `git` | Git CLI wrapper, repository queries, commit model, hooks, diff | reading implemented; hooks/diff are Phase 3 |
| `config` | schema, layered loader, defaults | implemented |
| `github` | Phase 4 enforcement | placeholder |
| `audit` | opt-in audit events and storage | placeholder (reports are audit-ready) |
| `security` | validation, sanitisation, hashing, strict safe YAML | implemented |
| `exceptions`, `utils` | error types, subprocess/filesystem/platform helpers | implemented |

Dependency direction: `cli → services → {git, config, rules.loader} → {detectors, policies, core, rules, provenance} → {security, exceptions}`.

Enforced by `tests/unit/test_architecture.py`:

- `core`, `detectors`, `policies`, `provenance` and `rules` (except
  `rules.loader`) never import `cli`, `services`, `github`, `audit`, config/rule
  loaders, Git I/O modules, filesystem/subprocess helpers, `subprocess`,
  `socket`, `urllib` or `http` — directly or transitively.
- Nothing outside `cli` imports `cli`.
- No module imports HTTP clients or AI SDKs (`requests`, `httpx`, `openai`, `anthropic`, …).
- YAML is only parsed through `security.safe_yaml`.
- Every module imports cleanly in a fresh interpreter (no import cycles).
- No `shell=True` anywhere.

## Key design decisions

**Git CLI wrapper instead of GitPython.** One choke point (`git/commands.py`)
with argument vectors, `--end-of-options`, disabled replace objects, mailmap and
signature display, and sanitised errors. Fewer dependencies, full control of
what Git is asked to do.

**Explicit detector registry.** No entry-point or import-by-name plugins:
configuration can never cause code to be imported.

**Fail closed.** A detector exception, an undeclared rule, or a finding without
a policy all produce BLOCK.

**Immutable models.** All Pydantic models are frozen with `extra="forbid"`.

**Pending commits.** `Commit.sha` is optional so `commit-msg` hooks can evaluate
a commit before it exists.
