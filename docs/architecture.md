# Architecture

> Status: Phase 1. This document describes the intended architecture and marks
> which parts exist today.

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
                             │ ScanContext
                   ┌─────────▼─────────┐
                   │ DetectionEngine   │  runs DetectorRegistry in name order
                   │  (core/)          │
                   └─────────┬─────────┘
          ┌─────────────┬────┴────────┬──────────────┐
          ▼             ▼             ▼              ▼
     coauthor       identity       trailer          bot        (detectors/)
          └─────────────┴──────┬──────┴──────────────┘
                               │ ScanResult (findings + detector failures)
                   ┌───────────▼───────────┐
                   │ PolicyEvaluator       │  PolicySet from config + defaults
                   │  (policies/)          │
                   └───────────┬───────────┘
                               │ Decision (ALLOW / WARN / BLOCK + explanations)
                   ┌───────────▼───────────┐
                   │ CLI output / exit code│  sanitised explanation, 0 or 1
                   └───────────────────────┘
```

## Packages and dependency rules

| Package | Responsibility | May import | Status |
|---|---|---|---|
| `cli` | Typer app, commands, terminal output, exit codes | everything | skeleton; `init`, `policy list`, `doctor` work |
| `core` | `ScanContext`, `Finding`, `ScanResult`, `Decision`, `DetectionEngine` | `detectors.base/registry`, `git.commit`, `provenance`, `security` | implemented |
| `detectors` | pure detectors + explicit registry | `core.context/result`, `git.commit`, `provenance`, `security` | interface + registry implemented; detectors are stubs |
| `policies` | `Policy`, `PolicySet`, defaults, evaluator | `core`, `config.schema` | implemented |
| `provenance` | `Identity`, `Trailer`, `SignatureInfo`; parsing/analysis | `security` | models only; parsing is Phase 2 |
| `git` | Git CLI wrapper, repository queries, commit model, hooks, diff | `utils`, `security`, `provenance` | discovery + commit reading implemented |
| `config` | schema, loader, defaults | `core.decision`, `policies.defaults`, `utils`, `security` | implemented |
| `github` | Phase 4 enforcement | — | placeholder |
| `audit` | opt-in audit events and storage | `core` | placeholder |
| `security` | validation, sanitisation, hashing | `exceptions` | implemented |
| `exceptions`, `utils` | error types, subprocess/filesystem/platform helpers | stdlib | implemented |

Enforced by `tests/unit/test_architecture.py`:

- `core`, `detectors`, `policies` and `provenance` never import `cli`, `github`,
  `audit`, or the Git I/O modules (`git.commands`, `git.repository`, `git.hooks`,
  `git.diff`), nor `subprocess`.
- Nothing below `cli` imports `cli`.
- Every module imports cleanly in a fresh interpreter (no import cycles).

`git.commit` is pure data and is the one `git` module detectors may use.

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
