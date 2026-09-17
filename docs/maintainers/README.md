# Maintainer documentation

For whoever maintains CommitGuard - including the author six months from now.

| Page | Covers |
|---|---|
| [architecture.md](architecture.md) | Package map and the dependency rules between layers |
| [development.md](development.md) | Setting up, running the suites, the checks CI runs |
| [release-process.md](release-process.md) | Release candidates, the validation gate, publishing |
| [security.md](security.md) | Protected components, key handling, review requirements |
| [benchmarking.md](benchmarking.md) | Recording results, comparing them, what must never be edited |
| [incident-response.md](incident-response.md) | Where the procedures live |
| [repository-management.md](repository-management.md) | Branch protection, labels, GitHub API changes |

The user-facing documentation lives in [docs/](../), the research and evidence in
[docs/research/](../research/) and `evidence/`.

## The short version

- **Security decisions must fail closed.** Anything that cannot be evaluated is
  an error, never an allow. Every change to the engine, the policy layer or the
  GitHub integration should be read with that question first.
- **Evidence is immutable.** Benchmark results and policy versions are never
  edited or deleted, only added to.
- **Documentation is part of the product.** A page that claims something the code
  does not do is a bug, and several tests enforce that.
- **Do not add features without a problem.** After Phase 10 the project is
  deliberately in evidence-and-adoption mode, not feature mode.
