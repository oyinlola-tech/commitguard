# Phase 10 verification report: does CommitGuard actually work?

CommitGuard 0.1.0.dev0 · 2026-09-18 · Linux 7.1.5 (x86_64), Intel i5-8350U,
8 CPUs, 16 GB, Python 3.13.15, Git 2.53.0

This is a **repair and verification** pass, not a feature phase. Nothing here was
accepted because a file exists, a function is defined or a test is green. Every
claim below was checked in at least one of three ways, and the way is stated:
**code** (read), **tests** (run), **execution** (the real command, the real Git
repository, the real database).

For the earlier, separate Phase 10 work on adoption and reproducibility, see
[phase-10-report.md](phase-10-report.md).

---

## 1. Executive summary

CommitGuard's core contract holds. A developer can install it, commit normally,
have prohibited attribution blocked locally, bypass the hook deliberately, and
have the server-side check catch the bypass. That was verified by running it,
not by reading about it.

The pass found **four real defects** and removed one piece of dead code:

| # | Defect | Severity | Found by |
|---|---|---|---|
| 1 | The Git hook's fail-closed message escaped newlines, so the most common developer-facing error (an invalid `.commitguard.yaml`) printed as one unreadable `\x0a`-run | Medium (usability of a security message) | execution |
| 2 | `installation_repositories`, `known_repositories` and `installation_sync_status` were never deleted when a GitHub App installation was purged: an organisation's repository names stayed in the database forever, contradicting the documented retention promise | Medium (data retention) | execution |
| 3 | The dogfooding workflow fell back to installing the scanner from `$GITHUB_WORKSPACE` - the change under review - when the trusted commit looked too old | Medium (CI trust boundary) | code |
| 4 | `.env.example` said "CommitGuard does not read any environment variables yet". It reads 30, including every GitHub App credential | Medium (deployment documentation) | code |

