# CommitGuard documentation

## Start here

| | |
|---|---|
| [5-minute quick start](getting-started/quickstart.md) | Install, enforce, see a commit blocked, understand the limits |
| [Examples](../examples/) | Worked examples, each verified by a test |
| [CLI reference](cli/) | Every command, option and exit code |
| [Troubleshooting](deployment/troubleshooting.md) | Real error messages and what they mean |

## How it works

| | |
|---|---|
| [Architecture](architecture.md) | Packages, layers and data flow |
| [Detection engine](detection-engine.md) | What is detected, and what is deliberately not |
| [Policy engine](policy-engine.md) | How decisions combine |
| [Configuration](configuration.md) | The file format and the layers |
| [Git hooks](git-hooks.md) · [GitHub enforcement](github-enforcement.md) · [GitHub App](github-app.md) | The three enforcement layers |
| [Dashboard and API](dashboard.md) · [API reference](api/) | The service interfaces |

## Running it

| | |
|---|---|
| [Deployment](deployment/) | Local, Actions, App, organization, self-hosted, production |
| [Operations runbook](operations/runbook.md) | Ten failure procedures |
| [Compatibility](support/compatibility.md) · [Compatibility policy](support/compatibility-policy.md) · [Maintenance policy](support/maintenance-policy.md) | What is supported, and for how long |
| [Recovery](recovery.md) | Failure handling and policy recovery |

## Organization governance

[Organization governance](organization-governance.md) ·
[Policy inheritance](policy-inheritance.md) ·
[Simulation](policy-simulation.md) ·
[Exceptions](policy-exceptions.md) ·
[Staged rollouts](policy-rollouts.md) ·
[Repository management](repository-management.md) ·
[Security posture](security-posture.md) ·
[Compliance reporting](compliance-reporting.md) ·
[Notifications](notifications.md) ·
[Merge queue](merge-queue.md) ·
[Policy management and rollback](policy-management.md)

## Security

| | |
|---|---|
| [Security documentation index](security/) | Threat model, boundaries, authorization matrix, review guide |
| [Vulnerability response](security/vulnerability-response.md) | Report to fix to advisory, and the fixes so far |
| [Incident response](security/incident-response.md) | When something has already gone wrong |
| [Supply chain](security/supply-chain.md) | Dependencies, CI integrity, and the gaps |
| [CI pipeline security](security/ci-pipeline-security.md) | Fork pull request safety |

## Research and evidence

| | |
|---|---|
| [Research index](research/) | Question, methodology, evaluations, limitations |
| [Detection evaluation](research/detection-evaluation.md) | Accuracy, and the bypasses the benchmarks found |
| [Performance evaluation](research/performance-evaluation.md) | Latency, scaling, hook overhead |
| [Bypass resistance](research/bypass-resistance.md) | What happens when someone tries |
| [Reproducibility](research/reproducibility.md) | Re-run everything yourself |
| [Limitations](research/limitations.md) | What this does not do |
| [Decision records](adr/) | Decisions and their costs |
| [Timeline](evidence/timeline.md) · [Validation matrix](evidence/validation-matrix.md) | What is validated, by whom |
| [Phase 10 report](evidence/phase-10-report.md) | The most recent phase, end to end |

## Project

[Contributing](../CONTRIBUTING.md) ·
[Maintainers](maintainers/) ·
[Community](community/) ·
[Roadmap](../ROADMAP.md) ·
[Security policy](../SECURITY.md) ·
[Presentations and article](presentations/)
