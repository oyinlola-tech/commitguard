# Architecture

> Status: Phase 8. Detection, policy, local Git hook enforcement, GitHub
> Actions enforcement, the webhook-driven GitHub App (Checks API), the control
> plane (`/api/v1` and the web dashboard), the operational layer (notifications,
> merge queue, re-runs, policy recovery) and organization governance (central
> policy, groups, exceptions, rollouts, simulation, posture) are implemented.

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

### Control plane and dashboard (Phase 6)

```text
                                  Developer
                                      │ Git workflow
                                      ▼
                             Git hooks (Phase 3)
                                      │
                                      ▼
                                   GitHub
                      ┌───────────────┴────────────────┐
                      ▼                                ▼
            GitHub Actions (Phase 4)          GitHub App (Phase 5)
                      └───────────────┬────────────────┘
                                      ▼
                   CommitGuard core: detection · policy · Finding · ScanResult
                                      │
                                      ▼
                               ScanService
                                      │
                   ┌──────────────────┴──────────────────┐
                   ▼                                     ▼
   Persistence (controlplane/results.py)       Audit system (services/audit.py)
   scans · findings · violations · policy        actor · tenant · transactional
   versions · members · sessions
                   └──────────────────┬──────────────────┘
                                      ▼
            CommitGuard API  api/app.py   authentication → CSRF → rate limit
                                          → permission → AccessScope → service
                                      │
                                      ▼
            Security dashboard  web/  (React; renders server results only)
```

The dashboard is not the security engine:

```text
                    CommitGuard core
                          │
                          ▼
                     ScanResult
            ┌─────────────┼─────────────┐
            ▼             ▼             ▼
           CLI       GitHub Check    Dashboard
         terminal       GitHub        Web UI
```

`controlplane` stores what the worker produced (`ScanResultRecorder`),
versions organization policy (`OrganizationPolicyService`, applied through the
existing mandatory-policy floor), tracks whether each violation is still
present, and answers queries for one `AccessScope` at a time. `api` parses
requests, authenticates, checks CSRF, rate limits and permissions, and calls
those services. The browser receives only view models
(`controlplane/views.py`) and renders them. See [dashboard.md](dashboard.md).

### Operational layer (Phase 7)

```text
                              GitHub
             ┌──────────────────┼──────────────────┐
         PR / push       check_run / check_suite   merge_group
             └──────────────────┼──────────────────┘
                                ▼
          GitHub App: signature → event record (delivery ID, status) → normalise
                                │
                  ┌─────────────┴─────────────┐
                  ▼                           ▼
        scan execution (worker)       installation state transition
                  │                           │
                  ▼                           │
   CommitGuard core: detection · policy       │
                  │                           │
                  ▼                           ▼
   ScanResultRecorder / OrganizationPolicyService / InstallationService
        one transaction: state change + audit event + notification outbox
                  │
                  ├──► GitHub Check Run (exact repository, SHA, check, execution)
                  ▼
      notifications: dispatcher ──► in-app · e-mail (SMTP) · signed webhook
                                         └─ bounded retries, delivery audit
```

The core security principle is unchanged: detection → policy → security
decision → enforcement → notification → audit. Notifications, the dashboard
and GitHub event handlers never decide; they orchestrate and report.

| Component | Module | Role |
|---|---|---|
| Event processing | `github.app`, `github.events`, `github.storage` | normalised `MergeGroupEvent`, `CheckRunRerequestedEvent`, `CheckSuiteRerequestedEvent`; event records with processing status and safe redelivery |
| Scan executions | `github.storage` (`create_execution`), `github.worker` | numbered executions of one logical scan (`push`, `pull_request`, `merge_group`, `manual`, `rerun`, `retry`); stale protection through Check Run ownership |
| Merge queue | `github.app`, `github.worker`, `controlplane.results` | merge group records, scans of `base..merge group`, merge group exposures |
| Policy recovery | `controlplane.policies` | immutable versions, diff, rollback as a new version |
| Recovery | `github.recovery` | abandoned events, bounded automatic retry executions |
| Notifications | `notifications` (`models`, `outbox`, `deduplication`, `dispatcher`, `retry`, `preferences`, `templates`, `channels/{in_app,email,webhook,sink}`, `service`, `settings`) and `controlplane.notifications` (API-facing center) | transactional outbox, fan-out, delivery with retries |

