# 07 - Benchmarks

All **MEASURED** on 2026-09-17: Linux 7.1.5 (x86_64), Intel i5-8350U, 8 CPUs,
16 GB, Python 3.13.15, Git 2.53.0, CommitGuard 0.1.0.dev0. Every number below
comes from a recorded result file with a full environment manifest, and can be
re-run with `commitguard reproduce all`.

## Detection accuracy (dataset 1.3.0, 9,174 labelled cases)

| Metric | Value |
|---|---|
| False negatives | **0** of 3,709 |
| False positives | **0** of 5,465 |
| Precision / recall / F1 | 100% / 100% / 1.00 |
| Exact decision match | 9,174 / 9,174 |
| Latency per commit | p50 0.22 ms, p95 0.44 ms, p99 0.67 ms |

Read with the next table: this is the state **after** fixing three bypasses these
benchmarks found.

## The runs that failed (published, not deleted)

| Run | Dataset | Result | What it found |
|---|---|---|---|
| 1 | 1.0.0 | 1 false negative | a replacement character before the key hid attribution |
| 2 | 1.1.0 | 2 false positives | the fix flagged ordinary bulleted **human** trailers |
| 5 | 1.2.0 | 11 false negatives | Unicode letters/numbers before the key (found by fuzzing) |
| 9 | 1.3.0 | 15 false negatives | default-ignorable characters (found by fuzzing) |

## Performance, before and after a real fix

| Measure | Before | After | Change |
|---|---|---|---|
| 10,000-commit batch, p50 per commit | 0.3455 ms | 0.1162 ms | **3.0x** |
| Throughput | 2,538/s | 6,901/s | **2.7x** |
| 1 MB commit message | 531 ms | 74.9 ms | **7.1x** |
| 10 MB commit message | 4,763 ms | 752 ms | **6.3x** |

The defect: per-character Unicode categorisation made large commit messages a
denial-of-service surface. Both runs are published.

## Scale

| Commits in history | Total scan | Throughput | Seeded violations found |
|---|---|---|---|
| 1,000 | 0.28 s | 3,520/s | 50 / 50 |
| 10,000 | 3.5 s | 2,834/s | 500 / 500 |
| **100,000** | **43.9 s** | 2,279/s | **5,000 / 5,000** |

Peak memory for a full benchmark run: 82 MiB.

## Hook overhead - the least flattering measurement

| Operation | Without hooks | With hooks | Overhead |
|---|---|---|---|
| `git commit` | 6.2 ms | 953 ms | **+947 ms** |
| `git push` | 17 ms | 472 ms | **+455 ms** |

Detection itself costs ~0.1 ms; the rest is Python interpreter start-up, paid
twice per commit. It is published because it is true, and it is the clearest
example of what the benchmark discipline is for: the number nobody would volunteer.

## Robustness

| | |
|---|---|
| Property-based examples per run | **7,476** across 25 properties |
| Regular expressions measured for ReDoS | **33**, against 70 adversarial 50,000-character inputs each |
| Worst regular-expression case | 133.9 ms against a 500 ms budget |
| Security-marked tests | **350**, run in 69 seconds |
