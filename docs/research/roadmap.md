# Research roadmap

Open directions, with the honest status of each. Nothing here is promised, and
nothing here should be implemented merely because it appears on a roadmap.

## The distinction that drives all of it

CommitGuard detects **metadata**. It does not prove **how code was produced**.

```text
Co-authored-by: Example Agent <agent@example.com>
```

is detectable, checkable and enforceable. It is not evidence that an agent wrote
any particular line, and its absence is not evidence that none did. Everything
below is about whether that gap can be narrowed, and at what cost.

## 1. Commit attestations (research)

A signed statement about how a commit was produced, made at the time it was
produced - by the agent, the editor, or the CI system - and verified later.

- **What it would give:** an unforgeable *positive* claim ("this commit was
  produced with tool X, attested by key Y"), rather than an easily removed
  trailer.
- **What it would not give:** anything about a commit with no attestation.
  Absence stays unprovable.
- **Prior art to build on:** Sigstore, in-toto, SLSA provenance for builds.
- **Status:** not implemented, not prototyped.

## 2. Verified developer identity (research)

Signed commits (SSH or GPG) bind a commit to a key. Combined with attribution
policy, that could distinguish "a human asserted this" from "an unauthenticated
trailer claims this".

- **Open question:** what does a policy do with an *unsigned* commit? Requiring
  signatures is a large process change for most teams.
- **Status:** not implemented. CommitGuard reads whether a signature exists but
  does not verify one.

## 3. Policy verification (research)

Today, properties like "a mandatory policy can only make enforcement stricter" are
tested over generated inputs. They could be *proved* for the resolver, which is
small and pure.

- **Status:** property-based testing today; formal verification not attempted.

## 4. Large-scale repository analysis (research)

How common is AI attribution in public repositories, how is it written, and how
do the conventions differ between agents? This would replace a synthetic dataset
with a sampled one, and give the false-positive question a real denominator.

- **Constraint:** it must not become a way to profile individual developers.
  Aggregate counts only, no identity-level publication.
- **Status:** not started.

## 5. Distributed and incremental scanning (engineering)

Scanning 100,000 commits takes 44 seconds today. Incremental scanning with a
persistent result cache keyed on commit SHA would make history-wide policy changes
cheap.

- **Status:** not implemented; the measurement that would justify it exists.

## 6. Hook start-up cost (engineering, known problem)

Nearly a second per commit, essentially all Python interpreter start-up. Options:
a single hook invocation per commit, a lazier import graph, or a resident helper
process (which brings its own security questions).

- **Status:** measured and documented; not yet addressed.

## 7. Privacy-preserving telemetry (deliberately not done)

CommitGuard collects nothing. If usage data were ever wanted, it would have to be
opt-in, aggregate, documented field by field, and off by default. The current
answer is simply not to collect it
([../community/data-policy.md](../community/data-policy.md)).

## Experimental work

Anything from this page that is prototyped will live under `experimental/`,
clearly separated from the enforcement path, and will never be required for the
stable product. Nothing has been prototyped yet, so the directory does not exist -
which is the honest state, rather than an empty directory implying work in
progress.
