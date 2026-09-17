# 15 - Demonstrations

Eight things anyone can run. Times are from this machine.

## 1. Block a commit (2 minutes)

```bash
git init demo && cd demo
commitguard init --install-hooks
git commit --allow-empty -m "feat: x

Co-authored-by: Claude <noreply@anthropic.com>"
```
**Expected:** refused, exit 1, nothing committed, message preserved.

## 2. See the evidence

```bash
commitguard scan HEAD            # explains the finding, its source and remediation
commitguard scan --format json   # the same as a structured document
```

## 3. Watch a local bypass succeed - and be honest about it

```bash
git commit --no-verify --allow-empty -m "feat: x

Co-authored-by: Claude <noreply@anthropic.com>"
```
**Expected:** it works. Local hooks are advisory; the server-side check is the
control. `commitguard doctor` then reports the state of the hooks truthfully.

## 4. Try to disguise the attribution (the interesting one)

```bash
commitguard check --message-file examples/basic/commits/disguised-coauthor.txt
```
**Expected:** exit 1. Try your own: change the case, insert symbols, use full-width
characters, add a variation selector inside the key. Three of these were real
bypasses, found by the project's own fuzzing and fixed.

## 5. Reproduce the accuracy claim (about 10 seconds, no credentials)

```bash
commitguard reproduce benchmark --results benchmarks/results
```
**Expected:** the 9,174-case dataset rebuilds to the published fingerprint and
re-measures 0 false negatives, 0 false positives.

## 6. Run the security evidence (about 70 seconds)

```bash
commitguard reproduce security --evidence-dir evidence/security
commitguard report security --results benchmarks/results --evidence evidence/security
```
**Expected:** 350 tests pass; `reports/security-report.md` is rebuilt with every
line labelled Measured, Tested, Observed, Expected or Not tested.

## 7. Validate your own platform

```bash
commitguard benchmark platform
```
**Expected:** 15 checks, each with expected and observed values, in a directory
whose path contains spaces and non-ASCII characters. Sending this result is the
most useful contribution an external evaluator can make.

## 8. The dashboard

The demo stack replays a full lifecycle through the real services - scan, finding,
policy change, rollback, notification, merge queue - and the README screenshots
come from it. See the README's "Run the demo locally".

## Video

**PLANNED.** A 5-10 minute technical walkthrough (problem, architecture, local
enforcement, local bypass, GitHub enforcement, organization policy, dashboard,
benchmark evidence, security testing, limitations), using the real software rather
than slides. Outline:
[docs/presentations/commitguard-technical-overview.md](../docs/presentations/commitguard-technical-overview.md).
