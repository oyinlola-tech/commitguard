# Deploying CommitGuard

This page moved into [deployment/](deployment/README.md). It is kept so
existing links keep working.

> CommitGuard is **not published to PyPI**; the PyPI name `commitguard` belongs
> to an unrelated project. Install from source, pinned to a commit
> ([deployment/README.md](deployment/README.md#installing-from-source)).

| Topic | Page |
|---|---|
| Deployment modes, security properties, status of each | [deployment/README.md](deployment/README.md) |
| Local Git hooks | [deployment/local.md](deployment/local.md) |
| GitHub Actions check | [deployment/github-actions.md](deployment/github-actions.md) |
| GitHub App service | [deployment/github-app.md](deployment/github-app.md) |
| Organization governance | [deployment/organization.md](deployment/organization.md) |
| Running the App service and dashboard yourself: architecture, requirements, configuration, built-in server, WSGI server, reverse proxy, dashboard, health checks | [deployment/self-hosted.md](deployment/self-hosted.md) |
| Production operation: operator responsibilities, single-instance constraint, backups, reliability, upgrades and rollback, security checklist, what has been validated | [deployment/production.md](deployment/production.md) |
| Error messages and fixes | [deployment/troubleshooting.md](deployment/troubleshooting.md) |
| Incident procedures | [operations/runbook.md](operations/runbook.md) |

## Architecture

See [deployment/self-hosted.md#architecture](deployment/self-hosted.md#architecture).

## Requirements

See [deployment/self-hosted.md#requirements](deployment/self-hosted.md#requirements).

## Dashboard

See [deployment/self-hosted.md#dashboard](deployment/self-hosted.md#dashboard).

## Health checks

See [deployment/self-hosted.md#health-checks](deployment/self-hosted.md#health-checks).

## Data, backups and retention

See [deployment/production.md#data-backups-and-retention](deployment/production.md#data-backups-and-retention).

## Reliability behaviour

See [deployment/production.md#reliability-behaviour](deployment/production.md#reliability-behaviour).

## Organization governance background work

See [deployment/organization.md#background-work](deployment/organization.md#background-work).

## Upgrades

See [deployment/production.md#upgrades-and-rollback](deployment/production.md#upgrades-and-rollback).

## Security checklist

See [deployment/production.md#security-checklist](deployment/production.md#security-checklist).
