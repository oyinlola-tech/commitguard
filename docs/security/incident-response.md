# Incident response

For when something has already gone wrong. Vulnerability *reports* follow
[vulnerability-response.md](vulnerability-response.md); this page is about live
incidents. Operational procedures per failure mode are in
[../operations/runbook.md](../operations/runbook.md).

Each incident: **detect -> contain -> investigate -> recover -> communicate ->
prevent recurrence.**

## Security bug in CommitGuard itself

- **Detect**: a report, a failing security test, or a benchmark run showing a new
  false negative.
- **Contain**: if the bug means violating commits are being passed, tell affected
  users to treat recent passing checks as unverified, and re-scan history with
  `commitguard scan <range>` once fixed.
- **Investigate**: reproduce as a failing test; determine which versions and
  which deployment modes are affected.
- **Recover**: fix, regression test, release; re-scan.
- **Communicate**: advisory once the fix is available.
- **Prevent**: add the case to the labelled dataset (new version) and, if a class
  of input was missed, to the fuzzing strategies.

## Compromised GitHub App (private key or webhook secret)

- **Detect**: unexpected check runs, deliveries you did not expect, GitHub's own
  security alerts, audit entries without a matching action.
- **Contain**: in the App's settings, **revoke the private key immediately** and
  generate a new one; change the webhook secret. Both are read from files or
  environment variables (`COMMITGUARD_GITHUB_PRIVATE_KEY_FILE`,
  `COMMITGUARD_GITHUB_WEBHOOK_SECRET_FILE`), so rotation is: write the new value,
  restart the service. Suspend the installation if you cannot rotate at once.
- **Investigate**: the audit log records every scan, policy change and
  installation event; webhook deliveries are recorded with their payload digest,
  so replays and forgeries are distinguishable.
- **Recover**: rotate, restart, re-scan the affected repositories.
- **Communicate**: tell every organization with the App installed what the key
  could have done: publish check runs and read metadata. It cannot write code.
- **Prevent**: keep the key file `chmod 600` (CommitGuard warns when it is not on
  POSIX systems), keep it out of the repository, and rotate on a schedule.

## Leaked credential in logs or an issue

- **Contain**: rotate first, investigate second.
- **Investigate**: CommitGuard redacts registered secrets and credential-shaped
  strings (GitHub tokens, JWTs, PEM blocks, `Authorization` headers) from logs,
  errors and API responses, but redaction is a second line of defence, not a
  guarantee.
- **Prevent**: never paste tokens into issues; the issue templates ask for this
  explicitly.

## Malicious or tampered release

- **Detect**: a checksum that does not match, or a tag pointing at an unexpected
  commit.
- **Contain**: delete the release, tell users to pin the last known-good commit
  SHA.
- **Recover**: re-create the release from a verified commit.
- **Prevent**: installs are pinned to a full commit SHA (never a branch or tag),
  and the CI workflows pin every Action by SHA.

## Dependency vulnerability

`pip-audit` runs in CI on every push, pull request and weekly. On a finding:
assess whether the vulnerable path is reachable from CommitGuard, upgrade,
regenerate `requirements/ci.txt` with hashes, and note it in the changelog.
CommitGuard's runtime dependencies are deliberately few (typer, pydantic, PyYAML,
and `cryptography` only for the App service).

## Database compromise or corruption

- **Contain**: stop the service; the SQLite file is the whole state.
- **Investigate**: what it holds is repository metadata, scan results, findings,
  policies, audit entries and session records - no repository content and no
  GitHub tokens (they are short-lived and never stored).
- **Recover**: restore from a backup, restart, re-scan open pull requests.
- **Prevent**: file permissions, backups, and the integrity checks in
  [../operations/runbook.md](../operations/runbook.md).

## Policy corruption or unexpected policy change

Policy versions are immutable and every change is audited with an author. A
rollback is a **new** version, not an edit, so the history of what was enforced
when stays intact. If a policy changed unexpectedly, find the version in the
audit log, roll back to the previous version, and check how the author obtained
the permission.

## False security state

The most dangerous incident: the dashboard or a check says "secure" when nothing
was verified. Treat any of these as an incident, not a bug report:

- a check reporting **success** for a commit that was never scanned;
- a repository shown as protected without evidence from GitHub;
- scans silently not running (a stalled queue) while the posture stays green.

These are exactly what the reliability experiments exercise
(`tests/integration/github/app/security/test_server_enforcement_experiments.py`):
GitHub unreachable, revoked permissions and a database failure all produce
`error`, never `success`. If you see otherwise, report it as a **Critical**
vulnerability.
