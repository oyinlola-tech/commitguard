# Architecture

> Status: Phase 5. Detection, policy, local Git hook enforcement, GitHub
> Actions enforcement and the webhook-driven GitHub App (Checks API) are
> implemented. No dashboard or management API yet.

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
                           Developer
                               │
                         git commit/push
                               │
                               ▼
                     Local CommitGuard hooks          commitguard hook <name>
                               │
                               ▼
                        Detection Engine ◀── bundled rules
                               │
                               ▼
                         Policy Engine ◀── .commitguard.yaml (work tree)
                     ┌─────────┴─────────┐
                  ALLOW                BLOCK ─▶ Git stops
                     │
                     ▼
                   GitHub
                     │  pull_request / merge_group / push
                     ▼
             GitHub Actions (action.yml)
                     │
                     ▼
               commitguard ci github   github/events.py ─▶ CIContext
                     │                 services/ci.py   ─▶ CommitRange + trusted PolicySource
                     ▼
              Detection Engine ◀── bundled rules (installed package)
                     │
                     ▼
                Policy Engine ◀── .commitguard.yaml at the TRUSTED commit
              ┌──────┴──────┐
             PASS          FAIL
              │             │
              ▼             ▼
          Check passes   Check fails ─▶ annotations, job summary, outputs
              │             │
              ▼             ▼
          PR mergeable   PR blocked   (only if branch protection requires "commitguard")
```

Both paths reach the same `services.analysis.Analyzer`; only the inputs differ
(which commits, which policy source).

### GitHub App (Phase 5)

```text
                         GitHub
                           │ webhook (HTTPS)
                           ▼
       github/app.py  WSGI POST /webhooks/github
         webhooks.py   size · signature · headers · JSON
         events.py     normalize_webhook ─▶ typed events (CIContext via parse_github_event)
         storage.py    delivery-ID dedup ─▶ ScanJob stored ─▶ queue.py
                           │
                           ▼  (background)
       github/worker.py  ScanWorker
         installations.py  installation state + token for ONE repository + ID lookup
         repositories.py   metadata-only mirror fetch (no checkout, no blobs)
                           │
                           ▼
       services/scan.py  ScanService ─▶ services/ci.py plan_ci + execute_ci_plan
                           │                (identical to `commitguard ci github`)
                           ▼
                Detection Engine ─▶ Policy Engine (+ optional mandatory policy floor)
                           │
                           ▼
       services/enforcement.py ─▶ check_runs.py ─▶ client.py ─▶ Check Run (exact SHA)
       services/audit.py ─▶ audit events · observability/ ─▶ JSON logs, metrics
```

The adapters differ only at the edges:

```text
                    CommitGuard core (detection, policy, findings, ScanReport)
                                        │
        ┌───────────────┬───────────────┼────────────────┬──────────────────┐
        │               │               │                │                  │
       CLI          Git hooks     GitHub Actions     GitHub App      future providers
   exit codes    allow/reject      exit code +      Check Runs      (GitLab, Bitbucket…)
                                   job summary                       via CIContext
