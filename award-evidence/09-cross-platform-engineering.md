# 09 - Cross-platform engineering

| Platform | Test suite | Local enforcement validation |
|---|---|---|
| Linux | **TESTED** - full suite in CI | **MEASURED** - 15/15 checks |
| macOS | **TESTED** - full suite passing in CI (run 35187790650) | **PLANNED** - job added, not yet run |
| Windows | **IMPLEMENTED** - 28 failures found and fixed; **not yet verified in CI** | **PLANNED** |

No claim of universal compatibility is made.

## How local enforcement is validated

`commitguard benchmark platform` does the real thing rather than asserting it: it
creates a repository in a directory whose path contains **spaces and non-ASCII
characters** (`path with spaces/répertoire-ünïcøde`), installs the hooks, and then
commits, pushes, bypasses, scans, misconfigures and uninstalls - 15 checks, each
recording expected and observed values. A check that cannot run is `SKIPPED` with
a reason, never a pass.

Linux result: **15 passed, 0 failed, 0 skipped**, including that invalid
configuration exits 2 (fails closed) and that CRLF line endings parse correctly.

## Windows defects found by the CI matrix

Real portability problems, each with a real fix:

| Defect | Cause | Fix |
|---|---|---|
| Every time zone rejected, including UTC | Windows ships no IANA time-zone database | `tzdata` as a Windows-only dependency |
| Repository mirror cleanup failed | Git's pack files are read-only; `shutil.rmtree` cannot remove them | clear the read-only flag and retry |
| ~20 tests reading files | no encoding specified, so the ANSI code page was used | explicit UTF-8 |
| A file-permission assertion | POSIX-only semantics | assert only on POSIX |
| A branch-name test | Windows cannot store the name | POSIX-only portion |

## Portability engineering in the design

- Hooks run under Git's `sh` on every platform (Git for Windows provides one), so
  the hook scripts are identical.
- Paths are handled as `Path` objects throughout; the platform benchmark
  deliberately uses a hostile path.
- The CI matrix covers three operating systems and two Python versions, and the
  test shell is `bash` everywhere so hooks are exercised as they run in practice.
- A new `platform-validation` CI job runs the platform benchmark on all three
  runners and uploads the JSON, so the matrix above will fill itself in from real
  runs rather than from assertions.
