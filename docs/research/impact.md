# Impact

Only what can be counted. No adoption is claimed, because none has been recorded.

## What exists (measured 2026-09-17)

| | Count |
|---|---|
| Python source | 43,418 lines |
| Tests | 19,634 lines, **1,343 tests**, of which **350** carry the `security` marker |
| Dashboard (TypeScript/React) | 15,361 lines |
| Documentation | 107 Markdown files, 17,190 lines |
| Commits | 94 |
| Detection rules | 15 AI agents, 6 automation accounts, as data |
| Labelled dataset | 9,174 cases across 4 immutable versions |
| Recorded benchmark runs | 13 detection, 2 performance, 1 hooks, 1 repository, 1 platform |
| Security experiments | 13, with recorded outcomes |
| Property-based examples per run | 7,476 across 25 properties |
| Regular expressions measured for ReDoS | 33, against 70 adversarial inputs each |

## Security defects found and fixed, by this project's own evidence

| Defect | Found by | Impact if unfixed |
|---|---|---|
| Symbol before a trailer key hid attribution | detection dataset 1.0.0 | detection bypass |
| Bulleted human trailers flagged as malformed | dataset 1.1.0 (false-positive probes) | false positives for ordinary commits |
| Unicode letters/numbers before a key hid attribution | property-based fuzzing | detection bypass |
| Default-ignorable characters hid a key or an alias | property-based fuzzing | detection bypass |
| Quadratic JWT redaction (4.6 s on 80 KB) | ReDoS tests | denial of service on log/audit paths |
| Quadratic workflow `secrets.` check (2.7 s on 60 KB) | ReDoS tests | denial of service from a monitored repository |
| Slow normalisation (4.8 s for a 10 MB message) | performance benchmark | denial of service in the commit path |
| Notifications never delivered under `github serve` | documentation review against code | silent failure of a security notification path |
| Documentation instructing `pip install commitguard` | Phase 10 review | dependency confusion: a different project entirely |
| 28 Windows test failures (time zones, read-only files, encodings) | CI matrix | unusable on Windows |

**Ten defects, eight of them security-relevant, found before any user encountered
them.** That is the measurable output of the benchmark and fuzzing work.

## Performance improvements, measured before and after

| Measure | Before | After | Change |
|---|---|---|---|
| 10,000-commit batch, p50 per commit | 0.3455 ms | 0.1162 ms | 3.0x faster |
| Throughput | 2,538 commits/s | 6,901 commits/s | 2.7x |
| 1 MB commit message, p50 | 531.2 ms | 74.9 ms | 7.1x |
| 10 MB commit message, p50 | 4,763 ms | 751.8 ms | 6.3x |
| Rule loading | 39.2 ms | 28.6 ms | 1.4x |

Both runs are published; the improvement is verifiable rather than asserted.

## Adoption

**No external production deployments have been recorded yet.**
**No external evaluations have been recorded yet.**
There is **1** contributor, **2** stars, **0** forks, **0** issues, **0** pull
requests and **0** releases (GitHub, 2026-09-17; the repository became public on
2026-09-14).

These numbers are here because the alternative - implying adoption that does not
exist - would make every other number in this project worth less. The mechanisms
for recording real external evidence exist
([../community/external-evaluation.md](../community/external-evaluation.md),
[../community/pilot-program.md](../community/pilot-program.md),
`evidence/external-validation/`), and they are empty.

## What would change these numbers

| Evidence | How it becomes real |
|---|---|
| Cross-platform matrix | the CI platform job runs on macOS and Windows |
| External evaluation | one engineer follows the quick start and reports what happened |
| Real deployment | one repository runs the check for a month, with metrics |
| Independent security review | the review guide exists for exactly this |
| Reproduction by a third party | `commitguard reproduce all` on someone else's machine |
