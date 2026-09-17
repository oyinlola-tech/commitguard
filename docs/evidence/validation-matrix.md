# Validation matrix

What has been validated, how, and by whom. **Internal** means the author's own
tests and measurements; **external** means someone else; **deployment** means
running for a real team.

| Capability | Internal test | External test | Real deployment | Evidence |
|---|---|---|---|---|
| Detection accuracy | **Yes** - 9,174 labelled cases, 0 FN / 0 FP | Pending | Pending | [detection-evaluation](../research/detection-evaluation.md), 13 recorded runs |
| Detection under adversarial input | **Yes** - 74 adversarial cases, 7,476 fuzz examples per run | Pending | Pending | [fuzzing](../research/fuzzing.md) |
| Local hooks | **Yes** - 15/15 platform checks on Linux | Pending | Pending | `benchmarks/results/raw/platform/` |
| Local bypass behaviour | **Yes** - 7 bypasses recorded as observed | Pending | Pending | [bypass-resistance](../research/bypass-resistance.md) |
| GitHub Actions check | **Yes** - integration tests, including policy tampering | Pending | Pending | [policy-tampering](../research/policy-tampering.md) |
| GitHub App service | **Yes** - 7 server-side experiments against a fake GitHub | Pending | **Pending** - never run against github.com in production | [github-enforcement-validation](../research/github-enforcement-validation.md) |
| Merge queue and re-runs | **Yes** - modelled | Pending | Pending | [merge-queue-security](../research/merge-queue-security.md) |
| Organization policy governance | **Yes** - unit, integration and property tests | Pending | Pending | `tests/integration/github/app/dashboard/` |
| Mandatory policy floor | **Yes** - property-based over generated configurations | Pending | Pending | `tests/security/test_properties.py` |
| Reliability (outage, revoked permissions, database failure) | **Yes** - 4 experiments | Pending | Pending | [reliability-evaluation](../research/reliability-evaluation.md) |
| Cross-platform: Linux | **Yes** - full suite in CI, 15/15 platform checks | Pending | Pending | CI, recorded result |
| Cross-platform: macOS | **Partial** - full suite passing in CI; platform job added, not yet run | Pending | Pending | CI run 35187790650 |
| Cross-platform: Windows | **Partial** - 28 failures found and fixed locally, **not yet verified in CI** | Pending | Pending | [cross-platform-validation](../research/cross-platform-validation.md) |
| Performance | **Yes** - latency, scaling to 100k commits, memory, hook overhead | Pending | Pending | [performance-evaluation](../research/performance-evaluation.md) |
| ReDoS resistance | **Yes** - 33 patterns x 70 adversarial inputs | Pending | Pending | [fuzzing](../research/fuzzing.md) |
| Packaging and installation | **Partial** - wheel build and install smoke test added to CI, not yet run | Pending | Pending | `.github/workflows/ci.yml` (`package` job) |
| Reproducibility | **Yes** - `commitguard reproduce` re-runs the evidence | **Pending** - the point of the command | Pending | [reproducibility](../research/reproducibility.md) |
| Security review | **Self-review only** | **Pending** | n/a | [review-guide](../security/review-guide.md) |

## Summary

| | Count |
|---|---|
| Capabilities validated internally | 15 of 18 fully, 3 partially |
| Validated externally | **0** |
| Validated in a real deployment | **0** |

Every "Pending" in the external and deployment columns is accurate today. They
are the next thing this project needs, and no amount of additional internal
testing substitutes for them.
