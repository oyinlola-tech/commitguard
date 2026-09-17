# Organization governance deployment

> Status: **Experimental.** Organization governance is implemented inside the
> GitHub App service and covered by integration tests against the same offline
> model of GitHub as the App (including a tenant isolation sweep over every
> governance route). It has not been exercised against github.com, and no
> external production deployment has been recorded.

```text
Organization (the GitHub account that installed the App)
      │
      │ members and roles (viewer · security_manager · admin · owner)
      ▼
Central policy                         organization_settings, organization_policy_versions,
  organization · groups · repositories scoped_policy_versions, policy_drafts, policy_approvals,
  security baseline · exceptions       policy_exceptions, policy_rollouts, organization_rule_versions
      │
      │ GovernanceResolver (maintenance loop, every 60 s, batches of 200)
      ▼
Effective policy per repository        repository_effective_policies
      │
      │ scan workers apply it; every scan records the versions it used
      ▼
Repositories ──▶ Check Run "commitguard-app"
```

Governance is **not a separate service**. It runs in the App process, stores
its state in the App's SQLite database, and is administered through the
dashboard and its API. Deploying it means deploying the App with the dashboard
enabled. The GitHub Action and the local hooks never read organization policy.

Concepts, permissions and invariants: [../organization-governance.md](../organization-governance.md).

## Prerequisites

1. A running App service: [self-hosted.md](self-hosted.md).
2. The dashboard enabled (`COMMITGUARD_DASHBOARD_URL`, client ID and secret,
   `COMMITGUARD_DASHBOARD_STATIC_DIR`): [self-hosted.md](self-hosted.md#dashboard).
3. The App installed on the organization, and the installation known to the
   service's database (it is recorded from the `installation` webhook, or when
   the service first handles an event for an installation created before it
   ran). Until then `members grant` fails with "no GitHub App installation is
   known for organization ...".
4. A first owner, granted on the host by numeric GitHub user ID:

   ```bash
   gh api users/<login> --jq .id
   commitguard dashboard members grant --organization <org-login> --user-id <id> --role owner --login <login>
   ```

   The command works directly on the database in `COMMITGUARD_APP_DATA_DIR` and
   records an audit event with the actor `commitguard-cli`.

## Rollout order that keeps enforcement predictable

1. **Observe.** Existing and new repositories default to `enforce` mode
   (`default_onboarding_mode`). To observe first, an owner or admin sets
   `default_onboarding_mode: monitor` in **Settings → Organization** before
   repositories are discovered. This is a weakening change: it needs
   confirmation, a reason and a recent sign-in, and it is audited.
2. **Group repositories** (`/organization/repositories`), then define group or
   organization policies as **drafts**.
3. **Simulate** a draft against recent history before publishing
   ([../policy-simulation.md](../policy-simulation.md)). Simulation never changes
   enforcement.
4. **Approve and publish**, optionally as a **staged rollout** with automatic
   pause thresholds ([../policy-rollouts.md](../policy-rollouts.md)).
5. **Watch propagation** on `/organization/policies` or
   `GET /api/v1/organizations/{id}/policy-propagation`. A change is complete only
   when every repository is `up_to_date`. A GitHub check reflects the new policy
   after the next scan (push, re-run, **Scan again**, or a scheduled scan).
6. **Require `commitguard-app`** in branch protection or rulesets. Governance
   decides what the check reports; only GitHub decides whether a failing check
   blocks a merge.

## Background work

All governance background work runs in the App's maintenance thread, once per
minute. One failing task is logged as `governance_task_failed` and the others
still run.

| Task | Work per pass |
|---|---|
| exception expiry and expiry warnings | up to 200 exceptions each |
| rollout safety evaluation | every rollout in `pilot` or `rollout` |
| effective policy propagation | up to 200 repositories; a repository is retried up to 5 times |
| policy simulations | one queued simulation, 10-minute lease |
| bulk operations | up to 200 items, 100 per transaction, 5-minute lease |
| scheduled scans | due schedules, up to 50 repositories, nothing while more than 200 scans are queued |
| security metric snapshots | one per organization per day |

Timing figures measured on a development machine are in
[../security-posture.md](../security-posture.md#performance); they are test
measurements, not a capacity guarantee.

## Security properties

| Property | Organization governance |
|---|---|
| Floors | organization and group requirements are applied after repository configuration; a repository cannot weaken them |
| History | policy and rule versions are immutable (database triggers); rollback publishes a new version; exceptions are never deleted |
| Separation of duties | with `require_separate_approver` (default on) authors cannot approve their own changes; requesters never approve their own exceptions |
| Tenant isolation | every query is scoped by the stored resource's organization; other tenants get 404 |
| Failure | a scan resolves policy directly when the cache is not current and fails closed if resolution fails |
| Custom configuration | data only; organization rules contain no regular expressions or code |

## Limitations

- Same single-host, single-database constraints as the App
  ([production.md](production.md#single-instance-constraint)).
- Audit retention defaults to 30 days (`COMMITGUARD_APP_RETENTION_DAYS`); raise
  it before you need the evidence.
- No invitations, SCIM or SSO beyond GitHub sign-in.
- No full inventory re-synchronisation on a timer; use **Sync** or webhooks.

The full list is in
[../organization-governance.md](../organization-governance.md#known-limitations).
