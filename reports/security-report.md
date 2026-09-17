# CommitGuard security report

Generated 2026-09-17T21:23:37.968817+00:00 for CommitGuard 0.1.0.dev0.

Every row is labelled: **Measured** (a number from a recorded benchmark run),
**Tested** (an automated test asserts it), **Observed** (an experiment recorded what
happened, including bypasses), **Expected** (design intent, not yet covered by a test)
or **Not tested**. Nothing in this report is estimated.

## Environment of the latest runs

- Detection: Linux 7.1.5+kali-amd64 (x86_64), Intel(R) Core(TM) i5-8350U CPU @ 1.70GHz, 8 CPUs, Python 3.13.15, Git 2.53.0
- Source revision: `f680f18797c82a4ca7e98c94f110a982e98d6f67` (dirty: True)

## Detection

| Statement | Label | Evidence |
|---|---|---|
| False negatives | Measured | 0 of 3709 cases that must not be allowed (dataset 1.3.0) |
| False positives | Measured | 0 of 5465 clean cases |
| Precision / recall | Measured | 100.000% / 100.000% |
| False positive rate / false negative rate | Measured | 0.000% / 0.000% |
| Detection latency per commit | Measured | p50 0.1885 ms, p95 0.3949 ms, p99 0.7847 ms |

## Performance

| Statement | Label | Evidence |
|---|---|---|
| Throughput | Measured | 7592.2 commits/s on 10000 commits (p50 0.1027 ms per commit) |
| Large commit messages | Measured | 10,485,736 byte message: p50 592.200 ms |
| Peak memory | Measured | 83.1 MiB for the whole run |
| Repository history scanning | Measured | 100000 commits in 43.9 s (2279 commits/s); 5000 of 5000 seeded violations found |
| Hook overhead: git commit (clean) | Measured | +947 ms (6.2 -> 953.0 ms, median of 20 runs) |
| Hook overhead: git push (clean, one commit) | Measured | +455 ms (17.1 -> 471.9 ms, median of 20 runs) |
| clean commit allowed without hooks | Observed | exit 0 x20 (expected exit 0 every time) |
| clean commit allowed with hooks | Observed | exit 0 x20 (expected exit 0 every time) |
| AI co-authored commit blocked by commit hooks | Observed | exit 1 x20 (expected exit non-zero every time) |
| --no-verify bypasses local commit hooks | Observed | exit 0 x20 (expected exit 0 every time) |
| clean push allowed by pre-push | Observed | exit 0 x20 (expected exit 0 every time) |
| AI co-authored push blocked by pre-push | Observed | exit 1 x20 (expected exit non-zero every time) |
| --no-verify bypasses the local pre-push hook | Observed | exit 0 x20 (expected exit 0 every time) |

## Cross-platform validation

| Statement | Label | Evidence |
|---|---|---|
| Local enforcement on Linux 7.1.5+kali-amd64 (x86_64) | Tested | 15 passed, 0 failed, 0 skipped |

## Security experiments

13 recorded experiments: 5 detected, 8 prevented.

