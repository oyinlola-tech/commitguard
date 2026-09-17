# 13 - Impact

Only what can be counted.

## Security defects found and fixed before any user met them

**MEASURED.** Ten defects, eight security-relevant, all found by the project's own
measurement infrastructure:

| Defect | Class | Found by |
|---|---|---|
| Symbol before a trailer key hid attribution | detection bypass | labelled dataset |
| Unicode letters/numbers before a key hid attribution | detection bypass | property-based fuzzing |
| Default-ignorable characters hid a key or alias | detection bypass | property-based fuzzing |
| Bulleted human trailers flagged | false positives | dataset false-positive probes |
| Quadratic JWT redaction (4.6 s on 80 KB) | denial of service | ReDoS suite |
| Quadratic workflow `secrets.` scan (2.7 s on 60 KB) | denial of service | ReDoS suite |
| Slow normalisation (10 MB message: 4.8 s) | denial of service | performance benchmark |
| Notifications never delivered under `github serve` | silent security-path failure | documentation review against code |
| `pip install commitguard` in the documentation | dependency confusion | Phase 10 review |
| 28 Windows failures | portability | CI matrix |

## Measured improvements

| Measure | Before | After |
|---|---|---|
| Detection throughput | 2,538 commits/s | **6,901 commits/s** |
| 10 MB commit message | 4,763 ms | **752 ms** |
| Known detection bypasses | 3 | **0** |
| Quadratic regular expressions reachable from untrusted input | 2 | **0** |
| Platforms with validated local enforcement | 0 | **1 measured, 2 pending** |

## What was built

| | |
|---|---|
| Python | 43,418 lines |
| Tests | 1,343 (350 security-marked), 19,634 lines |
| Dashboard | 15,361 lines of TypeScript/React |
| Documentation | 107 files, 17,190 lines |
| Labelled dataset | 9,174 cases, 4 immutable versions |
| Recorded benchmark runs | 18, including 4 published failures |
| Security experiments | 13 |

## Adoption

**None.** 1 contributor, 2 stars, 0 forks, 0 issues, 0 pull requests, 0 releases,
0 external evaluations, 0 external deployments (2026-09-17).

## The honest claim

This project demonstrates security engineering and measurement discipline: a
defence-in-depth design, an adversarial dataset that found real bypasses in its
own implementation, immutable evidence including failures, and documentation that
states its limits. It does **not** demonstrate adoption, independent validation or
production operation - and it says so in every document where the question arises.
