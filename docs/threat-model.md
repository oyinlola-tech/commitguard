# Threat model

> Living document. Status markers: **[done]** implemented and tested,
> **[planned]** designed but not built.

## What CommitGuard protects

The integrity of a repository's **contribution policy**: commits that violate
the policy (initially, AI agent attribution) should not reach protected
branches, and every decision should be explainable with preserved evidence.

## What CommitGuard does not do

- **Prove authorship.** Git metadata is self-asserted. Someone who removes an
  AI trailer and commits under their own name is indistinguishable from a
  human author by metadata alone. CommitGuard enforces policy over *claims*.
- **Detect AI-written code from its content.** "AI generated contribution
  detection" is a future research area and would be probabilistic; it must
  never be presented as deterministic.
- **Replace server-side enforcement.** Local checks are advisory.

## Assets

- the repository policy (`.commitguard.yaml`) and rule data;
- the decision and its evidence;
- the developer's machine and environment (CommitGuard runs inside Git hooks);
- repository contents (must not leave the machine);
- GitHub App credentials: the App private key, the webhook secret, JWTs and
  installation tokens (Phase 5);
- the App's state (installations, scan jobs, audit events) and tenant isolation
  between the accounts and organisations that install it.

## Adversaries

| Adversary | Goal |
|---|---|
| Contributor bypassing policy | get a violating commit merged |
| Malicious commit author (any repo you scan) | crash, mislead, or exploit CommitGuard via crafted metadata |
| Malicious repository content | abuse config/rules to execute code or weaken policy |
| Automated agent | add attribution that evades detection, or strip it |
| Internet attacker (GitHub App) | forge or replay webhooks, reach other tenants' repositories, exhaust the service, steal credentials |

## Threats and mitigations

### GitHub App (Phase 5)

Security boundaries, each validating its input before passing anything on:

```text
GitHub ─▶ webhook boundary (size, content type, rate limit)
       ─▶ authentication boundary (X-Hub-Signature-256, delivery ID)
       ─▶ event normalisation (typed events; raw JSON stops here)
       ─▶ authorization (installation state; down-scoped token; repository ID lookup)
       ─▶ scan service ─▶ CommitGuard core ─▶ policy engine
       ─▶ GitHub Check (exact scanned SHA, ownership-guarded writes)
```

