# CommitGuard

**Git commit provenance and contribution policy enforcement.**

> **Status: pre-alpha (Phase 2 — AI attribution detection).** `commitguard scan`
> and `commitguard check` analyse commits locally and exit non-zero when a
> policy blocks them. **Git hooks and GitHub enforcement are not implemented
> yet**, so nothing is enforced automatically. See [What works today](#what-works-today).

---

## What CommitGuard is

CommitGuard analyses Git commit metadata and decides, according to a
repository's policy, whether a commit is acceptable. It is built as a general
*provenance and contribution policy engine*: independent detectors report what
a commit claims about its origin, and a separate policy layer decides what to
do about it.

The first policy is **AI agent attribution**. This commit is blocked by default:

```text
feat: implement authentication

Co-authored-by: Claude <noreply@anthropic.com>
```

This one is not:

```text
feat: implement authentication

Co-authored-by: John Doe <john@example.com>
```

## What it detects — and what it does not

CommitGuard detects **explicit attribution and identity evidence** in commit
metadata:

| Rule | Detector | Example | Default |
|---|---|---|---|
| `ai_coauthor` | `coauthor` | `Co-authored-by: Claude <noreply@anthropic.com>` | block |
| `ai_identity` | `identity` | author/committer `Copilot <…+Copilot@users.noreply.github.com>` | block |
| `ai_trailer` | `trailer` | `Generated-by: Claude Code`, `🤖 Generated with [Claude Code](…)` | block |
| `malformed_trailer` | `trailer` | `Co-authored-by Claude noreply@anthropic.com` | warn |
| `bot_identity` | `bot` | author `dependabot[bot]` (a bot, **not** an AI) | warn |

It does **not**:

- determine whether code was written by an AI. A finding means *"this commit
  contains an identity or attribution associated with an AI agent"*, never
  *"this code was written by AI"*;
- guess from wording (`feat: use AI service for recommendations` is not evidence);
- analyse diffs or file contents, call any AI/LLM API, or use the network;
- prove authorship: metadata is self-asserted and can simply be removed.

## Why it exists

AI coding agents increasingly write commits, and many record themselves in
commit metadata. Some organisations and projects need to control that for
licensing or contributor-agreement reasons, accurate provenance records, or a
consistent contribution policy. Checking by hand does not scale, and naive
string matching is both easy to evade (casing, malformed trailers, look-alike
Unicode, escape codes that hide a line) and prone to false positives (a human
named Claude, an employee with an `@anthropic.com` address).

## How AI attribution is identified

```text
Git ─▶ Commit parser ─▶ Commit (author, committer, message, trailers)
                             │
                   Detection engine (4 detectors, pure, offline)
                             │  rules/*.yaml ─▶ identity matcher
                             ▼
                        Findings (rule, severity, confidence, evidence)
                             │
                   Policy evaluator (.commitguard.yaml)
                             ▼
                   Decision: ALLOW / WARN / BLOCK  ─▶ exit code 0 / 0 / 1
```

- **Rules are data** (`rules/ai-identities.yaml`, `ai-domains.yaml`,
  `bot-identities.yaml`, `patterns.yaml`), not code.
- **Matching is deliberate:** exact comparison after case/width/whitespace
  normalisation, removal of invisible characters and folding of Cyrillic/Greek
  look-alikes. No substring or fuzzy matching: `Claude` never matches
  `Claudette` or `Claude Dupont`.
- **Evidence is combined:** an exact AI email, GitHub bot login or distinctive
  name prefix is strong evidence; a bare alias like `Claude` is medium
  confidence; a vendor domain alone (`jane@anthropic.com`) is never enough.
- **Explicit evidence > weak inference.** An agent not listed in the rules is
  not guessed; add a rule instead.

Details: [docs/detection-engine.md](docs/detection-engine.md).

## Detection vs. policy

| | Detector | Policy |
|---|---|---|
| Question | *Does this commit list an AI co-author? What is the evidence?* | *Is that allowed here?* |
| Output | `Finding` (rule, severity, confidence, evidence, remediation) | `Decision` (allow / warn / block, with reasons) |
| Knows about | a single commit and rule data | configuration |
| Side effects | none | none |

Precedence is deterministic: **block > warn > allow**, independent of detector
order. A detector that crashes blocks (fail closed). See
[docs/policy-engine.md](docs/policy-engine.md).

## CLI usage

```bash
commitguard init                          # write .commitguard.yaml with secure defaults
commitguard scan                          # explain findings for HEAD
commitguard scan origin/main..HEAD        # every commit in a range
commitguard scan --format json            # structured report (schema_version 1)
commitguard check                         # machine-friendly result for HEAD
commitguard check --quiet origin/main..HEAD
commitguard check --message-file .git/COMMIT_EDITMSG   # a commit that does not exist yet
commitguard policy list                   # effective policies and config layers
commitguard doctor                        # environment, config and rules health
```

Blocked `scan` output (abridged):

```text
CommitGuard
✗ BLOCKED: policy violation detected

AI coauthor detected
  Commit:      4f71c92
  Detector:    coauthor
  Rule:        ai_coauthor
  Severity:    high
  Confidence:  high
  Action:      block (policy ai_coauthor)
  Evidence:    Claude <noreply@anthropic.com>
  Source:      Co-authored-by trailer, line 4
  Matched:     email "noreply@anthropic.com" (ai-identities.yaml#claude)
  Remediation: Remove the AI co-author attribution from the commit message ...

Result: BLOCK
```

`check` output is one tab-separated line per finding followed by a summary:

```text
BLOCK	4f71c92	coauthor	ai_coauthor	Claude <noreply@anthropic.com>
result=BLOCK commits=1 block=1 warn=0 allow=0
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | allowed (no findings, or only `allow`/`warn` findings) |
| `1` | blocked by policy (a finding or detector failure evaluated to `block`) |
| `2` | error: invalid configuration/rules, Git error, bad arguments, unexpected failure |

CommitGuard never modifies commits or rewrites history; remediation is always
left to the developer.

## Configuration

```yaml
# .commitguard.yaml
version: 1
policies:
  ai_coauthor:
    enabled: true
    action: block
  bot_identity:
    enabled: true
    action: warn
```

Layers, lowest precedence first: built-in defaults → global
`~/.config/commitguard/config.yaml` → repository `.commitguard.yaml` →
`--config PATH`. Omitted policies keep secure defaults; invalid configuration
is an error (exit 2). See [docs/configuration.md](docs/configuration.md).

## Git hooks and enforcement layers

Local Git hooks (Phase 3) will run `commitguard check` automatically on
`git commit`/`git push`. Hooks run on the developer's machine and can be
skipped (`--no-verify`), so they are fast feedback, **not a security
boundary**. Repository-level enforcement (Phase 4) will run the same engine in
GitHub Actions as a required check, reading policy from the protected base
branch. See [docs/git-hooks.md](docs/git-hooks.md) and
[docs/github-enforcement.md](docs/github-enforcement.md).

## What works today

- `scan`, `check` (including `--message-file`, `--format json`, `--quiet`),
  `init`, `policy list`, `doctor`, with documented exit codes
- Commit model with parsed trailers; lenient, bounded trailer parser that
  records malformed and evasive variants instead of crashing
- Four detectors (`coauthor`, `identity`, `trailer`, `bot`) driven by YAML rules
- Detector registry, detection engine (fail closed), policy evaluator
- Layered, strictly validated configuration
- Hardened read-only Git access (no shell, `--end-of-options`, no mailmap /
  replace objects, batched reads)
- Terminal-safe output and ASCII-only JSON

Not yet: `install`/`uninstall` and hook enforcement (Phase 3), GitHub
enforcement (Phase 4), audit storage, signature verification, dashboard.

## Development

Requires Python 3.12+ and Git 2.31+.

```bash
./scripts/install-dev.sh
source .venv/bin/activate
pytest
ruff check . && ruff format --check .
mypy
```

## Roadmap

**Phase 1 — Foundation** ✔
Project structure · CLI · configuration · Git abstraction · commit model ·
detector interface · policy interface · testing foundation

**Phase 2 — AI attribution detection** ✔ *(current)*
`Co-authored-by` parsing · AI identity rules · AI domain rules · identity
detection · findings · blocking decisions

**Phase 3 — Git enforcement**
`pre-commit` · `commit-msg` · `pre-push` · hook installation · hook management ·
local repository enforcement

**Phase 4 — GitHub enforcement**
GitHub Actions · pull request checks · repository policies · branch protection integration

**Phase 5 — Security intelligence**
Advanced bot detection · signed commit verification · secret detection ·
provenance analysis · audit storage · advanced rules

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
- [SECURITY.md](SECURITY.md) — please report vulnerabilities and detection bypasses privately
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- [MIT License](LICENSE)
