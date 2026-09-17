# CommitGuard: architecture

A design paper: the system, the reasoning behind its shape, and the properties
that shape is meant to guarantee. The implementation reference is
[../architecture.md](../architecture.md).

## The problem, in one line

Decide whether a commit's metadata is acceptable under a policy, and make that
decision at every point where a commit can enter a repository, without ever
claiming a safety that was not verified.

## Layering

```text
          Organization  ──▶ central policy, groups, exceptions, rollouts
                │
        Effective policy  ──▶ resolved per repository, mandatory floor last
                │
        CommitGuard core  ──▶ detection engine  +  policy engine
                │
   ┌────────────┼────────────┐
Git hooks   GitHub Actions   GitHub App
(advisory)  (authoritative)  (authoritative, central)
   │             │                │
   └─────────────┴────────────────┘
                │
     Findings · Audit · Notifications · Dashboard
```

The same engine runs in all three places. That is the central design decision:
one implementation of "is this commit acceptable", three delivery mechanisms with
different trust properties.

## Detection and policy are separate

| | Detector | Policy |
|---|---|---|
| Question | *Does this commit carry AI attribution, and what is the evidence?* | *Is that allowed here?* |
| Output | a `Finding`: rule, severity, confidence, evidence, remediation | a `Decision`: allow, warn or block, with reasons |
| Knows about | one commit and the rule data | configuration |
| Side effects | none | none |

Why it matters: the evidence does not change when the policy does. A repository
that allows AI attribution still produces findings, so the provenance record
exists even where enforcement is off. It also means the detection accuracy
measurements are independent of any policy choice.

Detectors are **pure and deterministic** by contract, and the contract is
enforced: a detector may only emit rules it declared, only for the commit it was
given. Violating that is a detection failure, which blocks.

## Precedence is total and order-independent

`block > warn > allow`, whatever order detectors ran in. A detector that raises
produces a failure that blocks. A finding whose rule has no policy blocks. Both
are asserted as properties over generated inputs, not examples.

The rule underneath: **no path converts "could not evaluate" into "allowed"**.

## Configuration layering, and the one direction it cannot go

```text
built-in defaults ─▶ ~/.config/commitguard/config.yaml ─▶ .commitguard.yaml ─▶ --config
                                                                                  │
                                                          mandatory policy ────────┘ (applied last)
```

Ordinary layers override each other. The mandatory policy is applied afterwards
and takes the *more restrictive* of the two actions, so a central floor cannot be
lowered locally, and `enabled: false` in a mandatory policy is rejected rather
than ignored. That asymmetry is what makes organization governance meaningful.

## Trust boundaries

| Boundary | What is trusted |
|---|---|
| Repository content | **Nothing.** Commit messages, identities and configuration in a pull request are attacker-controlled |
| Policy source | The **base** commit for a pull request; a pull request cannot judge itself |
| Rule data | The installed CommitGuard package only, never the repository |
| The scanner binary | Installed from a trusted commit in the workflow, via `git worktree` |
| Webhooks | Only after HMAC-SHA256 verification over the raw body, with duplicate detection |
| GitHub permissions | GitHub is the authority on what a user may see; CommitGuard never widens it |
| Tokens | Installation tokens are short-lived, down-scoped to one repository, and never stored |

## The three delivery layers, and what each is worth

| Layer | Stops | Bypassable by the contributor | Latency |
|---|---|---|---|
| Git hooks | accidents, before anything leaves the machine | **yes** - measured, seven ways | ~1 s per commit |
| GitHub Actions check | every commit a pull request introduces | no; but it only *reports* unless required | CI time |
| GitHub App | the same, centrally, with policy the repository cannot weaken | no | seconds after the webhook |

Hooks are deliberately not presented as a control. They exist because feedback
one second after `git commit` is worth more than feedback five minutes later in
CI - and because an honest tool says which layer is doing the work.

## Data model choices worth defending

- **Findings carry evidence, not content.** The matched trailer or identity,
  bounded to 512 characters, never file contents or whole commit messages.
- **Policy versions are immutable.** A rollback is a new version; the history of
  what was enforced when survives.
- **Scans are versioned executions.** A GitHub "Re-run" is a new numbered
  execution; earlier results are kept, and a stale re-run cannot become current.
- **Results are append-only in research too.** The same discipline as policy
  versions, applied to the project's own measurements.

## What the architecture does not attempt

- Proving authorship. Metadata is self-asserted; see
  [limitations.md](limitations.md).
- Blocking merges by itself. Only branch protection can do that, and CommitGuard
  reports its inability to verify it.
- Running repository code. The App never checks out or executes anything from a
  repository; it fetches metadata.
- Horizontal scale. SQLite and an in-process queue mean one instance per database
  - adequate for the scale it has been tested at, and stated rather than implied.