| Threat | Mitigation / limitation | Status |
|---|---|---|
| Forged webhook | HMAC-SHA256 over the raw body with the webhook secret, constant-time comparison, verified before any parsing; missing, malformed or wrong signatures get `401` (tested: valid, invalid, missing, modified payload, wrong secret, empty, malformed) | **[done]** |
| Replayed webhook | `X-GitHub-Delivery` IDs stored with a payload digest: same ID and payload is ignored, same ID with a different payload is `409`; equivalent events under new IDs map to an existing scan job (tested). Delivery IDs expire with retention; a replay older than that re-runs a deterministic scan of the same SHA | **[done]** |
| Duplicate webhook | Idempotent job keys and one Check Run per repository, SHA and check name (tested: 5 deliveries give one scan and one run; 12 concurrent deliveries give one job) | **[done]** |
| Unauthorized repository access (spoofed installation, owner or repository in a payload) | Every scan mints a token down-scoped to that single repository ID; GitHub refuses when the installation does not cover it; the repository is then looked up by immutable ID with that token. Stored state rejects deleted or suspended installations early. Names are never used for authorization or paths (tested: repository outside the installation, spoofed installation ID, removed repository, uninstalled App) | **[done]** |
| Stolen installation token | Tokens live about 1 hour, are limited to one repository and to Checks write plus read-only Contents, Metadata and Pull requests, are kept only in memory, are dropped on uninstall or removal, and are never logged | **[done]** (short lifetime is GitHub's) |
| Stolen private key or webhook secret | Read from files or the environment, wrapped in `Secret`, registered for redaction, never logged or stored, key file permissions checked by `validate`. A stolen key lets the holder act as the App: rotate it in GitHub | partially mitigated (operational) |
| Secrets leaking into logs, errors, Checks or responses | Redaction of registered secrets and credential-shaped strings in logs, errors and stored text; generic HTTP errors; test forces credential-echoing failures and inspects every output surface | **[done]** |
| Malicious repository metadata (names, branches, PR text, commit messages, trailers) | Names validated against GitHub's formats; PR titles and bodies are never read; commit data only reaches detectors and escaped Markdown; no shell (tested with `$(touch …)`, backticks, `;`, `\|`, `&&`, `../../`) | **[done]** |
| Repository code execution | No checkout, no work tree, no hooks (`--template=`, null hooks path), no submodules, no builds or installs; only commit, tree and config-blob objects are fetched | **[done]** |
| Malicious Git server response or redirect | Protocol allow-list (HTTPS only in production), no redirects, no credential helpers, fetch timeout; token only in a header scoped to the remote URL | **[done]** (Git client bugs remain an upstream risk) |
| PR policy tampering | Trusted base or before policy (Phase 4 code); a weakening is reported as "Security policy modification detected" and audited (tested) | **[done]** |
| Rule tampering | Rules only from the installed package (tested with rule files in the PR) | **[done]** |
| Repository weakens organisation requirements | Optional mandatory policy applied after trusted config; can only tighten; `enabled: false` rejected (tested) | **[done]** (organisation-hosted policy: planned) |
| Stale scan overwrites newer result | Sequenced jobs; per-SHA check ownership with guarded writes; superseded PR scans cancelled (tested: B before A, A mid-flight while B completes) | **[done]** |
| Result attached to the wrong commit (TOCTOU) | Check Runs are created with the SHA that is fetched and scanned; the planned range head is verified before publishing; GitHub's returned head SHA is checked | **[done]** |
| GitHub API outage, errors or rate limits produce a false PASS | Bounded retries (3 attempts), bounded rate-limit waits; every failure publishes `failure` or `timed_out` when a check exists, otherwise no check (a required check stays unsatisfied) (tested: 5xx, timeouts, persistent 429, permission revoked mid-scan) | **[done]** |
| Reduced App permissions silently pass | Token requests ask for the required permissions and verify the granted ones; missing permissions stop the scan without publishing success (tested) | **[done]** |
| Denial of service | Request body limit (25 MB, checked from `Content-Length` before reading), JSON depth limit, duplicate-key rejection, per-client rate limit, bounded queue with durable recovery, commit limit per scan, fetch and Git timeouts, pagination page limit, output caps (20 findings, 60,000 characters) | **[done]** (per-message size limit: not implemented) |
| SSRF | No URLs are taken from payloads: the API base and Git host are fixed, paths are built from validated segments, pagination links must stay on the API host, only the HTTPS handler is installed | **[done]** |
| Cross-tenant data access | All storage keyed by installation and repository IDs; tenant-scoped listing APIs; mirrors under numeric installation and repository directories | **[done]** (no external API yet) |
| Plain-HTTP interception of webhooks | The service binds to localhost; TLS is required at the reverse proxy (documented). GitHub requires HTTPS webhook URLs for signature verification to be meaningful | limitation documented |
| Check exists but merges are not blocked | Branch protection or rulesets must require `commitguard-app`; not configured or verified by CommitGuard | limitation documented |

### GitHub server-side enforcement (Phase 4)

| Threat | Mitigation / limitation | Status |
|---|---|---|
| Developer bypasses local hooks | GitHub Actions check analyses every commit the pull request / push introduces with the same engine | **[done]** |
| Developer modifies their local CommitGuard installation or rules | CI installs CommitGuard from the pinned Action commit (or, in this repository, a trusted commit) and uses its bundled rules | **[done]** |
| Pull request relaxes `.commitguard.yaml` to approve itself | Policy is read from the trusted base commit's tree; the change is reported as a notice and applies only after merge (tested) | **[done]** |
| Pull request edits `rules/*.yaml` to weaken detection | Repository rule files are never read; rules come from the installed package (tested) | **[done]** |
| Pull request edits the workflow that runs the check (e.g. `exit 0`) | **Limitation of GitHub's `pull_request` model.** Mitigate with CODEOWNERS review for `.github/workflows/` or organisation rulesets requiring a workflow from another repository; documented, not enforceable by CommitGuard | limitation documented |
| Malicious commit message, author, branch or trailer injects shell or workflow commands | No shell interpolation (env-only inputs, argument vectors); untrusted log text printed inside `::stop-commands::<random>`; annotations escaped; job summary Markdown/HTML-escaped; step outputs are enums/integers (tested with `$(touch /tmp/commitguard-pwned)`, `::set-output`, `::error::`) | **[done]** |
| Malicious YAML in trusted policy | Strict safe loader; object tags, aliases, duplicate keys rejected; failure fails the check (tested) | **[done]** |
| Malformed or spoofed event payload | Strict normalisation: validated SHAs, control-character checks, consistency checks (`deleted` vs `after`), unsupported events and `pull_request_target` fail | **[done]** |
| Fork pull request steals secrets or writes to the repository | `pull_request` only, `contents: read`, no secrets used, `persist-credentials: false`; no repository-controlled code executed | **[done]** |
| Third-party GitHub Action compromises CI | Actions pinned to verified commit SHAs (enforced by tests); minimal permissions | **[done]** |
| Dependency tampering or confusion during install | `--require-hashes` lock (`requirements/ci.txt`), `--no-build-isolation`, CommitGuard installed from source by path, never by index name | **[done]** |
| CommitGuard cannot evaluate (bad config, missing commits, Git missing, import failure) and CI passes | Every error path exits non-zero and prints `Result: FAILED`; shallow clones fail with an actionable message (tested) | **[done]** |
| Direct push bypasses pull request validation | Push-triggered check detects it **after** the commits reach GitHub. Prevention requires protected branches, required pull requests and the required `commitguard` check | limitation documented |
| Cancelled push runs leave commits unchecked | No `cancel-in-progress` in shipped workflows; `doctor` warns about it | **[done]** |
| Branch protection not actually configured | CommitGuard cannot verify it locally; `doctor` and `github setup` say so explicitly | limitation documented |
| Squash/rebase merge message edited at merge time | Detected by the post-merge push check (after landing) | limitation documented |

### Local Git operations (Phase 3)

| Threat | Mitigation / limitation | Status |
|---|---|---|
| Developer **accidentally** commits or pushes an AI-attributed commit | pre-commit and commit-msg hooks block the commit; pre-push blocks every outgoing violating commit, including ones created with `--no-verify`, merges, rebases and tools that skip commit hooks | **[done]** |
| Developer **intentionally** bypasses local hooks (`--no-verify`, deleting hooks, `core.hooksPath`, editing `.commitguard.yaml`, another clone) | **Limitation:** local hooks are user-controlled; CommitGuard does not fight Git's bypass mechanism (tests assert it works). `commitguard doctor` makes these states visible. **Mitigation:** GitHub-side check (see below) | limitation documented; mitigation **[done, Phase 4]** (requires branch protection) |
| Malicious commit metadata or ref names attempt command injection (`$(…)`, backticks, `;`, `&&`, `\|`, quotes, Unicode) | Git metadata and pre-push input are untrusted data: no shell anywhere, argument vectors or stdin only, object IDs validated before use, ref names never interpolated; tests with hostile messages and branch names assert no execution | **[done]** |
| Existing hooks are destroyed by installation | Foreign hooks are renamed to `<hook>.pre-commitguard` and chained (never overwritten); conflicts refuse; uninstall removes only the managed block and restores the original; tests compare bytes | **[done]** |
| Repository content hijacks the hook (a `commitguard/` package in the work tree) | Hooks run `python -P -m commitguard`, so the current directory is not on `sys.path`; tested | **[done]** |
| CommitGuard unavailable (venv removed, not on PATH) silently allows operations | Wrapper falls back to `commitguard` on PATH, otherwise blocks with instructions (exit 2) | **[done]** |
| Invalid configuration or internal errors allow operations | Hook commands map every exception to exit 2 (blocks Git) with "security check could not be completed"; not configurable | **[done]** |
| Malformed pre-push input | Strict parsing (four fields, valid object IDs, no control characters, consistent deletions); anything else blocks | **[done]** |
| Hook script tampering | Managed block checksum; `doctor` reports modifications, `install` repairs on request | **[done]** (detection, not prevention) |
| Shared `core.hooksPath` modified for all repositories | Install refuses hooks directories outside the repository's Git directory unless explicitly allowed; `--global` uses `init.templateDir` only when unset, never `core.hooksPath` | **[done]** |
| Huge pushes exhaust resources or are silently truncated | Only outgoing commits analysed, deduplicated, bounded by `max_push_commits`; exceeding it blocks | **[done]** |
| Stale remote-tracking refs exclude commits the remote no longer has | Accepted: those commits were checked when pushed; exclusion never uses unknown data | limitation documented |
| commit-msg cannot see `--cleanup` or the final commit object | Documented best effort; pre-push analyses real commit objects | limitation documented |

### Bypassing local enforcement
- `--no-verify`, removing hooks, other clones. **Accepted locally**; mitigated by
  server-side enforcement **[done, Phase 4; effective with branch protection]**. `pre-push` analyses commits
  created with `--no-verify` **[done]**.

### Weakening policy through configuration
- PR adds `action: allow`. Mitigation: CI reads policy from the base branch **[planned]**.
- Typos/ambiguity silently disabling a policy: strict schema, unknown keys and
  policy IDs rejected, strict booleans, no nulls **[done]**.
- Duplicate YAML keys overriding earlier values: rejected **[done]**.
- Omitted policies: fall back to secure defaults, never to "off" **[done]**.

### Code execution via configuration or data
- YAML object construction: `SafeLoader` only **[done]**.
- Plugins/commands named in config: not supported by design; explicit
  in-code detector registry **[done]**.
- ReDoS via regex rule data: rules are literal values; the parser and matcher use no regular expressions **[done]**.
- Malicious rule files: strict schema, cross-validation, strict safe YAML **[done]**.

### Crafted commit metadata
- **Terminal escape injection** (hide a trailer, spoof output): all untrusted
  text is sanitised before display; ESC, C0/C1 and bidi controls made visible **[done]**.
- **Option injection** via revisions (`--output=…`): revisions validated
  (no leading `-`, no control characters) and passed after `--end-of-options` **[done]**.
- **Shell injection**: no shell anywhere; argument vectors only **[done]**.
- **Parser confusion** (NULs, malformed dates/SHAs): NUL-delimited Git output
  with the free-form message last; every structured field validated; mismatch
  raises instead of guessing **[done]**.
- **`git replace` objects** substituting an innocent commit:
  `GIT_NO_REPLACE_OBJECTS=1` **[done]**.
- **`.mailmap` rewriting an AI identity** into a human one: `--no-use-mailmap` **[done]**.
- **Evasion by formatting**: key casing/spacing/underscores, missing colon,
  zero-width and bidi characters, fullwidth/mathematical letters, Cyrillic/Greek
  look-alikes, Unicode line separators, indented trailers, trailers outside the
  trailer block: normalised and still detected **[done]**.
- **Trailer flood** (hide attribution after thousands of trailers): parsing is
  bounded and exceeding the limit fails closed (BLOCK) **[done]**.
- **False positives** (humans named like an agent, employees at vendor domains):
  exact matching only, vendor domains never sufficient alone, ambiguous names
  need corroboration **[done]**; single-token alias names remain a documented
  medium-confidence risk.
- **Resource exhaustion**: config/rule/message files size-limited, linear-time
  parsing, `--max-commits` refuses oversized ranges **[done]**; bounded Git
  output capture **[planned]**.
- **Record-splitting in batched Git output**: records separated by a random
  per-call boundary and cross-checked against requested SHAs **[done]**.

### Detector failure
- Any detector exception or invalid output → BLOCK (fail closed) **[done]**.
- Unexpected CLI errors exit 2 (never 1 = "blocked", never 0) without tracebacks **[done]**.

### Information disclosure

The GitHub App stores IDs, repository names, SHAs, states, counts, rule IDs and
finding fingerprints for a bounded retention period (default 30 days). Mirrors
hold commit and tree objects (file names, not file contents) of repositories
while they are installed. See [github-app.md](github-app.md#operational-notes).

- No network access and no AI/LLM APIs in the local tool; no telemetry; enforced by architecture tests **[done]**.
- Reports and JSON contain concise metadata evidence only, never file contents or full messages **[done]**.
- Tracebacks never render local variables (`pretty_exceptions_show_locals=False`) **[done]**.
- Environment variables are never logged **[done — nothing logs them]**.
- GitHub Actions enforcement uses no token and no secrets **[done]**. The GitHub App keeps installation tokens and JWTs in memory only, redacts them from every output and never persists them **[done]**.

### Repository modification
- Detectors receive data, not a repository handle; architecture tests forbid
  I/O imports in detection layers **[done]**.
- Git reads use `GIT_OPTIONAL_LOCKS=0` **[done]**.
- CommitGuard never rewrites commits or history; it never runs `git reset`, `rebase`,
  `commit --amend`, `filter-branch` or `filter-repo`. Remediation text explains
  the scope of any command it suggests **[by design]**.
- `init` never overwrites an existing file **[done]**.
