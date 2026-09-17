# Architecture for maintainers

A map of the Python packages, what each owns, and the dependency rules that
keep security decisions in one place. For the user-facing design see
[../architecture.md](../architecture.md); for detection and policy semantics
see [../detection-engine.md](../detection-engine.md) and
[../policy-engine.md](../policy-engine.md).

## The one rule that matters most

There is exactly **one detection engine and one policy evaluator**. Every
entry point (CLI, Git hooks, GitHub Action, GitHub App, benchmarks) feeds
commits to `services.analysis.Analyzer`, which runs the detectors
(`core.engine`) and the policy evaluator (`policies.evaluator`). Nothing else
may decide ALLOW, WARN or BLOCK. Governance decides *which* policy applies;
simulation re-evaluates recorded findings with the same evaluator.

```text
 entry points          cli/  hooks (git/hooks.py → cli hook)  ci github  github/ App worker  research/
                          \            |                          |            |              /
 services                  services.hooks · services.ci · services.scan · services.enforcement
                                                  │
                                       services.analysis.Analyzer
                                         │                    │
 pure core                     core.engine + detectors   policies.evaluator
                                         │                    │
                               provenance · rules.matcher   policies.model / governance (resolver)
                                         │
 I/O adapters                  git.commands / git.repository · rules.loader · config.loader
```

## Packages

| Package | Owns | Pure? |
|---|---|---|
| `core` | `CommitContext`, `Finding`, `Decision`, detection engine | yes |
| `detectors` | `coauthor`, `identity`, `trailer`, `bot`; registry | yes |
| `provenance` | identity and trailer parsing and normalisation; signature model (not wired in) | yes |
| `rules` | rule models and matcher; `rules.loader` reads the bundled YAML (exempt I/O module) | yes, except `rules.loader` |
| `policies` | policy model, defaults, evaluator, mandatory floors, governance resolver | yes |
| `config` | layered, strictly validated `.commitguard.yaml`; enforcement settings | no (reads files) |
| `security` | sanitisation, validation, hashing, strict safe YAML, secrets, rate limiting | helper layer |
| `git` | `run_git`, `Repository`, commit model, hook rendering and installation, push parsing | no |
| `utils` | `run_command` (the only subprocess entry point), filesystem, platform | no |
| `services` | analysis, hooks, CI planning and execution, scan and enforcement services, reports, audit service | no |
| `ci` | provider-neutral `CIContext` | yes (models) |
| `github` | Actions context and workflow inspection; App: webhooks, auth, REST client, Check Runs, events, installations, mirrors, SQLite storage, queue, worker, recovery, HTTP server | no |
| `api` | `/api/v1` WSGI app, HTTP helpers, settings, static hosting of `web/dist` | no |
| `controlplane` | dashboard services: identity (OAuth, sessions), access (roles, tenant scope), members, results, policies, queries, notifications | no |
| `governance` | organization settings, inventory, groups, bulk operations, rollouts, exceptions, simulation, schedules, posture, cache | no |
| `notifications` | outbox, dispatcher, deduplication, retries, preferences, templates; channels: in-app, e-mail (SMTP), webhook, sink | no |
| `audit` | audit event models and storage interface | no |
| `observability` | structured JSON logging with redaction, correlation IDs, in-memory metrics | no |
| `research` | datasets, benchmarks, immutable results | no |
| `cli` | Typer commands; the only layer that prints | no |
| `exceptions` | error hierarchy | yes |

## Dependency rules and the tests that enforce them

All in `tests/unit/test_architecture.py` unless noted.

