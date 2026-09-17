# Performance evaluation

**Measured 2026-09-17** on Linux 7.1.5 (x86_64), Intel i5-8350U (8 CPUs), 16 GB,
Python 3.13.15, Git 2.53.0, CommitGuard 0.1.0.dev0.

## Detection latency and throughput

| Commits in one batch | p50 per commit | Commits/second |
|---|---|---|
| 1 | 0.2291 ms | 4,317 |
| 10 | 0.1348 ms | 5,959 |
| 100 | 0.1419 ms | 5,711 |
| 1,000 | 0.1081 ms | 7,970 |
| 10,000 | 0.1162 ms | 6,901 |

Rule loading (once per process): **28.6 ms**. Peak resident memory for the whole
benchmark: **82 MiB**.

## Commit message size

Detection is linear in message size, and this is where a real defect was found.

| Message size | p50 **before** | p50 **after** | Improvement |
|---|---|---|---|
| 1 KB | 0.6368 ms | 0.2483 ms | 2.6x |
| 10 KB | 4.3845 ms | 0.8415 ms | 5.2x |
| 100 KB | 45.93 ms | 6.999 ms | 6.6x |
| 1 MB | 531.2 ms | 74.85 ms | 7.1x |
| 10 MB | 4,763 ms | 751.8 ms | 6.3x |

**The defect:** normalisation categorised every character individually, so a large
commit message could occupy a hook - or an App worker - for seconds. A commit
message is attacker-controlled and unbounded in practice, which makes this a
denial-of-service surface, not just slowness. **The fix:** an ASCII fast path
(most text is ASCII, and ASCII has no format or default-ignorable characters).
Old and new implementations were checked for identical output on 50,000 random
strings before the change was kept.

Both runs are recorded: `benchmarks/results/raw/performance/`.

## Repository history scanning

Real repositories built with `git fast-import`, every 20th commit carrying an AI
co-author trailer. Listing, reading and analysing are measured separately.

| Commits | List | Read | Analyse | Total | Commits/s | Seeded violations found |
|---|---|---|---|---|---|---|
| 100 | 3.0 ms | 11.5 ms | 14.3 ms | 28.8 ms | 3,470 | 5 / 5 |
| 1,000 | 11.2 ms | 130.3 ms | 142.6 ms | 284.1 ms | 3,520 | 50 / 50 |
| 10,000 | 78.7 ms | 1,268.6 ms | 2,181.2 ms | 3,528.5 ms | 2,834 | 500 / 500 |
| 100,000 | 1,175.0 ms | 13,836.2 ms | 28,875.3 ms | 43,886.4 ms | 2,279 | 5,000 / 5,000 |

Scanning scales linearly; reading commit metadata from Git costs roughly as much
as analysing it. A 100,000-commit history is scanned in **44 seconds**, with every
seeded violation found.

(`commitguard scan` applies a 1,000-commit safety bound by default; this
benchmark calls the same library functions without it to measure scaling.)

## Hook overhead: what a developer actually waits for

20 repetitions of real `git` operations, with and without hooks:

| Operation | Without hooks | With hooks | Overhead |
|---|---|---|---|
| `git commit` (clean) | 6.16 ms | 953.0 ms | **+947 ms** |
| `git push` (clean, one commit) | 17.1 ms | 471.9 ms | **+455 ms** |

This is the least flattering measurement in the project, and the most useful.
Detection itself costs about **0.1 ms**; the rest is Python interpreter start-up
and imports, paid twice per commit (`pre-commit` and `commit-msg`) and once per
push.

Honest caveats: this machine is a 2017 mobile i5, and four documentation agents
were running during the recording, so it is an upper bound rather than a typical
figure. An earlier 5-repetition trial on an idle machine measured about +790 ms
per commit. Either way the conclusion is the same: **the cost is process
start-up, not analysis.**

It is a usability problem, not a correctness one, and the fix is known (fewer
imports on the hook path, or a single invocation per commit). It is recorded here
rather than quietly omitted.

## Where the time goes

```text
git commit with hooks  ~953 ms
├── pre-commit hook    ~470 ms   ── Python start-up + imports ~465 ms, detection ~0.2 ms
├── commit-msg hook    ~470 ms   ── same
└── git itself         ~6 ms
```

## Method notes

- Percentiles by nearest rank; no warm-up discarded.
- Allocation profiling runs in a separate pass: `tracemalloc` inflated latency by
  about 2.5x when enabled during timing.
- Pushes go to a local bare repository, so no network latency is included.
- Every figure above comes from a recorded result file with a full environment
  manifest.
