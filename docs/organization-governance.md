# Organization governance

Phase 8 turns CommitGuard from per-repository enforcement into organization-wide
governance: a security administrator defines requirements once, and CommitGuard
applies them consistently to every repository of the organization, with
explicit exceptions, reviewed changes and a record of what applied to every scan.

```text
Organization (GitHub account)
    ├── Members and permissions
    ├── GitHub installations        synchronisation health
    ├── Repositories                discovery, onboarding, monitor / enforce mode
    ├── Repository groups           Production, Backend, Mobile, ...
    ├── Security settings           security baseline, approvals, exception limits
    ├── Policies                    organization · group · repository targets
    │     drafts → simulation → approval → publication → staged rollout → rollback
    ├── Exceptions                  scoped, expiring, approved, kept as history
    ├── Organization rules          additional AI agent and bot identities (data only)
    ├── Scan schedules              daily / weekly default-branch scans
    └── Posture, reports, audit     explicit states, point-in-time exports
```

Contents: [Principle](#the-principle) · [Model](#organization-model) ·
[Permissions](#members-roles-and-permissions) · [Tenant isolation](#tenant-isolation) ·
[Settings](#organization-settings) · [Where each part is documented](#documentation-map) ·
[Security invariants](#security-invariants) · [API and dashboard](#api-and-dashboard) ·
[Limitations](#known-limitations)

## The principle

> Organizations define the security baseline. Policies determine what is required.
> The policy resolver determines what applies. The policy engine determines the
> security decision. GitHub enforces the decision. The dashboard explains the state.
> Notifications communicate important changes. Audit records the history.

The organization layer never becomes a second security engine:

```text
       governance (this phase)                          CommitGuard core (unchanged)
┌───────────────────────────────────────┐   ┌──────────────────────────────────────────┐
│ settings · groups · policy versions   │   │                                          │
│ exceptions · rollouts · monitor mode  │──►│ resolve_policy ─► PolicySet               │
│        GovernanceResolver             │   │        │                                  │
└───────────────────────────────────────┘   │ detection engine ─► findings ─► policy    │
                                            │ evaluator ─► decision ─► GitHub check     │
                                            └──────────────────────────────────────────┘
```

* `commitguard.governance` decides **which configuration applies** to a
  repository. It never runs a detector and never evaluates a finding
  (architecture tests enforce it; the only use of the policy evaluator is
  [policy simulation](policy-simulation.md), which reuses it instead of
  duplicating it).
* `commitguard.policies.governance.resolve_policy` is the pure function that
  turns governance inputs plus the repository's own configuration into the
  effective `PolicySet` - with provenance for every rule.
* The scan worker records the governance inputs, their versions and the
  resolved provenance with every scan: history never changes afterwards.

## Organization model

The organization is the **GitHub account** (organization or user) that owns
the GitHub App installation, identified by GitHub's immutable account ID - the
same tenant as in [Phase 6](dashboard.md#organizations-roles-and-permissions).
Everything governance stores carries that `account_id`, and repository-level
rows use GitHub's immutable repository ID (so a reinstallation keeps groups,
policies and exceptions attached to the right repositories).

| Entity | Table | Notes |
|---|---|---|
| Organization | `installations` (account) | one active installation per GitHub account |
| Members | `memberships` | role per account; GitHub access checked at sign-in |
| Settings | `organization_settings` | versioned document, optimistic concurrency |
| Repositories | `known_repositories`, `repository_governance` | discovery, onboarding, mode |
| Repository groups | `repository_groups`, `repository_group_members` | many-to-many, archived not deleted |
| Policy versions | `organization_policy_versions`, `scoped_policy_versions` | immutable (triggers) |
| Drafts and approvals | `policy_drafts`, `policy_approvals` | approval bound to a document fingerprint |
| Simulations | `policy_simulations` | read-only results |
| Rollouts | `policy_rollouts`, `policy_rollout_repositories` | one in progress per target |
| Exceptions | `policy_exceptions` | never deleted (trigger) |
| Effective policies | `repository_effective_policies` | cache with propagation state |
| Organization rules | `organization_rule_versions` | immutable (triggers) |
| Bulk operations | `bulk_operations`, `bulk_operation_items` | background, idempotent |
| Scan schedules | `scan_schedules`, `scan_schedule_runs` | one run per slot |
| Posture snapshots | `security_metric_snapshots` | daily, for trends |
| Sync status | `installation_sync_status` | separate from "connected" |
| Acknowledgements | `notification_acknowledgements` | "seen", never "resolved" |

Foreign keys and unique indexes enforce: one membership per user and account;
a repository once per group, and a group's members belong to the group's
organization (composite foreign key); unique active group names; one version
number per target; at most one open exception per rule and scope; at most one
pending approval per draft; at most one rollout in progress per target; unique
bulk operation idempotency keys; one run per schedule slot.

**Deletion.** Organizations are GitHub accounts: uninstalling the App marks the
installation deleted and keeps its history for the retention period. There is
no endpoint that deletes an organization, a policy version, an exception or an
audit event. Groups are archived. Retention purges (scans, audit events,
finished background work) are the existing operator-configured maintenance,
documented in [deployment.md](deployment.md#data-backups-and-retention); audit
retention defaults to 30 days, which limits how far back compliance evidence
reaches ([compliance-reporting.md](compliance-reporting.md#retention-decides-how-far-back-evidence-goes)).

## Members, roles and permissions

Authorization is **by permission**. Routes and services check a permission;
roles are only bundles of permissions, defined in one table
(`commitguard.controlplane.access`):

| Permission | viewer | security manager | admin | owner |
|---|:-:|:-:|:-:|:-:|
| `organization:read`, `security:read`, `exceptions:read` | ✓ | ✓ | ✓ | ✓ |
| repositories, scans, violations, policies, rules, notifications: read | ✓ | ✓ | ✓ | ✓ |
| `exceptions:create`, `violations:manage`, `scans:trigger`, `audit:read` | | ✓ | ✓ | ✓ |
| `organization:manage`, `security:manage`, `rules:manage` | | | ✓ | ✓ |
| `repositories:manage` (groups, onboarding, mode, bulk) | | | ✓ | ✓ |
| `policies:write`, `policies:publish`, `policies:approve`, `policies:rollback` | | | ✓ | ✓ |
| `exceptions:approve`, `exceptions:revoke` | | | ✓ | ✓ |
| `members:manage`, `policies:emergency` | | | | ✓ |

There is no separate "member" role: `viewer` is the least-privileged member.
Separation of duties is enforced by the workflow, not by roles: with
`require_separate_approver` the author of a policy change cannot approve it,
and an exception is never approved by its requester.

Members are managed in **Settings → Organization → Members**
(`commitguard dashboard members` on the command line). Access is re-evaluated
on **every request** from the database: a removed member or a lowered role
takes effect on the member's next request, with no stale authorization state.
When a member's last membership is removed, their sessions are ended as well.
There are no invitations: a member signs in with GitHub and must also have
GitHub access to the installation (see
[dashboard.md](dashboard.md#sign-in-and-sessions)).

## Tenant isolation

Every governance query is scoped by the organization of the **stored resource**,
never by an ID from the URL or the body:

* organization routes check membership and the permission for that
  organization - a non-member receives **404**, exactly like a missing resource;
* nested resources (a group, draft, exception, rollout, simulation, bulk
  operation, schedule) are loaded first and authorized against *their*
  organization;
* repositories named in a request must belong to the organization **and** be
  visible to the caller's session on GitHub; otherwise 404;
* request bodies are validated only after authorization, so a caller without
  permission learns nothing from validation messages.

`tests/integration/github/app/dashboard/test_governance_isolation.py` creates
every kind of governance resource in one organization and calls **every
governance route** with those IDs as the owner of another organization (all
404, no data) and as a viewer (every write 403).

## Organization settings

**Settings → Organization** (`organization:manage`). Every change names the
version it was based on (409 on a concurrent change), is audited
(`organization_settings_changed` with the changed keys) and notifies
(`organization_settings_changed`, critical when a control is relaxed).

| Setting | Default | Meaning |
|---|---|---|
| `security_baseline` | none | mandatory rule requirements for every repository: a policy layer applied with the organization policy, labelled "security baseline" in provenance |
| `require_policy_approval` | off | policy changes must be drafted and approved before publication; direct saves are refused with `APPROVAL_REQUIRED` |
| `require_separate_approver` | on | the author (or submitter) of a draft cannot approve it |
| `exception_approval_min_severity` | `high` | exceptions for rules at or above it need approval |
| `exception_max_days` | 90 | longest exception |
| `allow_permanent_exceptions` | off | see [policy-exceptions.md](policy-exceptions.md) |
| `exception_warning_days` | 7, 3, 1 | expiry warnings |
| `default_onboarding_mode` | `enforce` | mode of newly discovered repositories |
| `auto_onboard_new_repositories` | on | new repositories are onboarded (else they wait as discovered) |
| `archived_repositories` | `keep` | `keep` visible (no scheduled scans) or `exclude` from onboarding |
| `rollout_auto_pause`, `rollout_max_error_rate`, `rollout_max_block_rate`, `rollout_min_scans`, `rollout_auto_rollback` | on, 0.2, 0.5, 5, off | [policy-rollouts.md](policy-rollouts.md) |
| `aggregate_violation_alerts` | off | e-mail and webhooks receive one digest per rule and hour instead of one alert per pull request |
| `timezone` | `UTC` | scan schedules and report dates |

Relaxing a control - lowering or removing a baseline requirement, turning
approval or separate approvers off, raising the exception limits, allowing
permanent exceptions, onboarding in monitor mode, loosening rollout safety -
requires `confirm`, a reason and a sign-in from the last 15 minutes.

Why `enforce` is the default onboarding mode: installing the GitHub App has
meant "the check fails on violations" since Phase 5, and changing that silently
would weaken existing deployments. Organizations that want to observe first set
`monitor` (a weakening change, confirmed and audited).

## Documentation map

| Topic | Document |
|---|---|
| Precedence, strengths, conflicts, provenance, drift, propagation | [policy-inheritance.md](policy-inheritance.md) |
| Drafts, approvals, emergency publication, versions, rollback | [policy-management.md](policy-management.md) |
| Simulation | [policy-simulation.md](policy-simulation.md) |
| Exceptions | [policy-exceptions.md](policy-exceptions.md) |
| Staged rollouts | [policy-rollouts.md](policy-rollouts.md) |
| Discovery, onboarding, groups, bulk operations, schedules, synchronisation | [repository-management.md](repository-management.md) |
| Posture, matrix, trends, search, acknowledgement, performance | [security-posture.md](security-posture.md) |
| Reports and exports | [compliance-reporting.md](compliance-reporting.md) |
| Organization rules | [repository-management.md](repository-management.md#organization-rules) |
| Notifications added in Phase 8 | [notifications.md](notifications.md#types) |
| Threats | [threat-model.md](threat-model.md#organization-governance-phase-8) |

## Security invariants

Each invariant is enforced in code and covered by tests:

| # | Invariant | Enforced by |
|---|---|---|
| 1 | Mandatory organization policies cannot be weakened by repositories. | floors applied after every configuration layer (`resolve_policy`); a weaker request becomes a recorded conflict |
| 2 | Historical scans never change because policies change. | scans store versions, provenance and effective policies; policy writes never touch `scan_jobs` |
| 3 | Policy versions are immutable. | database triggers on every version table; rollback publishes a new version |
| 4 | Exceptions cannot silently become permanent. | `expires_at` required unless `permanent` (setting + `exceptions:approve` + approval by someone else); a check constraint; the expiry worker |
| 5 | Unauthorized users cannot approve security changes. | permission checks per resource; separation of duties; approval bound to the document fingerprint |
| 6 | Cross-tenant data is never returned. | resource-scoped authorization; 404 for other tenants; sweep test over every route |
| 7 | Simulation never changes enforcement. | read-only service; tests compare versions, checks and violations before and after |
| 8 | Failed propagation never becomes a false PASS. | a scan resolves directly when the cache is not current; resolution failure fails the scan closed; propagation errors are reported, not hidden |
| 9 | GitHub API failure never becomes false security success. | scheduled scans and synchronisation record `failed` (a synchronisation that never finishes is `failed` after 15 minutes); installations show `degraded`/`failed`; posture is `at_risk` |
| 10 | Custom configuration cannot execute arbitrary code. | policy documents, settings and organization rules are validated data; no regular expressions in organization rules; architecture test forbids `eval`/`exec`/dynamic imports in governance and policies |

## API and dashboard

All routes are listed with their permissions in
[dashboard.md](dashboard.md#api-reference) ("Organization governance routes").

| Page | Path |
|---|---|
| Organization command center | `/organization` |
| Repository security matrix, groups, bulk actions, onboarding | `/organization/repositories` |
| Repository group | `/organization/groups/:id` |
| Policies, drafts, rollouts, propagation | `/organization/policies` |
| Exceptions | `/organization/exceptions` |
| Trends and reports | `/organization/security` |
| Organization audit | `/organization/audit` |
| Organization settings, rules and schedules | `/settings/organization` |
| Members | `/settings/organization/members` |
| Effective policy of a repository | repository page → Effective policy |

## Known limitations

* **Tested against an offline model of GitHub**, not github.com, like
  Phases 5-7.
* **SQLite, one host.** Governance uses the same single database as the
  service. Measured volumes are in
  [security-posture.md](security-posture.md#performance); a 10,000-repository
  organization works, but the repository matrix takes about a second to
  compute and is not cached.
* **No invitations or SCIM.** Members sign in with GitHub and are granted roles.
* **No finding-level exceptions.** Exceptions target a rule within an
  organization, group or repository; to record that a single finding was
  reviewed, acknowledge the violation (which never changes enforcement).
* **No `optional` policy strength.** A rule no layer mentions is decided by the
  repository - the practical meaning of "optional".
* **No escalation chains.** Critical events can be acknowledged; escalation to
  other people after a delay is not implemented.
* **PDF reports are not produced**: JSON and CSV only, at most 10,000 rows per
  report (larger results say `truncated`).
* **Audit retention is short by default** (30 days). Organizations that need
  a longer trail must configure it before they need the evidence.
* **Counts depend on the viewer.** Repository-level numbers include only the
  repositories the viewer can see on GitHub; two members can see different
  totals.
* **No full re-synchronisation on a timer.** The inventory follows GitHub
  webhooks and on-demand synchronisation; an installation not synchronised for
  7 days is shown as `degraded`.
* **Scheduled scans** re-evaluate default branches only; the first scheduled
  scan of a branch uses built-in defaults for the repository configuration
  (organization governance still applies).
