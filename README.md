# CommitGuard

**Git commit provenance and contribution policy enforcement.**

> **Status: pre-alpha (Phase 1 — Foundation).** The architecture, CLI skeleton,
> configuration validation, Git abstraction, and policy evaluation exist.
> **AI attribution detection is not implemented yet**, and CommitGuard does not
> block anything today. See [What works today](#what-works-today).

---

## What CommitGuard is

CommitGuard analyses Git commits and decides — according to a repository's
policy — whether they may be committed, pushed, or merged. It is built as a
general *provenance and contribution policy engine*: a set of independent
detectors that report what a commit claims about its origin, and a policy
layer that decides what to do about it.

The first policy it will enforce is **AI agent attribution**: detecting and
blocking commits that list an AI agent as a co-author or contributor, such as:

```text
feat: implement authentication

Co-authored-by: Claude <noreply@anthropic.com>
```

## Why it exists

AI coding agents increasingly write commits, and many of them record
themselves in commit metadata — as `Co-authored-by` trailers, as the commit
author, or through other trailers. Some organisations and open-source projects
need to control that: for licensing or contributor-agreement reasons, to keep
provenance records accurate, or simply to apply a consistent contribution
policy.

Checking this by hand does not scale, and simple string matching is easy to
evade (different casing, malformed trailers, look-alike characters, terminal
escape codes that hide a line when the log is displayed).

## The problem it solves

- **Visibility:** make contribution provenance claims explicit and explainable.
- **Consistency:** apply one versioned policy file to every contributor.
- **Early feedback:** stop a violating commit locally, before it is pushed.
- **Enforcement:** (planned) stop it at the repository, where local tooling
  cannot be bypassed.

It is **not** a way to prove who wrote code. Git metadata is self-asserted:
anyone can remove a trailer or change an author name. CommitGuard enforces
policy over what commits *claim*; see [docs/threat-model.md](docs/threat-model.md).

## Architecture at a glance

```text
 git commit / git push
          │
          ▼
 ┌──────────────────┐   Commit   ┌──────────────────┐  Findings  ┌────────────────┐
 │  Git layer       │──────────▶│ Detection engine │───────────▶│ Policy engine  │
 │  (hooks, reader) │            │  + detectors     │            │  (config)      │
 └──────────────────┘            └──────────────────┘            └───────┬────────┘
                                                                          │
                                                          ALLOW / WARN / BLOCK + explanation
```

Details: [docs/architecture.md](docs/architecture.md).

### Detection vs. policy

These are deliberately separate:

| | Detector | Policy |
|---|---|---|
| Question | *Does this commit list an AI co-author? What is the evidence?* | *Is that allowed here?* |
| Output | `Finding` (rule, severity, evidence, remediation) | `Decision` (allow / warn / block, with reasons) |
| Knows about | a single commit | repository configuration |
| Side effects | none — never touches the repository | none |

The same detector can therefore serve a project that blocks AI co-authors, one
that only warns, and one that explicitly allows them.

### How Git hooks fit in

Local Git hooks (`commit-msg`, `pre-commit`, `pre-push`) will run CommitGuard
automatically, so developers keep using plain `git commit` and `git push` and
get immediate feedback. Templates live in [`hooks/`](hooks/); installation via
`commitguard install` is planned for Phase 3. See [docs/git-hooks.md](docs/git-hooks.md).

### Why local enforcement alone is insufficient

Local hooks run on the developer's machine, under the developer's control. They
can be skipped with `git commit --no-verify`, deleted, never installed, or
avoided by committing from another clone. They are a fast feedback mechanism,
**not a security boundary**.

### GitHub enforcement: the second layer

Repository-level enforcement (Phase 4) will run CommitGuard in GitHub Actions on
every pull request, scan every commit in the pull request, evaluate it against
the policy from the **base** branch, and report a required status check. With
branch protection, violating commits cannot be merged regardless of what
happened locally. See [docs/github-enforcement.md](docs/github-enforcement.md).

## What works today

Implemented and tested:

- `commitguard --version`, `commitguard --help`
- `commitguard init` — writes `.commitguard.yaml` with secure defaults (never overwrites)
- `commitguard policy list` — shows effective policies and where they came from
- `commitguard doctor` — checks Python, Git, repository and configuration validity
- Strict configuration validation (unknown keys/policies, wrong types,
  duplicate YAML keys and YAML aliases are all rejected)
- Hardened, read-only Git wrapper: repository discovery, commit metadata reading
- Core models (`Commit`, `Finding`, `Decision`), detector interface and registry,
  detection engine orchestration, policy evaluator (fail-closed)
- Output sanitisation against terminal escape injection

Present as interfaces only (they exit with a "not implemented" message):
`commitguard scan`, `commitguard check`, `commitguard install`, `commitguard uninstall`,
and all four built-in detectors.

## Development

Requires Python 3.12+ and Git 2.31+.

```bash
./scripts/install-dev.sh          # creates .venv and installs with dev extras
source .venv/bin/activate
pytest                            # tests (Phase 2/3 specs are marked xfail)
ruff check . && ruff format --check .
mypy
```

## Intended usage (not available yet)

```bash
pip install commitguard           # not published yet
cd my-project
commitguard init
commitguard install               # Phase 3

git add .
git commit -m "implement authentication"   # policies enforced transparently
git push
```

## Roadmap

**Phase 1 — Foundation** *(current)*
Project structure · CLI · configuration · Git abstraction · commit model ·
detector interface · policy interface · testing foundation

**Phase 2 — AI attribution detection**
`Co-authored-by` parsing · AI identity rules · AI domain rules · identity
detection · findings · blocking decisions

**Phase 3 — Git enforcement**
`pre-commit` · `commit-msg` · `pre-push` · hook installation · hook management ·
local repository enforcement

**Phase 4 — GitHub enforcement**
GitHub Actions · pull request checks · repository policies · branch protection integration

**Phase 5 — Security intelligence**
Bot detection · signed commit verification · secret detection · provenance
analysis · advanced rules

**Phase 6 — Web dashboard**
Repositories · security status · blocked commits · findings · policies · rules ·
audit history · GitHub integrations. (`web/` is a placeholder.)

## Documentation

- [Architecture](docs/architecture.md)
- [Detection engine](docs/detection-engine.md)
- [Policy engine](docs/policy-engine.md)
- [Configuration](docs/configuration.md)
- [Git hooks](docs/git-hooks.md)
- [GitHub enforcement](docs/github-enforcement.md)
- [Threat model](docs/threat-model.md)

## Contributing, security, license

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [SECURITY.md](SECURITY.md) — please report vulnerabilities privately
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- [MIT License](LICENSE)
