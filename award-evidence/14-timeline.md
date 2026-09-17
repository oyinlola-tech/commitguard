# 14 - Timeline

Dates from the repository history and recorded benchmark results. Full version:
[docs/evidence/timeline.md](../docs/evidence/timeline.md).

| Date | Milestone |
|---|---|
| 2026-09-14 | Project started; repository public |
| 2026-09-14 | Phase 1: architecture, commit model, policy engine |
| 2026-09-14 | Phase 2: detection engine, rules as data |
| 2026-09-15 | Phase 3: Git hooks with chaining and integrity checks |
| 2026-09-15 | Phase 4: GitHub Actions check, trusted policy source |
| 2026-09-15 | Phase 5: GitHub App service |
| 2026-09-15 | Phase 6: dashboard and control plane |
| 2026-09-15 | Phase 7: notifications, merge queue, policy recovery |
| 2026-09-17 | Phase 8: organization governance |
| 2026-09-17 | Phase 9: research and benchmarking platform |
| 2026-09-17 13:24 UTC | First detection benchmark: **found a bypass** |
| 2026-09-17 14:16 UTC | The fix introduced false positives; the next run caught them |
| 2026-09-17 14:35 UTC | Performance defect fixed: 10 MB message 4.8 s to 0.75 s |
| 2026-09-17 14:54 UTC | 100,000-commit history scanned in 43.9 s; Linux platform 15/15 |
| 2026-09-17 14:58 UTC | **Fuzzing found a second bypass class** |
| 2026-09-17 15:12 UTC | **Fuzzing found a third bypass class** |
| 2026-09-17 15:15 UTC | All four dataset versions clean |
| 2026-09-17 | Phase 10: reproduction command, examples, release engineering, two ReDoS fixes, full documentation set |

## Not yet

First release · first external evaluation · first external contribution · first
external deployment · first externally reported vulnerability · independent
reproduction of the benchmarks.
