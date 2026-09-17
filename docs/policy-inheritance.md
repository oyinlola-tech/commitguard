# Policy inheritance: what applies to a repository, and why

A repository's **effective policy** is built from several sources. This page
defines exactly how, and how the result is explained, cached and propagated.

```text
Service policy            COMMITGUARD_APP_MANDATORY_POLICY_FILE (operator)
    ↓
Security baseline         organization settings
    ↓
Organization policy       versioned; a staged rollout may apply a newer version to pilots
    ↓
Repository group policy   every group the repository belongs to
    ↓
Repository policy         versioned, set in the dashboard for one repository
    ↓
Repository configuration  .commitguard.yaml at the trusted revision (known at scan time)
    ↓
Approved exception        scoped, expiring
    ↓
Monitor mode              report instead of block
    ↓
Effective policy  ──►  CommitGuard policy engine  ──►  decision  ──►  GitHub check
```

The organization layer (`commitguard.governance.resolver`) **collects** these
inputs. The pure function `commitguard.policies.governance.resolve_policy`
**combines** them into a `PolicySet`. The policy engine
(`commitguard.policies.evaluator`) **evaluates** findings with it - unchanged
since Phase 2. There is one evaluator.

## Two strengths

Every entry of a policy version (organization, group or repository target) is
one of:

| Strength | Actions | Meaning |
|---|---|---|
| **mandatory** | `warn`, `block` | a floor: layers below may make the rule stricter, never weaker |
| **default** | `allow`, `warn`, `block` | a baseline that narrower layers and the repository configuration may replace in either direction |

A rule that no layer mentions is decided by the repository configuration, or by
the built-in default. The security baseline and the service policy only contain
mandatory entries.

Stored documents are canonical JSON. A mandatory entry is its action, a default
entry is an object:

```json
{"ai_coauthor": "block", "bot_identity": {"action": "warn", "enforcement": "default"}}
```

Organization policy versions saved before Phase 8 contain only mandatory
entries and keep their fingerprints.

## Precedence, exactly

For each rule, in order (later steps win within their limits):

1. **Built-in default** (`policies/defaults.py`).
2. **Organization default**.
3. **Repository group defaults** replace it. A repository in several groups gets
   the **most restrictive** of their defaults.
4. **Repository policy default** replaces it.
5. **Repository configuration** (`.commitguard.yaml` from the trusted revision,
   the same trust model as Phase 4) replaces any default, in either direction.
6. **Mandatory floor**: the most restrictive mandatory entry of the service
   policy, the security baseline, the organization policy, every group policy
   and the repository policy. If the result of steps 1-5 is weaker (a lower
   action, or `enabled: false`), the floor applies and a **conflict** is recorded.
7. **Approved exception**: if an active exception exists for the rule, the most
   specific scope wins (repository, then group, then organization); among
   exceptions of the same scope, the most restrictive action wins. An exception
   only ever lowers the action (to `warn` or `allow`).
8. **Monitor mode**: if the repository is in monitor mode, `block` is reported
   as `warn`.

The result is deterministic: it does not depend on the order in which layers are
stored or queried (tested), and the same inputs always produce the same
fingerprint.

### Examples

| Organization | Group | Repository policy | `.commitguard.yaml` | Effective |
|---|---|---|---|---|
| `ai_coauthor` mandatory `block` | - | - | `allow` | **block**, conflict recorded |
| `bot_identity` default `block` | default `warn` | default `allow` | - | **allow** (repository policy) |
| - | default `warn` | - | `allow` | **allow** (repository configuration) |
| `bot_identity` default `allow` | Production: mandatory `block` | - | - | **block** (group floor), conflict with the organization default |
| `ai_coauthor` mandatory `block` | - | default `allow` | - | **block**, conflict: the repository policy cannot weaken it |
| `ai_coauthor` mandatory `block` | - | - | - | **warn** with an approved repository exception (`warn`, until its expiry) |
| `ai_coauthor` mandatory `warn` | - | - | `enabled: false` | **warn** (disabled is evaluated at the floor) |

## Provenance

Every rule of an effective policy explains its origin:

```text
ai_coauthor      Action: BLOCK   Source: organization   organization policy v8   Enforcement: mandatory
bot_identity     Action: WARN    Source: repository configuration (.commitguard.yaml)
ai_trailer       Action: WARN    Source: exception (repository)   expires 2026-10-15   before: BLOCK
ai_identity      Action: WARN    Source: monitor mode             (block reported as warn)
```

`RuleProvenance` carries: `action`, `enabled`, `source` (built-in, service,
organization, group, repository policy, repository configuration, exception,
monitor mode), a human label with the version, `enforcement`, the floor
(`required_action`, `required_label`), the conflict if any, the exception and
its expiry, the action before the exception, and whether the repository
configuration was known.

Where it is shown:

