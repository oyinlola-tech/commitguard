# GitHub enforcement validation

Seven server-side experiments, **recorded 2026-09-17**, in
`tests/integration/github/app/security/test_server_enforcement_experiments.py`.
They run against a fake GitHub that implements the API surface CommitGuard uses,
so they exercise the real service code end to end.

Outcomes: six `prevented`, one `detected`.

## Webhook forgery

| Attempt | Observed |
|---|---|
| Signed with the wrong secret | **HTTP 401** `invalid webhook signature` |
| Correctly signed, body altered afterwards | **HTTP 401** `invalid webhook signature` |
| No signature at all | **HTTP 401** `missing webhook signature` |
| Correctly signed control | accepted, `{"status": "queued"}` |

Scans processed from the three forgeries: **0**. Check runs created: **0**.

The control matters: without it, the experiment would pass even if the endpoint
rejected *everything*. An earlier version of this test did exactly that - it used
delivery identifiers that failed header validation before signature checking was
reached, so it was passing for the wrong reason. The test was fixed, not the
assertion.

## Replay and out-of-order delivery

| Attempt | Observed |
|---|---|
| The same delivery sent twice | second response `{"status": "duplicate"}` |
| An old event re-sent under a new delivery ID, after a newer commit was scanned | `{"status": "duplicate"}`; the newest commit's check is still **failure** |

Delivery records carry a payload digest, and check ownership is keyed on
repository, commit SHA and check name, so a replayed old event cannot overwrite a
newer result.

## Permissions revoked

The installation's Checks permission is reduced to read while a scan is in
flight: the job ends in **error (authorization)** and **0** check runs are
created. Never a success.

This experiment also passed for the wrong reason at first: a cached installation
token meant the scan kept working. Delivering the `new_permissions_accepted`
event to invalidate the cache made it a real test.

## Installation suspended

Outcome `detected`: scans processed **0**, check runs **[]**, and the service
records `installation_suspended` in the audit log and raises an
`installation_disconnected` notification. A suspended installation is a visible
outage, not a quiet gap.

## Mandatory policy floor

With the repository's merged configuration disabling the rule: **success**
without a mandatory policy, **failure** with one. See
[policy-tampering.md](policy-tampering.md).

## What is modelled rather than real

These experiments run against a fake GitHub, and branch protection is **modelled**:
the tests assert that a required check which fails or never reports does not
permit a merge, using a model of GitHub's gate. They do not prove GitHub's own
merge behaviour, which only a live installation can.

That is the largest gap in this evidence set, and it is listed in
[limitations.md](limitations.md) rather than glossed over.
