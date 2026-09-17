# Policy management: versions, approval, rollback and historical results

```text
service policy            COMMITGUARD_APP_MANDATORY_POLICY_FILE (operator), optional
  + security baseline       organization settings
  + organization policy     dashboard, immutable versions
  + group policies          dashboard, immutable versions per repository group
  + repository policy       dashboard, immutable versions per repository
  + repository config       .commitguard.yaml at the trusted revision
  + approved exceptions     scoped, expiring
  = effective policy        recorded with every scan
```

Each policy version (organization, group or repository **target**) holds
**mandatory** entries (floors: `warn` or `block` that nothing narrower can
weaken) and **default** entries (baselines that narrower layers and
`.commitguard.yaml` may replace). How the layers combine, per rule, is defined in
[policy-inheritance.md](policy-inheritance.md). This document covers the
lifecycle of versions: drafts, approval, publication, rollback and history.
See also [dashboard.md](dashboard.md#policies) and
[configuration.md](configuration.md).

## Draft, simulate, review, approve, publish

```text
draft ──submit──► pending_approval ──approve──► approved ──publish──► version vN (immutable)
  │                    └──reject──► rejected       └──edit──► draft
  └──cancel──► cancelled
```

* A **draft** proposes a complete document for one target. Nothing in it is
  enforced until it is published.
* **Simulate** a draft against recorded scans before publishing
  ([policy-simulation.md](policy-simulation.md)).
* **Approval** is an organization setting (`require_policy_approval`). When it
  is on, only approved drafts can be published and the direct save
  (`PUT /api/v1/policies/{organization_id}`) is refused with `APPROVAL_REQUIRED`.
* **Separation of duties** (`require_separate_approver`, on by default): the
  creator or submitter of a draft cannot approve it. Approval binds to the
  draft's document fingerprint; editing an approved draft sends it back to
  `draft`.
* **Publish** (`policies:publish`) writes an immutable version against the
  draft's base version: if someone published in between, publication conflicts
  (`409`) instead of overwriting. The draft shows `rebase_required`; editing it
  with `rebase: true` bases it on the current version (and, like any edit,
  discards an approval), so the change is reviewed against what is actually
  published. A published draft keeps the diff of what it changed. Weakening a floor needs `confirm_weakening`, a
  reason and a recent sign-in, as in Phase 7. A publication may start a
  [staged rollout](policy-rollouts.md).
* **Emergency publish** (`policies:emergency`, owners) skips approval for
  urgent fixes. It requires a reason, marks the version `emergency`, and
  produces a critical audit event and a mandatory notification.

Every transition is audited (`policy_draft_created`, `policy_approval_requested`,
`policy_approved`, `policy_rejected`, `policy_published`,
`policy_emergency_published`, ...).

## Versions

- Every publication creates a new, numbered version (`v1`, `v2`, ...) per
  target. Version rows are **immutable**: database triggers abort any `UPDATE`
  or `DELETE` on `organization_policy_versions` and `scoped_policy_versions`
  (group and repository targets), and migrations never delete them.
- Each version stores the canonical JSON document of its entries, its SHA-256
  fingerprint, author, time, reason, its **kind** (`change` or `rollback`), the
  draft it was published from and whether it was an emergency publication.
- **Status** is derived, never stored: the newest version is `active`, every
  other version is `archived` (during a staged rollout, the repositories not
  yet enrolled keep the previous version). Drafts are separate records; a
  version exists only once published.
- An archived version becomes the basis of enforcement again **only** through
  an explicit rollback.

## Effective version

Scans resolve the active version from the database when they start and record:

| Stored with every scan | |
|---|---|
| `organization_policy_version` | the organization version applied |
| `governance` | versions of every layer (organization, groups, repository policy, settings, rollouts, exceptions, organization rules) and per-rule provenance |
| `policy_version` | fingerprint of the complete effective policy set |
| `effective_policies` | every rule's enabled flag and action |
| `policy_source` | e.g. `pull request base (3a91f02e7d5c) + organization policy v12` |
| `rules_version`, `tool_version` | rule data and CommitGuard version |

There is no process-local policy cache: every worker, API process and host
reads the same database. The resolved effective policy per repository is cached
*in the database* and invalidated in the same transaction as any change that
affects it, so a change or rollback applies to the next scan everywhere
([policy-inheritance.md](policy-inheritance.md#effective-policy-cache-and-propagation)). A scan that started before a change keeps the version it resolved:

```text
scan starts (resolves v13) ─► rollback publishes v14 ─► scan completes ─► recorded as v13
```

**Historical results are never rewritten** by a policy change, a rollback, a
rule update or an installation change. Reproducing a scan needs its repository,
commit range, organization policy version, rules version and CommitGuard
version, while the Git data is still available.

## Rollback

```http
POST /api/v1/policies/{organization_id}/rollback
{ "target_version": 12, "expected_current_version": 13,
  "reason": "v13 incorrectly blocks approved bot identities", "confirm": true }
```

The result is a **new version** whose document is the target's:

```text
v12  archived   Stable production policy
v13  archived   Added bot restriction
v14  active     kind=rollback  restored_version=12  rollback_of=13
```

Nothing is deleted; `v13` stays in the history, and `v14` records both sides
of the lineage.

| Requirement | Enforced by the server |
|---|---|
| `policies:rollback` in that organization | 403; another tenant's organization is 404 |
| a non-empty `reason` (at most 500 characters) | 400 |
| `confirm: true` | 409 `CONFIRMATION_REQUIRED` |
| `expected_current_version` equals the active version (also re-checked inside the transaction) | 409 `CONFLICT`: no lost update |
| `1 <= target_version < active version`, and the target exists | 400 (`field: target_version`) |
| the target differs from the active version | 400 |
| the target's fingerprint matches its document, and the document is a valid canonical policy | 422 `POLICY_VERSION_INVALID` |
| a sign-in within 15 minutes when the rollback weakens a floor | 401 `REAUTHENTICATION_REQUIRED` |
| POST with an allowed `Origin` and CSRF token; 10 sensitive changes per minute | 403 / 429 |

In one database transaction CommitGuard inserts the version, the
`organization_policy_rolled_back` audit event (actor, organization, previous,
target and new version, changes, weakening flag, reason, request ID) and the
`policy_rolled_back` notification. If anything fails - validation, a
concurrent change, a database error - the transaction rolls back and the
active version is unchanged. `policy_rollbacks` and
`policy_rollback_failures` are counted.

**Dashboard.** Policies → an organization → **Version history and rollback**:
each version shows `ACTIVE`/`ARCHIVED`, the rollback lineage, a change summary
against the previous version and the reason. **Compare with vN** shows a
structured diff (added, changed, removed, weakening). **Roll back to vN** is
offered only with `policies:rollback`; the dialog shows the current and target
versions, the impact diff, a required reason and an acknowledgement, and is
fully keyboard accessible. `GET /api/v1/policies/{id}/diff?from=&to=` returns
the same diff.

## What rollback does not do

- It does not resolve or reopen violations. Existing findings stay history;
  the next scans decide whether a condition is still a violation under the
  restored policy.
- It does not rewrite existing GitHub checks. New scans (new pushes, pull
  request updates, a GitHub "Re-run", **Scan again**) use the new version.
- It never touches Git: no history rewrite, amend, force push or change to a
  pull request branch.

## Concurrency

```text
Admin A loads v10          Admin B loads v10
Admin B publishes v11  ─►  ok
Admin A rolls back to v9 (expected_current_version 10)  ─►  409 CONFLICT, nothing written
```

The same check protects saves against a concurrent rollback.

## Re-runs and policy changes

A GitHub "Re-run" or **Scan again** evaluates the stored commits with the
**current** effective policy and the same trust model as the original scan
(policy from the trusted base, never from the change). Both executions are
kept, and the execution history marks when they used different policy or rules
versions:

```text
Execution #1  pull_request  BLOCKED  organization policy v10
Execution #2  rerun         PASS     organization policy v11
```

A dashboard user cannot choose a historical policy version for a scan, so no
one can manufacture a "current" result under an old policy.
