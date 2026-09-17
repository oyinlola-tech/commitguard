# Security documentation

| Document | What it covers |
|---|---|
| [threat-model.md](threat-model.md) | Assets, actors, trust boundaries, threats and the evidence for each mitigation |
| [security-boundaries.md](security-boundaries.md) | What each component trusts, and what it does not |
| [authorization-matrix.md](authorization-matrix.md) | Roles, permissions and tenant isolation in the dashboard and API |
| [review-guide.md](review-guide.md) | Orientation for an independent security reviewer |
| [supply-chain.md](supply-chain.md) | The project's own dependencies, CI and release integrity |
| [ci-pipeline-security.md](ci-pipeline-security.md) | Review of the GitHub Actions workflows against untrusted pull requests |
| [vulnerability-response.md](vulnerability-response.md) | How a report becomes a fix, a regression test and an advisory |
| [incident-response.md](incident-response.md) | What to do when something has already gone wrong |

Reporting a vulnerability: [SECURITY.md](../../SECURITY.md). Please report
privately; do not open a public issue.

## What CommitGuard defends, in one paragraph

CommitGuard decides whether a commit's **metadata** is acceptable under a policy,
and makes that decision at three places: the developer's machine (advisory), the
GitHub check (authoritative when branch protection requires it) and a hosted App
service (authoritative, central policy). The security properties worth arguing
about are: a pull request cannot weaken the policy that judges it; a scan that
cannot complete never reports success; and a local bypass is always visible
server-side. Each of those is covered by a recorded experiment, listed in the
threat model.

## What it does not defend

Commit metadata is self-asserted. Someone who does not want to be attributed can
simply not write the attribution, and no amount of detection changes that.
CommitGuard measures and enforces what a commit *claims*, which is a policy and
provenance-record problem, not proof of how code was written. See
[../research/limitations.md](../research/limitations.md).
