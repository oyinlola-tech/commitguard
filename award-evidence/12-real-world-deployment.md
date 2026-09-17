# 12 - Real-world deployment

## Status: none

**No external production deployments have been recorded yet.** No organization,
team or open-source project other than this repository runs CommitGuard.

## What does run in production, honestly stated

The repository **enforces the policy on itself**: `.github/workflows/commitguard.yml`
runs CommitGuard's own check on every pull request, merge group and push, using
the scanner installed from a trusted commit. A commit carrying AI attribution
fails that check. That is dogfooding, not third-party adoption, and it is counted
as neither.

## What exists to support a first deployment

| | State |
|---|---|
| Deployment guides per mode, with security properties and limits | **IMPLEMENTED** - [docs/deployment/](../docs/deployment/) |
| An operations runbook (10 failure procedures) | **IMPLEMENTED** - [docs/operations/runbook.md](../docs/operations/runbook.md) |
| Incident response | **IMPLEMENTED** |
| A consent-based pilot process, report-only first, with metrics to record | **IMPLEMENTED** - [docs/community/pilot-program.md](../docs/community/pilot-program.md) |
| A case-study template requiring a limitations section | **IMPLEMENTED** |
| A deployment register that publishes only what an operator allows | **IMPLEMENTED, empty** |

## What a first pilot would measure

Commits scanned, violations found, false positives (judged by a human), scan
latency, remediation time, bypass attempts, policy changes, exceptions and
incidents - with the caveat, stated in advance, that one team is not a
representative sample.

## Readiness, stated conservatively

Local hooks and the GitHub Actions check are the parts ready to be used today: they
are simple, need no service, and are the layers with the most testing. The App
service and organization governance are **experimental** - complete and tested
against a fake GitHub, never run against github.com in production.

A team wanting to try this should start with the Actions check on one repository,
in report-only mode, for a fortnight.