| Rule | Test |
|---|---|
| `core`, `detectors`, `policies`, `provenance`, `rules` do not import the CLI, GitHub, audit, services, CI, config/rules loaders, Git command execution, hooks, diff, subprocess or filesystem helpers, `subprocess`, `socket`, `urllib` or `http` (`rules.loader` is exempt) | `test_pure_layers_do_not_import_io_or_interface_modules` |
| ...not even transitively, checked in a clean interpreter | `test_pure_layers_do_not_transitively_load_forbidden_commitguard_modules` |
| Only `cli` (and `__main__`) imports `cli` | `test_only_cli_imports_cli` |
| Every module imports on its own (no import-time cycles) | `test_every_module_imports_first_in_a_clean_interpreter` |
| No `shell=True` anywhere | `test_no_shell_true_anywhere` |
| No HTTP client or LLM libraries (`requests`, `httpx`, `urllib3`, `aiohttp`, `openai`, `anthropic`, `google.generativeai`) | `test_detection_code_uses_no_network_or_llm_libraries` |
| YAML only through `security.safe_yaml` | `test_yaml_is_only_loaded_through_the_strict_safe_loader` |
| `github` and `ci` (and `services/ci.py`) never import detectors, the engine or the evaluator | `test_ci_layers_do_not_reimplement_detection` |
| Network modules (`urllib.request`, `http.client`, `ssl`, `socket`, `smtplib`, `wsgiref.simple_server`, ...) only in `github.client`, `github.server`, `notifications.channels.email`, `notifications.channels.webhook` | `test_network_access_is_confined_to_the_github_client_and_server` |
| `cryptography` only in `github.auth` (and the CLI's availability probe) | `test_cryptography_is_only_used_for_app_authentication` |
| `services/scan.py`, `enforcement.py`, `audit.py`, `ci.py` do not import `github`, `cli` or detectors | `test_shared_services_are_platform_neutral` |
| `git fetch` only in `github.repositories` (mirror manager) | `test_git_fetch_happens_only_in_the_mirror_manager` |
| Importing the CLI loads neither the App service, the GitHub client, `cryptography`, `sqlite3`, the API nor the control plane (hooks stay fast and small) | `test_cli_import_does_not_load_the_app_service_or_cryptography`, `test_dashboard_api_is_not_loaded_by_the_cli` |
| `controlplane` and `api` never detect or evaluate policy | `test_control_plane_does_not_detect_or_evaluate_policy` |
| `governance` never detects; only `governance.simulation` imports the evaluator | `test_governance_never_detects_and_only_simulation_evaluates` |
| `governance` and `policies` never `eval`, `exec`, `compile`, `__import__` or `import_module` | `test_governance_policies_do_not_execute_configuration` |
| `api` routes contain no SQL | `test_api_routes_contain_no_sql` |

Other structural tests:

- `tests/unit/github/test_workflows_static.py`: actions pinned to SHAs, no
  `${{ }}` in enforcement `run:` scripts, least-privilege permissions.
- Hook reference copies in `hooks/` are generated and verified by tests
  (`tests/integration/hooks/test_hook_templates.py`, `tests/unit/git/test_hook_rendering.py`).

## Rules that are documented but not enforced by a test

- `research` "never imports the GitHub App, the API or the dashboard, and
  never contacts the network" (`src/commitguard/research/__init__.py`). The
  network part is covered by the network-module test; the import part is not.
- The dependency direction in the `src/commitguard/__init__.py` docstring
  ("flows downwards only") is incomplete: it does not list `api`,
  `controlplane`, `governance`, `notifications`, `research` or `services`.

## Actual package-level imports

Computed from the source on 2026-09-17 (first-party imports only). Use it to
spot new edges in review.

```text
api           -> audit controlplane core github governance notifications observability policies security utils
audit         -> core observability security
ci            -> security
cli           -> api audit config controlplane core exceptions git github observability policies provenance research rules security services utils
config        -> core exceptions git policies security utils
controlplane  -> audit config core exceptions git github notifications observability policies rules security services
core          -> detectors exceptions git security
detectors     -> core exceptions git provenance rules security
git           -> config exceptions provenance security utils
github        -> api audit ci config controlplane core exceptions git governance notifications observability policies rules security services utils
governance    -> audit ci config controlplane core exceptions github notifications observability policies rules security services
notifications -> audit controlplane core github observability security services utils
observability -> security
policies      -> config core security
research      -> core git policies provenance rules security services utils
rules         -> core exceptions provenance security utils
security      -> exceptions
services      -> audit ci config core detectors exceptions git observability policies provenance rules security utils
utils         -> exceptions
```

Notes:

- `core` and `detectors` import `git` only for the commit model
  (`git.commit`); Git execution modules are forbidden by the tests above.
- `cli` imports lightweight `github` modules (events, workflow inspection,
  permissions, webhook verification) at module level, but imports the App
  service, the storage layer, `api`, `controlplane` and `research` lazily
  inside the commands that need them, which is why the CLI import tests pass.
- `github`, `api`, `controlplane`, `governance` and `notifications` depend on
  each other at package level. The App service composes them; the
  import-first test keeps these edges free of import-time cycles. Adding a
  new edge between them deserves a comment in the pull request.

## State and trust boundaries

| Boundary | Where | Notes |
|---|---|---|
| Untrusted commit metadata | `provenance`, `git.repository` | bounded parsing, NUL-delimited Git output, sanitised on display |
| Untrusted configuration | `config`, `security.safe_yaml` | strict schema; policy for CI and the App read from a trusted commit |
| Untrusted webhooks | `github.webhooks` | size, signature (HMAC-SHA256), delivery ID replay protection, JSON limits |
| GitHub credentials | `github.auth`, `github.settings`, `security.secrets` | key parsed at start-up, fails closed; tokens in memory only |
| Browser requests | `api`, `controlplane.identity`, `controlplane.access` | sessions, CSRF, roles, tenant scoping by installation and repository |
| Outbound notifications | `notifications.channels.webhook`, `.email` | public-address checks, pinned connections, no redirects |
| Persistent state | `github.storage` (SQLite) | tenant-scoped queries, immutable history triggers, retention |

Full threat model: [../threat-model.md](../threat-model.md) and
[../security/](../security/threat-model.md).
