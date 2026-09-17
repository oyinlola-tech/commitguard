# Policy exceptions

Real organizations sometimes need a temporary exception: a legacy repository
being migrated, a generator that writes unusual trailers. An exception lowers
the action of **one rule** within **one explicit scope** until it **expires**.
It is the only way below a mandatory requirement, so it is bounded on every side.

```text
Repository:  legacy-project
Rule:        ai_coauthor
Exception:   warn (instead of block)
Expires:     2026-10-15
Reason:      Migration project
Requested:   sam        Approved: ada
```

## Scope

| Scope | Applies to | Approval |
|---|---|---|
| `repository` | that repository only | when the rule's severity is at or above `exception_approval_min_severity` (default `high`) |
| `group` | the group's current members | always |
| `organization` | every repository of the organization | always |

The scope is explicit and validated: a repository exception names a repository
of the same organization that the requester can see on GitHub; a group
exception names an active group of the organization. An exception for one
repository can never apply to another, and nothing widens a scope after the
fact. If several exceptions apply to the same rule, the most specific scope
wins (repository, then group, then organization).

**Finding-level exceptions are not supported.** To record that one finding was
reviewed, acknowledge the violation; that never changes enforcement.

## Action

`warn` or `allow`. An exception never raises enforcement and never removes
detection: findings are still recorded with the lowered action and the exception
that lowered it.

## Expiry

* `expires_at` is required, in the future, and at most `exception_max_days`
  (default 90) ahead.
* A **permanent** exception needs all of: the organization setting
  `allow_permanent_exceptions`, a requester with `exceptions:approve`, and
  approval by someone else. A database check constraint refuses a row that is
  neither permanent nor expiring.
* The maintenance loop (`expire_policy_exceptions`, every minute) moves
  `active → expired` once `expires_at` passes, invalidates the affected
  effective policies, audits it (`exception_expired`, actor `system`) and
  notifies (`exception_ended`). Even before the worker runs, the resolver
  ignores an exception past its expiry.
* **Warnings** before expiry at the configured days (`exception_warning_days`,
  default 7, 3 and 1): one notification per threshold crossed
  (`exception_expiring`). Crossing several thresholds at once sends only the
  most urgent one, and a threshold is never repeated on later polling cycles.

## Lifecycle

```text
requested ──approve──► active ──expire──► expired
    │                    └────revoke──► revoked
    ├──reject──► rejected
    └──cancel──► cancelled
```

Exceptions that need no approval start `active`. The state names map to the
approval workflow: REQUESTED = `requested`, REVIEW = waiting for a decision,
APPROVED/ACTIVE = `active` (with `decided_by`), EXPIRED = `expired`.

Rows are never deleted: a database trigger refuses `DELETE`, and the API has no
delete operation. At most one **open** (`requested` or `active`) exception exists
per rule and scope.

## Authorization

| Action | Permission |
|---|---|
| list, view | `exceptions:read` (repository exceptions only for repositories the viewer can see) |
| request | `exceptions:create` (security managers and above) |
| approve, reject | `exceptions:approve`; the requester can never approve their own exception |
| revoke | `exceptions:revoke` (reason required) |
| cancel a request | the requester, or `exceptions:revoke` |

A viewer cannot request an exception; a requester cannot approve it; another
organization's members receive 404 for every exception route.

## Audit and notifications

| Event | Audit | Notification (recipients) |
|---|---|---|
| requested | `exception_requested` (rule, scope, action, expiry, approval needed, reason) | `exception_requested` when approval is needed (`exceptions:approve`) |
| approved | `exception_approved` (approver, note) | `exception_approved` (`exceptions:read`) |
| rejected / cancelled | `exception_rejected` / `exception_cancelled` | - |
| revoked | `exception_revoked` (reason) | `exception_ended` |
| expiring | - | `exception_expiring` per threshold |
| expired | `exception_expired` (system) | `exception_ended` |

Every transition that changes an active exception invalidates the effective
policy of exactly the repositories in scope, in the same transaction.

## Posture

A repository with an active exception that lowers a `high` or `critical` rule is
never shown as `secure`: its posture is `at_risk` with the reason
"N active exception(s) lower high or critical rules". Repository pages and the
security matrix show active exceptions and those expiring within 7 days; the
organization overview counts active, expiring and recently expired exceptions.

## API

`GET/POST /api/v1/organizations/{id}/exceptions`, `GET /api/v1/exceptions/{id}`,
`POST /api/v1/exceptions/{id}/approve|reject|revoke|cancel`. Request body:

```json
{
  "rule_id": "ai_coauthor",
  "scope_type": "repository",
  "scope_id": 5001,
  "action": "warn",
  "reason": "Migration project",
  "expires_at": "2026-10-15T00:00:00Z"
}
```
