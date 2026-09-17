# Security posture

The organization security page answers "are our repositories protected, and if
not, why?" It never answers with a mystery score. Posture is one of four
explicit states, and every state lists the reasons that produced it.

| State | Meaning |
|---|---|
| `secure` | protected by GitHub, enforcing, nothing open that undermines it |
| `at_risk` | protected in principle, but something weakens it right now |
| `unprotected` | CommitGuard cannot stop a merge |
| `unknown` | CommitGuard cannot verify protection yet, and says so |

## Repository posture

The **first** matching rule decides:

| # | Condition | Posture |
|---|---|---|
| 1 | the GitHub App installation is suspended or lost access to the repository | `at_risk` |
| 2 | CommitGuard monitoring is paused | `unprotected` |
| 3 | GitHub does not require a CommitGuard check (branch protection / rulesets) | `unprotected` |
| 4 | the effective policy could not be resolved (propagation `error`) | `at_risk` |
| 5 | the latest scan failed on invalid CommitGuard configuration | `at_risk` |
| 6 | open critical violations | `at_risk` |
| 7 | monitor mode: violations are reported, not blocked | `at_risk` |
| 8 | an active exception lowers a high or critical rule | `at_risk` |
| 9 | branch protection has not been verified | `unknown` |
| 10 | otherwise | `secure` |

Rules 4-8 are cumulative: an `at_risk` repository lists every reason that
applies ("Monitor mode: violations are reported but not blocked." and
"2 open critical violation(s).").

## Organization posture

Again the first matching rule decides:

1. `unknown` - the organization has no repositories.
2. `at_risk` - an installation is suspended or removed, or its synchronisation
   failed; or any repository is `at_risk`; or **some** (not all) repositories are
   `unprotected`.
3. `unprotected` - **every** repository is proven unprotected.
4. `unknown` - protection of any repository is not verified.
5. `secure` - every repository is `secure` and installations are healthy.

So the organization is never shown as secure while enforcement, synchronisation
or policy propagation is unavailable:

```text
Organization posture: AT RISK

Installation octo-org (42): The installation is suspended.
3 repository(ies) at risk.
1 repository(ies) unprotected.
```

## Compliance fraction

Compliance is shown only as a fraction **with its definition**:

```text
94 of 100 required repositories satisfy all mandatory controls
```

* **Required repositories**: connected, not archived, and onboarded (or not yet
  classified). Excluded and archived repositories are not required, and the
  page shows how many there are.
* **Satisfy all mandatory controls**: repository posture `secure`.

This is a statement about CommitGuard policy enforcement. It is not a
percentage score, and it is not SOC 2, ISO 27001 or any other certification
([compliance-reporting.md](compliance-reporting.md)).

## Overview

`GET /api/v1/organizations/{id}/security/overview` (`security:read`) returns:

