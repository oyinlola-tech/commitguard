# 17 - Limitations

The full register is [docs/research/limitations.md](../docs/research/limitations.md).
The ones an assessor should weigh:

## 1. Metadata is not proof

CommitGuard enforces what a commit **claims**. Remove the trailer and the commit is
indistinguishable from a human's. This is a contribution-policy and provenance
tool, not an authorship oracle, and no amount of detection work changes that.

## 2. No external validation

The author wrote the software, the dataset, the benchmarks and this package. **No
independent evaluation, review, reproduction or deployment exists.** The
reproduction path is one command and needs no credentials - but until someone runs
it and says so, every number here is self-reported.

## 3. Headline accuracy is measured after the fixes

"0 false negatives, 0 false positives" is true of the current code on the current
dataset, **after** three bypasses these benchmarks found were fixed. The correct
reading is "no known bypass remains". The failing runs are published so this is
verifiable rather than merely admitted.

## 4. Local hooks are bypassable

Seven ways, measured. They are for fast feedback, not control.

## 5. Branch protection is outside the tool

A failing check blocks a merge only when branch protection requires it.
CommitGuard cannot configure or verify that, and the merge-blocking experiments use
a **model** of GitHub's gate rather than github.com.

## 6. Cross-platform evidence is incomplete

Linux measured (15/15). macOS: test suite passing, platform validation pending.
Windows: 28 failures found and fixed, **not yet verified in CI**.

## 7. Hook overhead is about a second per commit

Measured: +947 ms per commit, almost all Python interpreter start-up. A real
usability cost, quantified rather than hidden.

## 8. The App service is experimental and single-instance

SQLite and an in-process queue: one instance per database, never run against
github.com in production.

## 9. The dataset is synthetic

9,174 cases written to cover documented semantics and plausible evasions - not
sampled from real repositories, so it cannot estimate real-world frequencies.

## 10. Three days old

Built between 2026-09-14 and 2026-09-17. It has depth of engineering and
measurement; it has no operational history, no users and no maturity in the sense
of time.
