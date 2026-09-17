# Repository management

## Branch protection for this repository

Recommended settings for `main` (the project should hold itself to what it
recommends):

- Require a pull request before merging; block direct pushes.
- Require these status checks:
  - `commitguard` - CommitGuard's own check (it dogfoods)
  - `Lint and type check`
  - `Test (ubuntu-latest, Python 3.12)` and the rest of the matrix
  - `Dependency audit (pip-audit)`, `Static security analysis`
- Require branches to be up to date before merging.
- Do not allow force pushes or deletions.

CommitGuard cannot verify any of this from a clone, and `doctor` says so rather
than assuming it.

## Labels

| Label | Meaning |
|---|---|
| `bug`, `security`, `documentation`, `performance`, `compatibility`, `integration`, `usability` | Category (see [../community/feedback-triage.md](../community/feedback-triage.md)) |
| `accepted`, `rejected`, `duplicate` | Triage outcome |
| `good first issue` | Scoped for a newcomer |
| `evaluation` | Feedback from an external evaluation |
| `needs-reproduction` | Waiting on information |

## Triage rhythm

Weekly is enough at this size: read new issues, assign a category, reproduce
bugs, and close what will not be done **with a reason**.

## GitHub API dependencies

CommitGuard depends on GitHub behaviour that GitHub can change. The integration
pins `X-GitHub-Api-Version` where the API supports it, but behaviour changes are
not always versioned.

| Depends on | Used for | If it changes |
|---|---|---|
| Checks API (create and update check runs) | publishing results | the App cannot report; check-run tests fail |
| Pull request events and payloads | scanning what a PR introduces | `normalize_webhook` rejects the payload (fails closed) |
| `merge_group` events and the `gh-readonly-queue/` ref shape | merge queue validation | merge group events are rejected; the ref pattern is asserted in tests |
| Webhook signatures (`X-Hub-Signature-256`) | authenticity | deliveries rejected with 401 |
| GitHub App auth: JWT then installation token | all API calls | authentication failures, reported as errors |
| Installation permission names | validation and posture | `commitguard github validate` reports the mismatch |
| Rate limits | backoff behaviour | scans slow down and eventually error, never silently pass |

**Watch**: the GitHub Changelog (`github.blog/changelog`), the REST API
deprecation notices, and GitHub Apps documentation. Quarterly is a reasonable
cadence; also check after any integration test starts behaving oddly against a
real installation.

When something changes: reproduce it in the fake GitHub used by the integration
tests (`tests/integration/github/app/`), fix, add the case, and note it in the
changelog. The fake is the contract: if it does not model the new behaviour, the
tests prove nothing about it.

## Public repository hygiene

- Issue templates and `CODEOWNERS` live in `.github/`.
- The security policy must always point at private reporting.
- No secrets in the repository, ever - including in tests, fixtures and demo
  data, which use obviously fake values.