Five further suspicions were investigated and **cleared** - they were correct
behaviour or errors in how this report's author probed them. They are listed in
[section 14](#14-remaining-problems) because a verification pass that reports
only confirmed bugs is hiding half its work.

Final state: **1,397 tests pass, 1 skipped** (1,398 collected), 354 of them
security tests;
`ruff`, `ruff format`, `mypy --strict` and `bandit -ll` clean (0 medium, 0 high);
web dashboard typecheck, lint, 99 tests and production build all pass; detection
**0 false negatives and 0 false positives over 9,174 labelled cases**; Linux
platform validation 15/15.

**Status: READY WITH KNOWN LIMITATIONS.** The limitations are in
[section 14](#14-remaining-problems); the most important is that server-side
enforcement has never been exercised against real github.com from this machine.

---

## 2. Original product contract

CommitGuard inspects Git commits and decides whether they violate a configured
security or contribution policy. Its first use case is prohibited AI or
coding-agent attribution, for example:

```
Co-authored-by: Claude <noreply@anthropic.com>
```

The enforcement model has two layers, and they are not equal:

```
local hooks   ->  early prevention, convenient, bypassable by design
GitHub check  ->  authoritative, because the developer does not control the runner
```

A failing check stops a merge only when branch protection or a ruleset requires
that check. CommitGuard does not configure that and says so.

The invariant that outranks everything else: **CommitGuard must never report
that a check passed when the check did not actually run to completion.**

---

## 3. Phase 1-9 audit

| Phase | Responsibility | Current implementation | Problems found now | Status |
|---|---|---|---|---|
| 1 | CLI skeleton, config, exit codes | `scan`, `check`, `init`, `install`, `doctor`, `policy list`; exit 0/1/2 | `.env.example` stale from this phase (defect 4); one dead helper | **fixed** |
| 2 | AI attribution detection | trailer parsing without regex, Unicode normalisation, identity rules | none | **verified** |
| 3 | Git hooks | `pre-commit`, `commit-msg`, `pre-push`, managed block, checksums, chaining | fail-closed message unreadable (defect 1) | **fixed** |
| 4 | GitHub Actions enforcement | `ci github`, composite `action.yml`, trusted policy source | untrusted install fallback in the repo's own workflow (defect 3) | **fixed** |
| 5 | GitHub App | webhooks, Check Runs, worker, SQLite state | none | **verified** |
| 6 | Dashboard and API | React/Vite UI, framework-free WSGI API | none | **verified** |
| 7 | Notifications, re-runs, merge queue | outbox, dedup, retry, merge group validation | none | **verified** |
| 8 | Organization governance | groups, inheritance, mandatory floor, exceptions, rollouts | none | **verified** |
| 9 | Security intelligence, compliance, benchmarks | violations, posture, reports, immutable results | orphaned tenant rows after purge (defect 2) | **fixed** |

"Verified" means the phase's behaviour was exercised and matched its contract,
not that it is feature-complete. What is deliberately absent is in
[section 14](#14-remaining-problems).

---

## 4. Core detection

**Verified by execution.** A 28-case matrix was run directly against
`Analyzer.create(default_policy_set())`, covering the must-detect and must-not-detect
lists in the specification:

| Group | Cases | Result |
|---|---|---|
| Must block: agent trailers (Claude, ChatGPT, Cursor, GitHub Copilot, Claude Code, Devin) | 6 | 6 block |
| Must block: key casing, whitespace padding, tabs | 4 | 4 block |
| Must block: AI author identity, AI committer identity | 2 | 2 block |
| Must block: duplicate trailers, mixed trailers, `Generated-with` marker | 3 | 3 block |
| Must block: Cyrillic homoglyph key, zero-width inside the agent name | 2 | 2 block |
| Must allow: prose about AI, prose naming an agent, human co-author, unrelated email, `Signed-off-by`, similar human name, a trailer quoted in the body, a minimal message, 240 KB message, non-Latin prose | 10 | 10 allow |
| Investigated: `Co-authored-by Claude ...` with no colon | 1 | blocks - **intended**, see below |

The colon-less case is deliberate: a `*-by` key written without a colon is an
evasion shape, documented in `src/commitguard/provenance/trailers.py` and in
`docs/security/threat-model.md`. The initial expectation in this pass was wrong,
not the code.

**Verified by measurement.** The full labelled dataset, recorded to
`benchmarks/results/raw/detection/20260918T071254882265Z_0.1.0.dev0.json`:

```
Dataset               1.3.0 (9174 cases: 3709 must not be allowed, 5465 must be allowed)
False negatives       0
False positives       0
Precision             100.000%
Recall                100.000%
Exact decision        9174/9174
```

By class: adversarial 74, clean 43, generated 9000, malformed 9, variations 21,
violations 27 - every one an exact decision match.

**Detection limitations** (unchanged, and honest): CommitGuard reads commit
metadata. It cannot tell whether a human actually wrote the code, only whether
the commit claims otherwise. A developer who never adds attribution is not
detected, by construction. See `docs/research/limitations.md`.

---

## 5. Local Git enforcement

**Verified by execution in real repositories with real bare remotes.**

| Scenario | Result |
|---|---|
| Clean commit | allowed, exit 0 |
| AI-attributed commit | blocked at `commit-msg`, exit 1, nothing committed, message preserved in `COMMIT_EDITMSG` |
| Prose "Updated documentation about AI-assisted development" | allowed |
| Human `Co-authored-by` | allowed |
| `git commit --no-verify` | commit created (the documented bypass) |
| Push of 6 commits with one bypassed violation in the middle | **push blocked**, all 6 scanned, the violating one named |
| Force push of an amended violating tip | blocked, 1 commit scanned |
| Tag pointing at a violating commit | blocked |
| Tag on a clean commit, branch deletion, up-to-date push | allowed, "no new commits to check" |
| New branch push | full history scanned, not just HEAD |

**Git range calculation is correct.** A new branch scans the commits the remote
does not have; an existing branch scans `remote..local`; a deletion and a
no-op push scan nothing.

**Hook installation.** Installing over a pre-existing `pre-commit` preserved it
as `pre-commit.pre-commitguard` and chained it after CommitGuard. Installing
twice produced a byte-identical file. `uninstall` restored the original hook
exactly; a second `uninstall` reported "not installed" and left the restored
hook untouched. The managed block carries `BEGIN/END COMMITGUARD` markers and a
SHA-256 checksum.

**Tamper behaviour, checked both ways.** Appending `exit 0` *after* the managed
block does **not** bypass anything (the block exits first) and `doctor` correctly
stays silent. Editing `commitguard_run "$@" || exit $?` to `|| true` *inside* the
block **does** bypass the hook - and `doctor` reports
`! WARNING commit-msg hook appears to have been modified`. That is the local-hook
limitation working as documented, and it is exactly why the server-side check
exists.

**Fail-closed.** Invalid YAML, an unknown field and an unknown policy id each
blocked the commit with exit 2 and the message "Commit blocked because the
security check could not be completed". No configuration error ever produced a
pass.

---

## 6. GitHub enforcement

**Actions.** `.github/workflows/commitguard.yml` and `action.yml` were read line
by line. Both are `permissions: contents: read`, use no secrets, set
`persist-credentials: false`, pin every third-party action to a 40-character
commit SHA, install dependencies with `--require-hashes`, and pass all GitHub
context values through `env:` rather than interpolating them into shell bodies
(so a branch name or PR title cannot inject script). `pull_request_target` is
rejected by the event parser. Pull requests are scanned as `base..head`, never
the single head commit, and never GitHub's synthetic merge commit.

**Fixed here (defect 3).** The repository's own workflow contained a bootstrap
fallback: if the trusted commit lacked `requirements/ci.txt`, it installed
CommitGuard from `$GITHUB_WORKSPACE` - the change being checked. 16 of the
repository's 103 commits predate that file, so anyone able to create a branch at
one of them could have opened a pull request whose base triggered the fallback
and supplied its own scanner. The fallback is gone; the check now fails with an
explicit error. Enforced by
`test_the_enforcement_workflow_never_installs_the_scanner_from_the_change`.

**App.** Verified by the integration suite against a fake GitHub and a real
SQLite store: webhook HMAC verification, forged and tampered payload rejection,
replayed and out-of-order deliveries, installation suspension and removal,
down-scoped installation tokens, Check Run slot ownership (a newer job wins; an
older slower scan cannot overwrite a newer result), merge group validation, and
worker restart recovery.

**Never a false pass.** `test_github_unavailable_and_revoked_permissions_never_pass`
drops the GitHub API mid-scan (`TransportError`) and separately reduces the
Checks permission to read. In both cases the job ends in `ERROR` and **no
success conclusion is ever written**. `test_database_unavailable_fails_closed`
does the same for storage. The availability cost is recorded honestly: while
GitHub or the service is down, a required check stays unsatisfied and merges
wait.

**Branch protection.** `commitguard github setup` only *reports*; it never
configures protection. The App's `EnforcementProbe` reports `required`,
`not_required` or `unknown` from two read-only endpoints and returns `unknown`
whenever the answer is not visible with the App's permissions - it never guesses
`not_required`. The README's claim was tightened in this pass to say exactly
that.

**The authoritative test (specification section 21).**
`tests/integration/github/test_defense_in_depth.py` runs the whole chain with a
real repository and a real bare remote: clean commit passes locally and in CI ->
AI commit blocked locally -> `--no-verify` bypass reaches the remote -> the
pull-request check **fails** -> the modelled required-check gate refuses the
merge -> the developer amends -> the check **passes** -> the merge proceeds.
Only GitHub's branch-protection gate is modelled, and the file says so in its
first paragraph.

---

## 7. Organization governance

**Verified by execution** for the property that matters most - a repository
cannot weaken a mandatory organisation policy:

| Repository configuration | Effective action under a mandatory `ai_coauthor: block` |
|---|---|
| `action: allow` | block |
| `action: warn` | block |
| `enabled: false` | block |
| `enabled: false` + `action: allow` | block |
| silent | block |

A repository may still be *stricter* than the floor. A mandatory policy that
tries to weaken is rejected outright: `enabled: false` and any `enforcement:`
block raise a configuration error rather than being silently ignored.

The rest of governance - groups, inheritance, immutable policy versions,
rollback as a new audited version, scoped and expiring exceptions, staged
rollouts, bulk operations, simulation, schedules - is covered by the
`tests/integration/github/app/dashboard/test_governance_*.py` suites, all
passing.

---

## 8. Security intelligence

Only what exists: violations and findings with immutable evidence, security
posture per repository and organisation, finding trends, notification events and
acknowledgements, compliance reports with CSV export, and retention. Signed
commit verification, secret detection in changed content and provenance analysis
are **not implemented**; `src/commitguard/provenance/signatures.py` and
`src/commitguard/git/diff.py` hold the planned model shapes, are imported by
nothing, and are listed as placeholders in `ROADMAP.md`. They were kept for that
reason, not overlooked.

---

## 9. Security review

**Checked by execution:**

- *Unsafe YAML*: 7 of 7 hostile documents rejected - `!!python/object/apply`,
  `!!python/name`, billion-laughs expansion, plain aliases, merge keys,
  duplicate keys, unhashable keys - while a valid configuration still loads.
- *Secret leakage*: 6 of 6 credential shapes redacted (GitHub installation
  token, PAT, PEM private key, `Authorization` header, JWT, webhook signature),
  and 9 of 9 innocuous strings left intact (URL, email, Git SHA, UUID, SHA-256
  digest, environment variable name, ordinary prose, version string, dotted
  module path). `Secret` renders as `Secret('**********')` in `repr()` and in
  f-strings.
- *Log and terminal injection*: a configuration file containing
  `nope\n\nResult: PASS` cannot produce a line starting at column 0 - the fix
  for defect 1 indents every continuation line.

**Checked by code review:**

- No `shell=True` anywhere in `src/` (an architecture test forbids it), no
  `os.system`, `os.popen`, `eval`, `exec` or `pickle`.
- Subprocesses use argument arrays; Git revisions pass `validate_revision`
  (rejects leading `-`, control characters, over-length) and the Git wrapper
  adds `--end-of-options`.
- No credentials, private keys or tokens in tracked files.
- Cross-tenant access: `test_other_tenants_get_not_found_for_every_governance_route`
  enumerates every governance route from the live route table and asserts a
  user from another organisation receives 404 with no `data` - and asserts the
  enumerated route count equals the table's, so a new route cannot quietly
  escape the test.
- ReDoS: the security suite times every module-level pattern against adversarial
  inputs under a fixed budget.

`bandit -ll` reports 0 medium and 0 high across `src/`. The 26 low findings are
`assert` and `subprocess` usages already reviewed and annotated.

**Fixed here:** defects 1, 2 and 3 above. Defect 2 is the one with privacy
weight: the store's own docstring promised that data which can no longer be
attributed to a tenant is removed, and three tables did not honour it.

---

## 10. Cross platform

| Platform | Status | Evidence |
|---|---|---|
| Linux (7.1.5, x86_64) | **PASS 15 / FAIL 0** | `benchmarks/results/raw/platform/20260918T071339020561Z_0.1.0.dev0.json` |
| macOS | **not verified in this pass** | needs the CI matrix; no macOS machine here |
| Windows | **not verified in this pass** | as above |

The Linux run covers installation into a path containing spaces and non-ASCII
characters, hook integrity, clean and blocked commits, blocked pushes, both
`--no-verify` bypasses, `scan`, `check --message-file`, a CRLF configuration
file, fail-closed on invalid configuration, and uninstallation.

Portability was also checked statically: no `/usr/bin` or `/bin/bash`
assumptions in `src/`; hooks use `#!/bin/sh`, which Git for Windows provides;
`tzdata` is declared as a Windows-only dependency because Windows has no system
IANA database.

---

## 11. Testing

```
1397 passed, 1 skipped in 210.20s
```

The single skip is the network test, which needs `COMMITGUARD_NETWORK_TESTS=1`.

| Suite | Count |
|---|---|
| Security (`-m security`) | 354 |
| Integration (`-m integration`) | 425 |
| Total Python (collected) | 1,398 |
| Web dashboard (vitest) | 99 |

Static gates, all clean: `ruff check`, `ruff format --check` (465 files),
`mypy --strict` (187 source files), `bandit -ll` (0 medium, 0 high), web
`tsc -b`, `eslint`, `vite build`.

**Tests added in this pass: 12.**

| Test | Guards |
|---|---|
| `test_block_keeps_newlines_so_a_multi_line_reason_stays_readable` | defect 1 |
| `test_block_indents_continuation_lines_so_none_can_impersonate_our_own` | line forging |
| `test_block_still_escapes_control_characters_and_truncates` | defect 1 |
| `test_block_leaves_single_line_text_alone` | defect 1 |
| `test_a_multi_line_failure_reason_is_readable_and_cannot_forge_a_status_line` | defect 1, end to end through a real hook |
| `test_purging_a_removed_installation_leaves_no_row_behind` | defect 2 |
| `test_every_tenant_table_has_a_documented_cleanup_path` | defect 2, for every future table |
| `test_the_enforcement_workflow_never_installs_the_scanner_from_the_change` | defect 3 |
| `test_the_example_file_was_found_and_is_not_empty` | defect 4 |
| `test_every_variable_the_code_reads_is_documented` | defect 4 |
| `test_no_variable_is_documented_that_nothing_reads` | defect 4 |
| `test_no_secret_carries_a_value` | committed secrets |

Two of these are *guards* rather than cases: they derive their expectations from
the live schema and the live source, so the next table or environment variable
added has to account for itself.

**Test integrity.** No test was weakened or deleted in this pass. The suites
that matter most - bypass resistance, server enforcement experiments,
defence in depth, tenant isolation - use real Git repositories, a real SQLite
store and a fake GitHub transport, not stubbed decisions.

---

## 12. Performance

Recorded to `benchmarks/results/raw/performance/20260918T071333108788Z_0.1.0.dev0.json`:

| Commits | wall ms | commits/s | p50 ms | p99 ms | peak alloc |
|---|---|---|---|---|---|
| 1 | 0.29 | 3,509 | 0.2817 | 0.2817 | 5 KiB |
| 10 | 1.56 | 6,431 | 0.1369 | 0.2512 | 8 KiB |
| 100 | 18.3 | 5,451 | 0.1423 | 0.3481 | 25 KiB |
| 1,000 | 203 | 4,925 | 0.1645 | 0.4581 | 93 KiB |
| 10,000 | 2,031 | 4,923 | 0.2063 | 0.4872 | 187 KiB |

Message-size scaling stays linear to 10 MB (1 KB 0.26 ms, 10 KB 0.78 ms,
100 KB 7.6 ms, 1 MB 75 ms, 10 MB 808 ms). Peak RSS for the whole run: 82.8 MiB.
Rule loading: 25-40 ms depending on machine state.

**A flagged regression that was not one.** `commitguard benchmark compare`
reported latency p50 +36% and throughput -30% against the run recorded the
previous evening. None of this pass's changes touch the detection path, so the
claim was tested rather than explained away: the baseline commit
(`f680f18`) was checked out into a worktree and benchmarked **alternately** with
the current code on this machine, five pairs each.

```
baseline f680f18  mean p50 0.2025 ms  (0.1852 - 0.2195)
current  HEAD     mean p50 0.1971 ms  (0.1789 - 0.2168)
difference        -2.7%
```

The current code is not slower. The recorded difference is machine state between
sessions, and it exposes a genuine limitation of the benchmarking method:
`compare` uses a 20% threshold against a result recorded at another time, so
cross-session drift on a laptop can exceed it. Correctness metrics, which have a
0% threshold, were unchanged across all runs - and those are the ones that
matter. This limitation is worth recording in
`docs/maintainers/benchmarking.md`.

---

## 13. Documentation

| File | Change |
|---|---|
| `.env.example` | rewritten: was "CommitGuard does not read any environment variables yet" and named a `GITHUB_TOKEN` nothing reads; now lists all 30 variables the code reads, grouped, with the `*_FILE` variants preferred |
| `README.md` | branch-protection note made precise (the CLI cannot see protection; the App reports what GitHub's read-only endpoints show and says `unknown` otherwise); `ai_trailer` row no longer uses a pictographic character |
| `docs/git-hooks.md`, `docs/configuration.md`, `docs/deployment/troubleshooting.md` | quoted `doctor` output corrected to the symbols `doctor` actually prints |
| `src/commitguard/cli/output.py`, `src/commitguard/detectors/trailer.py` | module docstrings corrected to describe current behaviour |

All 187 Markdown files were link-checked: **0 broken relative links**. All 34
documented `commitguard` commands resolve (enforced by
`tests/unit/test_documented_commands.py`).

---

## 14. Remaining problems

### Blocking

None. No core requirement is broken.

### Non-blocking

1. **macOS and Windows are unverified in this pass.** The CI matrix covers them;
   it needs a push to GitHub, which was not performed. Until then, cross-platform
   status is Linux-only evidence plus static checks.
2. **Server-side enforcement has never run against real github.com from here.**
   The App and Action suites run against a fake GitHub transport, and
   branch protection is a model in `test_defense_in_depth.py`. The model
   reproduces GitHub's documented rule and uses the real exit code of the real
   check, but it does not prove any repository is configured correctly.
3. **`commitguard benchmark compare` is sensitive to cross-session machine
   drift** on performance metrics (section 12). Correctness metrics are not
   affected.
4. **Commits were created in this working tree by tooling outside this session**
   (authored as the repository's Git user at 08:09, carrying this pass's
   changes). They were left untouched. Worth confirming that auto-commit is
   intended, because it commits to `main` directly.

### Known limitations (by design)

- Local hooks are bypassable with `--no-verify`, by editing the hook, or by
  pointing `core.hooksPath` elsewhere. This is inherent to client-side hooks.
  `doctor` detects the first two; the server-side check is the answer to all
  three.
- A failing check blocks a merge only when branch protection requires it, and
  CommitGuard cannot configure that.
- CommitGuard reads commit metadata. It cannot determine authorship, only what
  the commit claims.
- Several tenant tables are cleaned up only when the *installation* is purged;
  retention is time-based, not immediate.

### Future improvements

Signed commit verification, secret detection in changed content, author and
committer relationship analysis, SARIF output, pull-request comments. All are
placeholders or roadmap entries, none are claimed as working.

### Investigated and cleared

A verification pass that lists only confirmed defects hides its false starts.
Five suspicions were raised and dismissed with evidence:

| Suspicion | Verdict |
|---|---|
| `Co-authored-by Claude ...` without a colon is a false positive | Intended and documented evasion handling |
| Appending `exit 0` to a hook bypasses enforcement without `doctor` noticing | It does not bypass; the managed block exits first, so `doctor` was right to stay silent |
| `enabled: false` in a repository config defeats a mandatory organisation floor | It does not; the first probe used `build_policy_set` instead of `apply_mandatory_policies` |
| `benchmark compare` returns exit 0 on an error | It returns 2; the probe was reading `tail`'s exit status through a pipe |
| Detection latency regressed 36% | It did not; controlled A/B shows -2.7% (section 12) |

### Emoji

At the maintainer's request, pictographic characters were removed from
everything CommitGuard writes - Check Run markdown, GitHub Actions summaries,
CLI status lines - and from documentation prose. They remain in exactly five
places, all of which are **detection inputs, not CommitGuard's voice**:
`src/commitguard/research/datasets.py`, two test fixtures,
`examples/basic/commits/ai-tool-footer.txt` and one immutable recorded benchmark
result. Removing them would delete adversarial coverage and invalidate a
recorded dataset fingerprint.

---

## 13a. Added after verification: opt-in automatic removal

The verification pass above deliberately changed no behaviour. One feature was
added afterwards, at the maintainer's request, because the product's first
purpose is to stop AI, bot and agent co-authorship reaching GitHub - and
blocking was the only response available.

```yaml
remediation:
  auto_remove: false   # default
```

With it on, the `commit-msg` hook deletes the offending lines from the pending
message, reports exactly what it removed, and lets the commit proceed. It
rewrites no history: `commit-msg` runs before Git creates the commit object.

It is off by default, and it refuses every case it cannot provably fix:

| Situation | Behaviour |
|---|---|
| `Co-authored-by:` naming an AI agent, or a tool footer | line deleted, commit proceeds |
| A human `Co-authored-by:` beside an AI one | only the AI line is deleted |
| AI agent as commit **author** or **committer** | still blocks - no text edit fixes an identity |
| An identity finding *and* a removable trailer | still blocks |
| Message that is nothing but the trailer | still blocks rather than commit an empty message |
| A detector failed | still blocks - a message not fully analysed is never "fixed" |
| `pre-push` | unchanged; correcting pushed commits means rewriting history |
| A mandatory organization policy setting it | rejected as a configuration error |

The decisive safeguard: the stripped message is **re-analysed**, and the commit
proceeds only if the result is clean. Nothing is allowed on the assumption that
the removal worked.

Verified by execution through real `git commit`, including the editor path with
`git commit -v`: body text and human co-authors survive, Git's comment block
never leaks into the stored message, and the resulting commit passes `pre-push`
afterwards - the two layers agree. `commitguard doctor` reports
`! WARNING  remediation.auto_remove is on` whenever it is enabled, because it
turns a block into a commit.

28 tests cover it: 15 unit tests for the decision and stripping logic, 7
integration tests through real Git, 4 configuration tests, 1 mandatory-policy
test, and 1 doctor test.

---

## 14a. Validation matrix

"Evidence" names how the status was established. No row is PASS without one.

| Area | Status | Evidence |
|---|---|---|
| Core detection | PASS | 28-case matrix run against the engine; 9,174-case dataset, 0 FN / 0 FP |
| AI attribution detection | PASS | same, plus `tests/unit/detectors/`, `tests/security/test_fuzz_parsers.py` |
| Policy engine | PASS | mandatory-floor matrix run directly; `tests/unit/policies/` |
| CLI | PASS | every command executed; exit codes 0/1/2 confirmed, including outside a repository |
| Git hooks | PASS | real repository: clean, blocked, amend, chained hook, install/uninstall idempotency |
| Pre-push | PASS | real bare remote: new branch, 6-commit range, force push, tag, deletion, up-to-date |
| Cross platform | PARTIAL | Linux 15/15 recorded; macOS and Windows need the CI matrix (unpushed) |
| GitHub Actions | PASS | workflow and `action.yml` read in full; `tests/unit/github/test_workflows_static.py` (40 tests) |
| GitHub App | PASS | `tests/integration/github/app/` against a fake GitHub and a real store |
| GitHub Checks | PASS | `test_app_end_to_end.py`, `test_policy_and_checks.py`, slot-ownership tests |
| Branch enforcement | PARTIAL | `test_defense_in_depth.py` end to end; GitHub's gate is modelled, not real |
| Findings | PASS | `tests/integration/github/app/dashboard/`; immutable evidence and policy version |
| Evidence | PASS | as above, plus redaction verified by execution (6 redacted, 9 left intact) |
| Organization governance | PASS | `test_governance_*.py` suites; mandatory floor verified by execution |
| Notifications | PASS | `tests/unit/notifications/`, `test_dashboard_notifications.py` |
| Dashboard | PASS | 99 vitest tests, `tsc -b`, `eslint`, production build; API integration suites |
| Security intelligence | PASS | dashboard and posture suites; scope stated in section 8 |
| Compliance | PARTIAL | reports and CSV export tested; no external control framework is claimed |
| Authorization | PASS | `test_dashboard_authorization.py` (17), `test_governance_isolation.py` (3) |
| Tenant isolation | PASS | every governance route enumerated; cross-tenant access returns 404 with no data |
| Database | PASS | `integrity_check` ok, `foreign_key_check` clean, v1 to v4 upgrade with data, newer schema refused |
| Queue | PASS | `test_app_concurrency.py`, worker restart recovery, per-slot ownership |
| Security audit | PASS | section 9; `bandit -ll` 0 medium / 0 high; 3 defects found and fixed |
| Documentation | PASS | 0 broken links across 187 files; 34 documented commands all resolve; 4 inaccuracies fixed |
| Full E2E | PASS | journey re-run after every change; `test_defense_in_depth.py` green |

---

## 15. Final product status

**READY WITH KNOWN LIMITATIONS.**

The question the phase had to answer:

> Can a developer install CommitGuard, make a prohibited commit, have it detected
> and blocked locally, bypass the local hook intentionally, have the GitHub
> enforcement layer detect the violation, have the required GitHub Check prevent
> the merge, fix the violation, pass the check, and then merge successfully?

Every step was executed and observed. Steps 1-5 and 10-12 ran against real Git
repositories and real remotes in this pass. Steps 6-9 ran in
`test_defense_in_depth.py` with a real repository, a real bare remote and the
real scanner - with GitHub's branch-protection gate modelled from its documented
rule, using the real exit code of the real check.

So the honest answer is: **yes, with one modelled link.** The chain is proven
end to end against everything that can run offline. What remains unproven is
that a particular GitHub repository is configured to require the check - which
CommitGuard reports on but deliberately does not control.