* **Scans** store the full provenance in `scan_jobs.governance` together with
  the versions used (organization, groups, repository policy, settings,
  rollouts, exceptions, organization rules). `policy_source` reads, for example,
  `pull request base (3a91f02e7d5c) + organization policy v8 + group Backend policy v2 + 1 exception(s)`.
* **Repository page → Effective policy** (`GET /api/v1/repositories/{id}/effective-policy`)
  shows the current resolution, and the provenance recorded by the latest scan
  (which includes conflicts with `.commitguard.yaml`, because that file is only
  read at scan time from the trusted revision).

## Conflicts

A conflict is never resolved silently. It records what was requested, by whom,
what is required, by whom, and what applies:

```text
Policy conflict

Repository:               octo-org/payments-api
Requested:                ALLOW   (Repository configuration (.commitguard.yaml))
Organization requirement: BLOCK   (organization policy v8)
Effective:                BLOCK
Reason:                   organization policy v8 requires at least block and is
                          mandatory: Repository configuration (.commitguard.yaml)
                          cannot weaken it.
```

A conflict means that *someone asked for less*: an organization or group default,
a repository policy, or `.commitguard.yaml`. A mandatory requirement that is
simply stricter than CommitGuard's built-in default is not a conflict; the rule
is attributed to the requirement.

Conflicts also appear as **policy drift** in the repository security matrix
(see below). A pull request that *changes* `.commitguard.yaml` to weaken a policy
is still reported as a policy modification (Phase 4); that change only takes
effect once merged, and even then the floor applies.

## Policy drift

Drift compares a repository with the organization's requirements, from the
provenance its latest scan recorded:

| Status | Meaning |
|---|---|
| `compliant` | no conflicts, and the repository configuration changes nothing |
| `customized` | the repository changes defaults it is allowed to change |
| `drift` | the repository asks for less than a mandatory requirement; each difference is listed and the requirement still applies |
| `unknown` | no scan has recorded provenance yet |

A drift entry is always explained, never a bare flag:

```text
Repository policy differs from organization baseline.
Difference: ai_coauthor   Organization: BLOCK   Repository: WARN   Effective: BLOCK
```

## Effective policy cache and propagation

Resolving a repository's governance inputs takes a few indexed queries. The
result is cached in `repository_effective_policies` with a state:

| State | Meaning |
|---|---|
| `up_to_date` | resolved from the current versions, memberships, exceptions and mode |
| `stale` | something that affects the repository changed; not yet re-resolved |
| `syncing` | the propagation job is resolving it |
| `error` | resolving failed; administrators are notified (`policy_propagation_failed`) |
| `pending` | never resolved (for example a newly discovered repository) |

**Correctness does not depend on the cache.** Every change marks the affected
rows `stale` in the same database transaction as the change itself, and
resolution runs inside a single transaction. A scan uses a cached row only if it
is `up_to_date` and no exception it includes has expired (`valid_until`);
otherwise it resolves from the database. If resolution fails during a scan, the
scan fails closed (no success check).

**Targeted invalidation:**

| Change | Invalidated |
|---|---|
| organization policy, security baseline, settings, organization rules, organization-wide exception | every repository of the organization |
| group policy, group exception, group archived | the group's members |
| group membership change | the repositories added or removed |
| repository policy, repository exception, onboarding mode | that repository |
| rollout stage enrolled / completed / rolled back | the repositories enrolled or in scope |

**Propagation** (`GovernanceResolver.propagate`, maintenance loop every minute,
batches of 200) resolves stale, failed and never-resolved repositories.
`GET /api/v1/organizations/{id}/policy-propagation` reports counts per state and
lists repositories in error:

```text
Policy v20 propagation: 94 / 100 repositories up to date - 6 syncing
```

A change is shown as propagated (`complete: true`) only when **every**
repository of the organization is `up_to_date`. Propagation means the
*effective policy* is current; a GitHub check reflects it after the next scan of
the pull request or branch (a push, a re-run, **Scan again**, or a
[scheduled scan](repository-management.md#scheduled-scans)). The repository page
shows whether the latest scan used the current effective policy.

## Rollback and history

* Rolling back an organization, group or repository policy publishes a **new**
  version (Phase 7); every affected repository is invalidated and re-resolves
  the restored document. Older scans keep the versions they recorded.
* A staged rollout decides which version applies to which repository while it
  is in progress ([policy-rollouts.md](policy-rollouts.md)).
* Historical scans are never rewritten by policy changes, rollbacks, exception
  expiry, group membership changes or mode changes.

## Performance

Resolution is cached per repository and invalidated precisely, so a GitHub
event does not rebuild the organization's policy graph. Measured with 10,000
repositories: resolving 1,000 cached effective policies takes about 30 ms;
propagating 10,000 after an organization-wide change takes about 4.3 seconds in
the background. See [security-posture.md](security-posture.md#performance).