See [notifications.md](notifications.md), [merge-queue.md](merge-queue.md),
[policy-management.md](policy-management.md) and [recovery.md](recovery.md).

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

### Organization governance (Phase 8)

```text
          Organization                      Settings: security baseline, approval, exception,
               │                            onboarding and rollout rules (versioned)
   ┌───────────┼───────────────┬──────────────────────┐
   ▼           ▼               ▼                      ▼
 Members   Repository groups   Organization rules     Policies (organization · group · repository)
 (roles)       │               (identity data only)    draft → simulate → approve → publish
               │                                        │           │
               ▼                                        │     staged rollout (pilot → stages)
         Repositories ◀── onboarding · mode ────────────┘           │
               │                                                    ▼
               │                                      Approved exceptions (scoped, expiring)
               ▼                                                    │
   GovernanceResolver ─── collects layers, exceptions, mode ◀───────┘
               │           (cached per repository, invalidated in the same transaction)
               ▼
   policies.governance.resolve_policy  (pure)  ─► effective PolicySet + provenance + conflicts
               │
               ▼
   worker ─► CommitGuard core: detection → policy evaluator ─► decision
               │
               ▼
   GitHub Check · violations · notifications · audit (versions and provenance recorded per scan)
               │
               ▼
   Security posture · matrix · trends · reports (read-only views over recorded data)
```

The principle is the same as in every phase: **governance decides which policy
applies; the policy engine decides what a finding means.** The organization
layer never detects anything and never contains a second evaluator. Policy
simulation re-evaluates recorded findings with the one `PolicyEvaluator`.

| Component | Module | Role |
|---|---|---|
| Resolution (pure) | `policies.governance` | precedence, mandatory/default strength, exceptions, monitor mode, provenance, conflicts, fingerprints |
| Resolver and cache | `governance.resolver`, `governance.cache` | collect inputs per repository, rollout-aware versions, `repository_effective_policies`, targeted invalidation, propagation |
| Settings | `governance.settings` | versioned organization settings, weakening needs confirmation, reason and recent sign-in |
| Inventory | `governance.inventory` | discovery, onboarding state, enforce/monitor mode |
| Groups | `governance.groups` | groups and memberships (archived, never deleted) |
| Policy workflow | `governance.workflow`, `controlplane.policies` | drafts, approvals, separation of duties, publish, emergency publish; versioned targets and rollback |
| Exceptions | `governance.exceptions` | scoped, expiring exceptions with approval, revocation, expiry and warnings |
| Rollouts | `governance.rollouts` | staged enrollment, pause/resume, automatic halt, rollback |
| Simulation | `governance.simulation` | queued, read-only impact estimates over recorded findings |
| Bulk operations | `governance.bulk` | background, bounded, idempotent repository operations |
| Scheduled scans | `governance.schedules` | default branch re-evaluation through the normal worker |
| Organization rules | `governance.rules` | versioned identity data compiled into the trusted rule set |
| Posture | `governance.posture` | explicit posture states, matrix, drift, trends, reports, search, acknowledgement |
| Wiring | `governance.service`, `api.governance` | service composition and maintenance tasks; `/api/v1` routes |

See [organization-governance.md](organization-governance.md) and the documents
it links.

## Packages and dependency rules

