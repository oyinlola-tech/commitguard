# Benchmark evidence

The evidence itself lives in [`benchmarks/results/`](../../benchmarks/results/),
which is append-only: results are never overwritten, so failing runs stay
published.

| Benchmark | Runs recorded | Latest result |
|---|---|---|
| detection | 13 (4 of them failing) | 0 false negatives, 0 false positives on 9,174 cases |
| performance | 2 (before and after the normalisation fix) | 6,901 commits/s; 10 MB message 752 ms |
| hooks | 1 (20 repetitions) | +947 ms per commit, +455 ms per push |
| repository | 1 | 100,000 commits scanned in 43.9 s |
| platform | 1 (Linux) | 15/15 checks passed |

Read them with [docs/research/](../../docs/research/), which explains what each
number means and does not mean.

Regenerate the index and reports:

```bash
commitguard reproduce benchmark --results benchmarks/results
commitguard report security --results benchmarks/results --evidence evidence/security
```