| Area | Attack | Outcome | Observed | Mitigation |
|---|---|---|---|---|
| GitHub Actions | A pull request removes or neuters the CommitGuard workflow so no check runs | **prevented** | no check conclusion for 13196fdab6ea; modelled gate allows merge: False | Require the check in branch protection or a ruleset; prefer the GitHub App, which runs outside the repository |
| GitHub App events | Redeliver an old webhook (same delivery ID), then deliver the old event again under a new delivery ID after a newer commit was scanned | **prevented** | replay response {'status': 'duplicate'}; late event response {'status': 'duplicate'}; newest commit check still failure; old commit job states ['passed'] | Delivery records with payload digests; check ownership per repository, SHA and check name; newest-scan-wins |
| GitHub App webhooks | Deliver a pull_request webhook signed with a wrong secret, a correctly signed body altered after signing, and an unsigned body | **prevented** | HTTP 401 (wrong secret), 401 (altered body), 401 (unsigned), reasons ["{'error': 'invalid webhook signature'}", "{'error': 'invalid webhook signature'}", "{'error': 'missing webhook signature'}"]; scans processed 0; check runs before the control 0; correctly signed control: {'status': 'queued'} | HMAC-SHA256 signature verified in constant time over the raw body |
| GitHub checks | Re-run the passing check of an outdated commit after a violating commit was pushed; queue a pull request whose own head passed while an AI-attributed change is queued ahead of it | **prevented** | stale re-run response {'status': 'ignored'}; newest pull request check failure; pull request head check success; merge group 96bce542fa73 check failure | Re-runs only for the newest commit of a pull request or branch; merge_group events scan base..merge-group SHA |
| GitHub integration | The GitHub App installation is suspended, then a pull request is opened | **detected** | installation state suspended; scans processed 0; checks []; audit contains installation_suspended: True; notification installation_disconnected: True | Installation lifecycle events, critical notification, AT RISK posture |
| central policy | The repository's merged (trusted) .commitguard.yaml disables ai_coauthor | **prevented** | without mandatory policy: success; with mandatory policy: failure | Mandatory policy floors (service policy, Phase 8 organization/group mandatory entries) |
| local hooks | Delete the pre-commit, commit-msg and pre-push hook files, then commit and push | **detected** | commit exit 0 (allowed); doctor exit 2, reports missing hooks: True; pull request check exit 1 (BLOCK) | commitguard doctor; server-side required check |
| local hooks | Edit the managed hook block so it exits 0 before running CommitGuard | **detected** | commit exit 0; doctor reports modified hooks: True; pull request check exit 1 (BLOCK) | Managed-block checksum reported by commitguard doctor; server-side required check |
| local hooks | Point core.hooksPath at an empty directory; separately, commit from a fresh clone without installing hooks | **detected** | core.hooksPath commit exit 0; fresh clone commit exit 0; pull request checks exit 1 and 1 | Server-side required check; commitguard install --global for developer machines |
| local hooks | git commit --no-verify and git push --no-verify with an AI co-author trailer | **detected** | normal commit exit 1; --no-verify commit exit 0; --no-verify push exit 0; pull request check exit 1 (BLOCK); modelled required-check gate allows merge: False | Server-side check (GitHub Actions or GitHub App) required by branch protection or a ruleset |
| reliability | GitHub's API is unreachable during a scan of a clean commit; separately, the installation's Checks permission is reduced to read | **prevented** | outage: job error (infrastructure), check conclusions []; revoked: job error (authorization), check runs 0 | Bounded retries, then ERROR; never a success conclusion without a completed evaluation |
| reliability | The service database fails while a webhook is received | **prevented** | HTTP 500 {"error": "internal error"}; scans processed 0; checks while down []; redelivery response {'status': 'queued'}; check after recovery success | Event recorded before processing; errors surface as 5xx; redelivery processed |
| repository configuration | In the pull request itself, disable ai_coauthor, set it to allow, or delete .commitguard.yaml | **prevented** | pull request check exit codes: disable=1, allow=1, delete=1 | Trusted policy source: the base commit (Phase 4) |

## Fuzzing and ReDoS

- **Tested**: 7479 property-based examples across 25 properties (derandomized).
- **Measured**: 33 regular expressions run against 70 adversarial inputs of 50000 characters; worst case 118.9 ms (budget 500 ms).

## Recorded runs

Results are immutable: a new run is a new file, and earlier results are kept.

| Benchmark | Runs |
|---|---|
| detection | 14 |
| hooks | 1 |
| performance | 3 |
| platform | 1 |
| repository | 1 |

## What this report does not say

- It does not claim CommitGuard cannot be bypassed: local hooks are bypassable by the
  person running them, and the experiments record exactly that.
- Results labelled Measured come from the machine named above, not from a controlled
  laboratory, and were not repeated across machines.
- No external party has reproduced these results yet.

