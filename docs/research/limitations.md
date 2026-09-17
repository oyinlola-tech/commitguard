# Limitations

What CommitGuard does not do, cannot do, or has not proven. This page is
deliberately the longest-lived document in the project: things get added here
before they get added to the feature list.

## 1. Metadata is not proof

CommitGuard reads what a commit **claims**. A commit with the attribution removed
is indistinguishable from a human's. Anyone who does not want to be detected only
has to not write the trailer - no detection improvement changes that.

This is a policy and provenance-record tool, not an authorship oracle. See
[roadmap.md](roadmap.md) for what a stronger guarantee would require.

## 2. Local hooks are advisory

`--no-verify`, deleting hooks, `core.hooksPath`, or a fresh clone all skip them,
and all are measured ([bypass-resistance.md](bypass-resistance.md)). Local hooks
are for fast feedback; the server-side check is the control.

## 3. Branch protection is outside CommitGuard

A failing check blocks a merge only when branch protection or a ruleset requires
that check. CommitGuard cannot configure it, and cannot verify it from a clone -
`doctor` reports it as unverifiable rather than guessing. Every merge-blocking
experiment uses a **model** of that gate, not github.com.

## 4. Accuracy is measured on a synthetic dataset

9,174 cases written to cover documented semantics and plausible evasions. It is
not a sample of real repositories, so it cannot say how often these cases occur in
practice, and the "0 false positives" figure is not a false-positive rate for your
history. Run `commitguard scan` over your own repository - that measurement is
worth more to you than this one.

## 5. The headline accuracy is post-fix

Three bypasses were found by these benchmarks and fixed; the current result is
measured after the fixes, on datasets that contain them. The failing runs are kept
and published. The correct reading is "no known bypass remains", not "no bypass
exists".

## 6. The author wrote both the software and its evaluation

No independent evaluation has been recorded. That is the single largest gap in
this evidence set. The process for external evaluation exists and is unused
([../community/external-evaluation.md](../community/external-evaluation.md)).

## 7. Cross-platform evidence is incomplete

Linux: 15/15 measured. macOS: full test suite passing in CI, platform validation
pending. Windows: 28 failures found and fixed locally, **not yet verified in CI**.
No claim of universal compatibility is made
([cross-platform-validation.md](cross-platform-validation.md)).

## 8. Hook overhead is about a second per commit

Measured: +947 ms per commit, +455 ms per push, almost entirely Python interpreter
start-up rather than analysis (which costs ~0.1 ms). On a busy repository this is
noticeable. It is a known, quantified usability problem
([performance-evaluation.md](performance-evaluation.md)).

## 9. The trade-off on quoted trailers

A quoted or bulleted line such as `> Co-authored-by: Claude <...>` is treated as a
trailer, because a symbol in front must not hide attribution. The consequence is
that quoting an AI trailer in a commit body - for example, pasting an e-mail
thread - can produce a finding. The reverse choice would reopen the first bypass.
Human trailers quoted this way are covered by dataset cases and do **not** produce
findings; an AI one does.

## 10. The App service is experimental and single-instance

SQLite, an in-process queue, and background threads in one process: one instance
per database, on one host. It has never been run against github.com in production
by anyone. No external production deployments have been recorded yet.

## 11. Not tested

- Real GitHub outages, rate limiting and API deprecations (modelled only).
- Long-running stability; there is no soak test.
- A disaster-recovery restore drill.
- Performance on Windows and macOS.
- Any deployment at organizational scale with real traffic.

## 12. Not implemented

- Signed releases, published SBOM, artifact checksums (the release workflow exists
  but no release has been published).
- Automated dependency updates.
- Cryptographic provenance or attestation verification.
- Any telemetry - deliberately, and with no plan to add it.
