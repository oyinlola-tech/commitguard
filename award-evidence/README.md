# Award evidence package

Evidence about CommitGuard, prepared for technical assessment. Each claim carries
one of these labels, and they are not mixed:

| Label | Means |
|---|---|
| **IMPLEMENTED** | The code exists and works |
| **MEASURED** | A number from a recorded benchmark on a stated machine |
| **TESTED** | An automated test asserts it |
| **EXTERNALLY VALIDATED** | Someone other than the author verified it |
| **DEPLOYED** | A real organization uses it |
| **PLANNED** | Not built |

**Nothing in this package is EXTERNALLY VALIDATED or DEPLOYED.** The project
became public on 2026-09-14 and has had no external evaluation and no production
deployment. Where those sections exist, they say so. That is deliberate: a package
that claimed otherwise would devalue the parts that are real.

| File | Covers |
|---|---|
| [01-project-origin.md](01-project-origin.md) | Why this exists |
| [02-problem.md](02-problem.md) | The problem and why it is hard |
| [03-technical-contribution.md](03-technical-contribution.md) | What was built |
| [04-cybersecurity-contribution.md](04-cybersecurity-contribution.md) | The security engineering |
| [05-architecture.md](05-architecture.md) | System design and its guarantees |
| [06-research-methodology.md](06-research-methodology.md) | How claims are measured |
| [07-benchmarks.md](07-benchmarks.md) | The numbers |
| [08-security-validation.md](08-security-validation.md) | Attacks attempted and what happened |
| [09-cross-platform-engineering.md](09-cross-platform-engineering.md) | Linux, macOS, Windows |
| [10-open-source.md](10-open-source.md) | Project maturity as open source |
| [11-external-validation.md](11-external-validation.md) | **Empty, and why** |
| [12-real-world-deployment.md](12-real-world-deployment.md) | **Empty, and why** |
| [13-impact.md](13-impact.md) | What measurably changed |
| [14-timeline.md](14-timeline.md) | Dated milestones |
| [15-demonstrations.md](15-demonstrations.md) | How to see it work in minutes |
| [16-publications.md](16-publications.md) | Written output |
| [17-limitations.md](17-limitations.md) | What it does not do |

Repository: https://github.com/oyinlola-tech/commitguard (public, MIT).
Every claim here can be checked by running `commitguard reproduce all`.