| Field | Content |
|---|---|
| `posture`, `posture_reasons` | the state and every reason for it |
| `compliance`, `required_repositories`, `compliant_repositories` | the fraction above |
| `by_posture`, `by_protection` | counts per state |
| `monitor_mode`, `critical_open`, `high_open` | counts |
| `active_exceptions`, `expiring_exceptions` (7 days), `expired_exceptions_30d` | exception activity |
| `drift` | `compliant`, `customized`, `drift`, `unknown` counts ([policy-inheritance.md](policy-inheritance.md#policy-drift)) |
| `installations` | connection and synchronisation health per installation ([repository-management.md](repository-management.md#inventory-and-synchronisation)) |
| `policy` | current versions, open drafts and approvals, rollouts in progress, propagation |
| `recent_activity` | recent governance audit events the viewer may see |
| `computed_at` | when it was computed |

Repository counts, violations, drift and the compliance fraction include only
repositories the viewer can see on GitHub (Phase 6 access scope), so two members
of the same organization can see different numbers. Exception counts, policy
status and installation health describe the organization as a whole.

## Repository security matrix

`GET /api/v1/organizations/{id}/security/repositories`: one row per repository
with the independent dimensions side by side: connection, archived,
onboarding, mode, protection (with reason), posture (with reasons), groups,
organization policy version, policy propagation state, last scan, open
violations and warnings, critical violations, active and expiring exceptions,
drift with its differences.

Filters: `q`, `group`, `posture`, `protection`, `mode`, `onboarding`,
`policy_state`, `drift`, `exceptions`, `severity`, `last_scan`.
`sort` = `name`, `posture` (worst first), `violations` or `last_scan`. Cursor
pagination: 25 rows by default, at most 100.

## Trends and staleness

`GET /api/v1/organizations/{id}/security/trends?days=30` (1-365):

* `history`: derived from recorded data (scans, blocked scans, scan errors,
  new violations, new critical violations per day);
* `snapshots`: protection, exceptions, drift and open violations per day. The
  past values of these cannot be reconstructed, so the maintenance loop records
  a daily snapshot. The response says so, and days before the first snapshot
  have no data rather than invented values.

Every computed response carries `computed_at`.

## Security events and acknowledgement

`GET .../security/events` lists recent critical and high organization events
(installation disconnected, repository unprotected, emergency policy publish,
rollout failure, propagation failure, critical violations) with their
acknowledgement.

`POST .../security/events/{event_id}/acknowledge` (`violations:manage`, optional
`note`) records that an administrator has seen an event. **Acknowledging
resolves nothing**: the posture, violation and notification are unchanged, and
the response says so. An event is acknowledged once; the acknowledgement is
audited (`security_event_acknowledged`).

## Alert aggregation

With the organization setting `aggregate_violation_alerts`, blocked-violation
alerts are grouped for external channels:

* every violation is still recorded, and in-app notifications stay per
  repository;
* e-mail and webhooks receive **one digest per rule per hour**:
  "CommitGuard detected blocked violations of ai_coauthor in 14 repositories";
* mandatory notifications (installation disconnected, repository unprotected,
  emergency publish, rollout failure, propagation failure) are never aggregated
  or muted.

## Search

`GET /api/v1/organizations/{id}/search?q=` (`organization:read`) searches
repositories, groups, policy drafts, rules, exceptions and findings. Results
are limited to what the viewer may read: repositories and findings they can see
on GitHub, exceptions and drafts they have permission for. Another
organization's resources never appear (tenant isolation test).

## Performance

Measured on a development machine (single SQLite file, Python 3.13), not a
production guarantee.

`tests/integration/github/app/dashboard/test_governance_performance.py`
(1,000 repositories, 20,000 scans, 100,000 findings; runs in the test suite
with budgets):

| Operation | Time |
|---|---|
| security overview | 200-280 ms |
| repository matrix page | 170-330 ms |
| propagate 1,000 effective policies | 0.3-0.5 s |
| bulk operation, 1,000 items (queue + process) | under 0.1 s |
| policy simulation | about 1 s |
| compliance report (CSV) | 0.35-0.45 s |

The same test pages through all 1,000 repositories of the matrix (10 pages of
100) and checks that each appears exactly once.

`scripts/benchmark_governance.py` (10,000 repositories, 100,000 scans,
1,000,000 findings):

| Operation | Time |
|---|---|
| security overview | 1.2 s |
| repository matrix page (sorted by violations) | 1.1 s |
| repository matrix search | 1.1 s |
| publish organization policy | 1.3 ms |
| propagate 10,000 effective policies (background) | 4.3 s |
| resolve 1,000 repositories (cached) | 31 ms |
| queue bulk operation (5,000 items) | 0.30 s |
| process bulk operation (5,000 items, background) | 0.28 s |
| policy simulation, bounded to 5,000 scans (background) | 7.0 s |
| trends (90 days) | 168 ms |
| compliance report (CSV) | 2.9 s |
| daily metrics snapshot | 73 ms |

Publishing a policy and handling a GitHub event do not scale with the number of
repositories: propagation, simulations, bulk operations and scheduled scans run
in bounded background batches. The overview and matrix compute posture per
request. With about 10,000 repositories they take roughly a second, which is
acceptable for an administrative page but is the first thing to cache
(materialize posture per repository) for larger organizations. SQLite remains a
single-writer database ([deployment.md](deployment.md)).
