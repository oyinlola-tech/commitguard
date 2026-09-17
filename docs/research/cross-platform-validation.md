# Cross-platform validation

Does local enforcement actually work on each operating system, rather than
"should work"? `commitguard benchmark platform` answers that by doing the real
thing: it creates a repository in a directory whose path contains **spaces and
non-ASCII characters**, installs the hooks, and tries to commit and push.

Every check states what was expected and what was observed. A check that cannot
run is `SKIPPED` with a reason, never a pass.

## Result: Linux

**Measured 2026-09-17**, Linux 7.1.5 (x86_64), Git 2.53.0, Python 3.13.15:
**15 checks, 15 passed, 0 failed, 0 skipped.**

| Area | Check | Observed |
|---|---|---|
| environment | git is available | git version 2.53.0 |
| hooks | install into `path with spaces/répertoire-ünïcøde` | all three installed |
| hooks | hook integrity (checksums) | all three report installed |
| pre-commit / commit-msg | clean commit allowed | exit 0, commit created |
| pre-commit / commit-msg | AI co-authored commit blocked | exit 1, no commit |
| bypass | `git commit --no-verify` | exit 0, commit created (documented limitation) |
| pre-push | clean push allowed | exit 0 |
| pre-push | AI co-authored push blocked | exit 1 |
| bypass | `git push --no-verify` | exit 0 (documented limitation) |
| cli | `scan` on a clean commit | exit 0 |
| cli | `scan` on an AI co-authored commit | exit 1 |
| configuration | CRLF line endings in `.commitguard.yaml` | exit 1 (parsed, still enforced) |
| configuration | invalid configuration fails closed | exit 2, never 0 |
| cli | `check --message-file` with spaces in the path | exit 1 |
| hooks | uninstall removes the hooks | hooks missing, AI commit no longer blocked |

## macOS and Windows

| Platform | Full test suite | Platform validation |
|---|---|---|
| Linux (ubuntu-latest) | **Passing** in CI | **15/15 measured** locally |
| macOS (macos-latest) | **Passing** in CI (run 35187790650) | **Pending** - the job is added, not yet run |
| Windows (windows-latest) | **Fixed locally, not yet verified in CI** | **Pending** |

Honest status: the full test suite runs on all three operating systems and two
Python versions in CI, and macOS has passed. Windows failed 28 tests, all of them
now fixed locally:

| Windows failure | Cause | Fix |
|---|---|---|
| Time zone rejected, including UTC | Windows has no system IANA time-zone database | `tzdata` as a Windows-only dependency |
| Mirror cleanup failed | Git's pack files are read-only; `shutil.rmtree` cannot delete them | clear the read-only flag and retry |
| ~20 tests reading files | no encoding given, so the ANSI code page was used | explicit UTF-8 |
| A permission assertion | POSIX-only file mode semantics | assert only on POSIX |
| A branch name test | Windows cannot store the name | POSIX-only portion |

These fixes need a push to verify in CI, which is the maintainer's call. Until
that run exists, this page says *pending* rather than claiming a green matrix.

The new `platform-validation` job in `.github/workflows/ci.yml` runs
`commitguard benchmark platform --json` on all three runners and uploads the
result, so the matrix above fills itself in from real runs rather than assertions.

## What is deliberately not claimed

- No claim of "works on every OS". Three are tested; others are untested.
- No claim about shells other than Git's `sh` (which Git for Windows provides).
- No claim about filesystems beyond the path handling exercised above.
