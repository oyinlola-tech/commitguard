# Bypass resistance

What happens when someone actively tries to get a violating commit through. Each
scenario below is a test that records what it observed, in
`tests/integration/github/security/test_bypass_resistance.py`, with the evidence
in `evidence/security/experiments.jsonl`.

Six local experiments, **recorded 2026-09-17**. Outcomes: five `detected`, one
`prevented`.

## The honest summary

Local hooks stop accidents. They do not stop anyone who does not want to be
stopped, and CommitGuard does not pretend otherwise: every local bypass below
**succeeds locally** and is then **caught by the server-side check**.

| Attack | Local result | Server-side result | Outcome |
|---|---|---|---|
| `git commit --no-verify` / `git push --no-verify` | commit created, push accepted (exit 0) | pull request check exit 1 (BLOCK) | detected |
| Delete the hook files | commit created (exit 0) | `doctor` exit 2 reports the hooks missing; check exit 1 | detected |
| Edit the managed hook block to exit early | commit created | `doctor` reports modified hooks; check exit 1 | detected |
| Point `core.hooksPath` at an empty directory | commit created | check exit 1 | detected |
| Commit from a fresh clone (no hooks installed) | commit created | check exit 1 | detected |
| Edit `.commitguard.yaml` in the pull request to allow it | n/a | check exit 1 for disable, allow and delete | prevented |
| Remove the workflow in the pull request | no check runs | a required check that never reports is not satisfied (modelled gate: merge not allowed) | prevented |

## Why "detected" and not "prevented"

The distinction is deliberate. For the local layer the attack **succeeds**: the
commit exists on the developer's machine. What CommitGuard provides is that the
attempt cannot reach a protected branch unnoticed:

1. `commitguard doctor` reports missing, modified or misdirected hooks, so the
   state is visible rather than silently absent;
2. the server-side check re-evaluates every commit a pull request introduces,
   from the base commit's policy, so nothing about the contributor's machine
   matters.

Claiming "prevented" for the local layer would be the kind of overstatement this
project exists to avoid.

## What genuinely cannot be bypassed this way

The `policy-tampering-in-pull-request` experiment is `prevented`, not `detected`:
a pull request that disables the policy, sets it to `allow`, or deletes the
configuration file entirely still gets exit 1, because the policy is read from the
**base** commit and the rules come from the installed CommitGuard.
See [policy-tampering.md](policy-tampering.md).

## The limit that no layer removes

If the attribution is never written into the commit, there is nothing to detect.
An agent configured not to add a trailer, or a developer who deletes it, produces
a commit indistinguishable from a human's. That is a property of self-asserted
metadata, not a gap in the implementation
([limitations.md](limitations.md)).