```

## Packages and dependency rules

| Package | Responsibility | Status |
|---|---|---|
| `cli` | Typer app, commands (incl. `hook`), text/JSON rendering, exit codes | implemented |
| `services` | the one analysis pipeline shared by every entry point (`analysis`), hook runtime (`hooks`), CI range/policy planning (`ci`), `ScanService` (`scan`), `EnforcementService` (`enforcement`), `AuditService` (`audit`), report models | implemented |
| `core` | `CommitContext`, `Finding`, `Evidence`, `DetectionResult`, `Decision`, `DetectionEngine` | implemented |
| `detectors` | pure detectors + explicit registry | implemented (4 detectors) |
| `rules` | rule schemas, compiled matcher (pure); loader (reads packaged YAML) | implemented |
| `policies` | `Policy`, `PolicySet`, defaults, layered merge, evaluator | implemented |
| `provenance` | identities, trailer parsing, normalisation, signatures model | implemented (signature verification: planned) |
| `git` | Git CLI wrapper, repository queries, commit model, `CommitRange` (`ranges`), pre-push input parsing, hook install/uninstall/integrity | implemented (staged diff inspection: planned) |
| `config` | schema, layered loader, policy sources (`sources`: working tree / trusted revision / built-in), enforcement settings | implemented |
| `ci` | provider-neutral `CIContext` | implemented |
| `github` | Actions: event normalisation (`events`), output (`actions`), check model (`checks`), workflow template and inspection (`workflow`). App: `settings`, `auth`, `client`, `webhooks`, `installations`, `repositories` (mirrors), `storage`, `queue`, `worker`, `check_runs`, `app` (WSGI), `server` | implemented |
| `audit` | audit event model, storage interface, logger | implemented (recorded by the GitHub App) |
| `observability` | structured JSON logs, correlation IDs, redaction, metrics | implemented |
| `security` | validation, sanitisation, hashing, strict safe YAML | implemented |
| `exceptions`, `utils` | error types, subprocess/filesystem/platform helpers | implemented |

Dependency direction: `cli → services → {git, config, rules.loader} → {detectors, policies, core, rules, provenance} → {security, exceptions}`.

Enforced by `tests/unit/test_architecture.py`:

- `core`, `detectors`, `policies`, `provenance` and `rules` (except
  `rules.loader`) never import `cli`, `services`, `github`, `audit`, config/rule
  loaders, Git I/O modules, filesystem/subprocess helpers, `subprocess`,
  `socket`, `urllib` or `http` — directly or transitively.
- Nothing outside `cli` imports `cli`.
- `github`, `ci` and `services/ci.py` never import detectors, the detection engine or the
  policy evaluator directly: CI enforcement cannot grow its own detection logic.
- No module imports HTTP clients or AI SDKs (`requests`, `httpx`, `openai`, `anthropic`, …).
- Network modules (`urllib.request`, `http.client`, `ssl`, `socket`, servers)
  appear only in `github.client` (outbound) and `github.server` (inbound).
- `cryptography` is used only by `github.auth`; importing the CLI loads neither
  it nor the App service, SQLite or HTTP modules, so hooks and the Action stay
  offline and dependency-light.
- `services.scan`, `services.enforcement`, `services.audit` and `services.ci`
  import nothing from `github`, `cli` or `detectors`.
- `git fetch` appears only in `github.repositories`.
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

**Hooks are thin.** Installed hook files only locate CommitGuard and call
`commitguard hook <name>`; the analysis is the same `Analyzer` used by `scan`.
Existing hooks are chained, never overwritten. See [git-hooks.md](git-hooks.md).

**Fail closed at every enforcement point.** Hook commands map every exception
to exit 2, which stops Git; the wrapper blocks if CommitGuard cannot be found.

**One range abstraction.** `git.ranges.CommitRange` (commits reachable from a
head but not from excluded commits) serves pre-push, pull requests, pushes in
CI and `scan a..b`, so every enforcement point agrees on what "introduced" means.

**Policy source is explicit.** `config.sources.PolicySource` records whether
policy came from the work tree, a trusted commit or built-in defaults; every CI
report shows it. Future organisation or GitHub-hosted policy sources add layers
here without touching detection.

**GitHub is an adapter.** The App reuses `CIContext`, `CommitRange`,
`PolicySource`, `plan_ci` and the `Analyzer` through `ScanService`. It reads
real Git objects from a partial mirror instead of API commit lists, so it
analyses exactly what the Action and the hooks analyse.

**Dependency-free service.** The App uses the standard library for HTTP
(WSGI and `urllib`), storage (`sqlite3`) and queueing. Only JWT signing needs
`cryptography`, in the optional `app` extra. Storage and the queue are
interfaces, so PostgreSQL or a message broker can replace them later.

**Durable jobs, disposable queue.** Webhooks persist a scan job before
answering. The queue only wakes workers, so crashes and restarts lose nothing.

**Checks are per commit, writes are owned.** A newer job owns the Check Run for
a (repository, SHA, check name) slot, and every write verifies ownership under
a lock.

**CI context is provider-neutral.** GitHub-specific JSON stops at
`github.events`; `services.ci` works on `ci.context.CIContext`, so GitLab or
other providers only need an event adapter.
