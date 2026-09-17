# CommitGuard: technical overview

A presentation outline and a script for the demonstration recording. Everything
below is shown with the real software; no slide states a number that is not in a
recorded result.

## Audience and claim

Engineers and security reviewers. The claim being demonstrated: *a contribution
policy can be enforced across machines, repositories and an organization, with
measured accuracy and explicit limits.*

## Structure (5-10 minutes)

| Time | Section | Shown |
|---|---|---|
| 00:00 | **The problem** | A commit with `Co-authored-by: Claude <...>`; why grep fails in both directions |
| 00:45 | **Threat model** | Trust boundaries; the five ways any check can be avoided |
| 02:00 | **Local enforcement** | `commitguard init --install-hooks`, `doctor`, a blocked commit, exit 1, message preserved |
| 03:00 | **The local bypass** | `--no-verify` succeeds; `doctor` reports hook state honestly |
| 04:00 | **GitHub enforcement** | The check fails the pull request; editing `.commitguard.yaml` in the PR does not help (policy comes from the base commit) |
| 05:00 | **Organization policy** | A mandatory floor a repository cannot lower; scoped expiring exceptions |
| 06:00 | **Dashboard** | What was scanned, what was blocked, the evidence, the policy version, the audit trail |
| 07:00 | **Benchmark evidence** | `commitguard reproduce benchmark`: 9,174 cases, fingerprint verified, 0 FN / 0 FP; the four published failing runs |
| 08:00 | **Security testing** | The three bypasses found by dataset and fuzzing; ReDoS findings; `pytest -m security` |
| 09:00 | **Limitations** | Metadata is not proof; hooks are advisory; branch protection is GitHub's; no external validation yet |

## Speaking notes for the difficult slides

**Slide: the local bypass.** Do not apologise for it. The point is that the tool
publishes seven ways around its own first layer, measures them, and shows the
second layer catching each one.

**Slide: benchmark evidence.** Show the *failing* runs first, then the current one.
The sequence is the credibility.

**Slide: hook overhead.** State +947 ms per commit plainly, explain that it is
interpreter start-up rather than analysis, and say it is a known problem with a
known fix that has not been done.

**Slide: limitations.** Finish here, not on a feature list.

## Assets

- Terminal recordings from a clean repository (no private data).
- Dashboard screenshots from the demo stack, which replays a full lifecycle
  through the real services.
- `reports/security-report.md`, generated from recorded evidence.

## Status

**PLANNED.** The recording has not been made. The outline is here so it can be
produced from real runs rather than reconstructed later.