| Package | Responsibility | Status |
|---|---|---|
| `cli` | Typer app, commands (incl. `hook`), text/JSON rendering, exit codes | implemented |
| `services` | the one analysis pipeline shared by every entry point (`analysis`), hook runtime (`hooks`), CI range/policy planning (`ci`), `ScanService` (`scan`), `EnforcementService` (`enforcement`), `AuditService` (`audit`), report models | implemented |
| `core` | `CommitContext`, `Finding`, `Evidence`, `DetectionResult`, `Decision`, `DetectionEngine` | implemented |
| `detectors` | pure detectors + explicit registry | implemented (4 detectors) |
| `rules` | rule schemas, compiled matcher (pure); loader (reads packaged YAML) | implemented |
| `policies` | `Policy`, `PolicySet`, defaults, layered merge, evaluator, mandatory floors, governed resolution (`governance`) | implemented |
| `provenance` | identities, trailer parsing, normalisation, signatures model | implemented (signature verification: planned) |
| `git` | Git CLI wrapper, repository queries, commit model, `CommitRange` (`ranges`), pre-push input parsing, hook install/uninstall/integrity | implemented (staged diff inspection: planned) |
| `config` | schema, layered loader, policy sources (`sources`: working tree / trusted revision / built-in), enforcement settings | implemented |
| `ci` | provider-neutral `CIContext` | implemented |
| `github` | Actions: event normalisation (`events`), output (`actions`), check model (`checks`), workflow template and inspection (`workflow`). App: `settings`, `auth`, `client`, `webhooks`, `installations`, `repositories` (mirrors), `storage`, `queue`, `worker`, `check_runs`, `recovery`, `app` (WSGI), `server` | implemented |
| `audit` | audit event model, storage interface, logger | implemented (recorded by the GitHub App) |
| `observability` | structured JSON logs, correlation IDs, redaction, metrics | implemented |
| `controlplane` | roles and access scope (`access`), sign-in and sessions (`identity`), members, scan result recording and violation lifecycle (`results`), versioned organization policy (`policies`), read services (`queries`), write commands (`commands`), rule catalogue, pagination, API view models (`views`) | implemented |
| `governance` | organization governance: settings, inventory, groups, policy workflow, exceptions, rollouts, simulation, bulk operations, scheduled scans, organization rules, effective policy resolver and cache, posture and reports (Phase 8) | implemented |
| `notifications` | notification types and events, transactional outbox, deduplication, dispatcher, preferences, delivery worker with bounded retries, templates, channels (in-app, SMTP e-mail, signed webhooks, test sinks), settings | implemented |
| `api` | framework-free WSGI `/api/v1` (`app`), HTTP primitives, dashboard settings, hosting of the built dashboard and composition with the webhook app (`hosting`) | implemented |
| `web/` (repository root) | React + TypeScript dashboard: typed API client, pages, design system, unit and browser tests | implemented |
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
- Network modules (`urllib.request`, `http.client`, `ssl`, `socket`, `smtplib`,
  servers) appear only in `github.client` (outbound), `github.server`
  (inbound), and the two notification channels
  `notifications.channels.email` (SMTP) and `notifications.channels.webhook`
  (signed HTTPS POST).
- `cryptography` is used only by `github.auth`; importing the CLI loads neither
  it nor the App service, SQLite or HTTP modules, so hooks and the Action stay
  offline and dependency-light.
- `services.scan`, `services.enforcement`, `services.audit` and `services.ci`
  import nothing from `github`, `cli` or `detectors`.
- `git fetch` appears only in `github.repositories`.
- YAML is only parsed through `security.safe_yaml`.
- Every module imports cleanly in a fresh interpreter (no import cycles).
- No `shell=True` anywhere.
- `controlplane` and `api` never import detectors, the detection engine, the
  policy evaluator or the analyzer: the dashboard has no second engine.
- `api` contains no SQL; data access goes through `controlplane` services and
  the state store.
- Importing the CLI loads neither `api` nor `controlplane`.
- `governance` never imports detectors, the detection engine or the analyzer,
  and only `governance.simulation` imports the policy evaluator.
- `governance` and `policies` never call `eval`, `exec`, `compile`,
  `__import__` or `importlib.import_module`: policy documents, settings and
  organization rules are data.

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

**One database, versioned schema.** The control plane adds tables to the
App's SQLite store through ordered migrations. Findings, violations and the
job's final state are written in one transaction with their audit events, so
the dashboard never shows a result without its findings or a policy version
without its audit record.

**Tenant scope is a type.** Read services require an `AccessScope` built from
the session; SQL binds the scope as JSON (`json_each`) and joins the
session's GitHub-reported repositories. Queries are assembled only from
constant fragments.

**The server decides every status.** Scan results, violation status and
repository protection are computed in `controlplane` from stored facts. The
frontend maps each value to a label and never derives one from findings.

**CI context is provider-neutral.** GitHub-specific JSON stops at
`github.events`; `services.ci` works on `ci.context.CIContext`, so GitLab or
other providers only need an event adapter.
