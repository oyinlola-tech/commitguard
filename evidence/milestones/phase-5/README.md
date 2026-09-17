# Phase 5: GitHub App service

**Completed:** 2026-09-15

## Objective

One service for many repositories, with central policy.

## Implementation

Webhook endpoint with HMAC verification and delivery de-duplication, scan service, Checks API integration, SQLite storage, installation lifecycle handling, mandatory policy support, and commitguard github validate.

## Evidence

docs/github-app.md

## Tests

Integration tests against a fake GitHub implementing the API surface used.

## Results

Check runs published on the exact commit scanned; least-privilege permissions; tokens down-scoped per repository and never stored.

## Limitations

Never run against github.com in production; single instance per database.
