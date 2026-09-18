# Security Policy

## Supported versions

CommitGuard is pre-alpha (`0.1.0.dev0`). Only the `main` branch receives fixes.
Local hooks, the GitHub Action and the GitHub App enforce policy, but the project
has not had an external security review: treat it as pre-release software.

## Reporting a vulnerability

Please report privately using **GitHub private vulnerability reporting**
("Report a vulnerability" under the repository's *Security* tab). Do not open a
public issue, pull request or discussion.

Private reporting is the only supported channel: it lets a fix exist before the
problem is public. There is no security e-mail address; GitHub's private
reporting handles the whole exchange.

Include:

- affected version or commit;
- a description and impact;
- reproduction steps or a proof of concept (a crafted commit or config is ideal);
- whether the issue is already public.

**Never include secrets** in a report: no tokens, private keys, webhook secrets,
session cookies or private repository content. A crafted commit message or
configuration file is all that is usually needed to reproduce a detection issue.

### What to expect

CommitGuard is maintained by one person. These are honest targets, not
guarantees:

| Stage | Target |
|---|---|
| Acknowledgement | 5 working days |
| Triage: in scope, severity, reproduced or more information needed | 10 working days |
| Fix for a Critical or High issue on `main` | 30 days from confirmation |
| Advisory published | with or shortly after the fix |

You will be told which of those applies to your report, and if a timeline slips
you will be told that too.

### Disclosure

Coordinated: details are published once a fix is available. If you intend to
publish on your own schedule, say so in the report and we will work to it.
Credit is given in the advisory unless you prefer to stay anonymous.

### Safe harbour

Testing against **your own** repositories and your **own** deployment of the
GitHub App is welcome, and we will not pursue a report made in good faith under
this policy. Do not test against other people's repositories or organizations,
do not access data that is not yours, and do not run denial-of-service or spam
tests against hosted services.

How a report becomes a fix, a regression test and an advisory:
[docs/security/vulnerability-response.md](docs/security/vulnerability-response.md).
Fixes so far, including the four found by this project's own fuzzing and
benchmarks, are listed there.

## In scope

- Code or command execution through configuration, rule files, commit metadata,
  or repository contents.
- Shell / option injection into Git or other subprocesses.
- Terminal escape injection that survives CommitGuard's output sanitisation.
- **Detection bypasses**: attribution that a documented, implemented detector
  should catch but does not.
- Policy weakening: configuration that silently lowers enforcement.
- Fail-open behaviour: errors that result in ALLOW.
- Leakage of environment variables, tokens or repository content.
- CommitGuard modifying a repository or its history.

## CI and supply chain

- GitHub Actions used by CommitGuard are pinned to full commit SHAs; updates are
  made deliberately with the upstream tag noted next to the SHA.
- CI installs dependencies from `requirements/ci.txt` with `--require-hashes`.
- Reports about the GitHub layer are in scope, including ways to make the check
  pass for commits that the same policy blocks locally.

## GitHub App

In scope in addition to the above:

- forging or replaying webhooks, or making a scan run for a repository an
  installation does not cover;
- cross-tenant access to another installation's repositories, scans or audit data;
- leaking the private key, webhook secret, JWTs or installation tokens;
- a Check Run reporting success for a commit that was not evaluated, or a result
  attached to the wrong commit;
- execution of repository-controlled code by the service.

Operators: rotate the App private key and webhook secret in GitHub if you
suspect exposure, and report the circumstances if CommitGuard was the cause.

## Dashboard

In scope in addition to the above:

- reading or changing another organization's data, or data for a repository
  GitHub does not let the user see;
- performing an action the user's role does not allow (for example weakening
  policy as a viewer or security manager), or granting a role;
- CSRF, XSS (including through commit metadata), open redirects, session
  fixation or theft;
- a GitHub token, client secret or session token reaching the browser, logs or
  the database in clear text;
- the dashboard showing a result different from the one CommitGuard reached
  (for example PASS for a blocked scan, or a violation marked resolved while
  the commit is still present).

Rotate the client secret in the GitHub App settings if you suspect exposure;
existing sessions can be revoked from **Settings** or by deleting the
`sessions` rows.

## Notifications, merge queue and policy recovery

In scope in addition to the above:

- reading, marking or archiving another user's notification, or receiving a
  notification about a repository or organization you cannot access;
- muting mandatory security notifications, or changing organization
  notification settings, e-mail recipients or webhooks without
  `notifications:manage`;
- forging a CommitGuard webhook that passes signature and timestamp
  verification, obtaining an endpoint's signing secret after creation, or
  making CommitGuard send a webhook to a private or internal address in
  production;
- injecting headers or HTML through notification content;
- rolling back organization policy without `policies:rollback`, without
  confirmation, over a concurrent change, or to a tampered version; modifying
  or deleting a published policy version;
- a merge group or re-run result published to the wrong repository or commit,
  an outdated commit's result becoming current, or a GitHub event processed
  twice into duplicate scans or notifications;
- any path where an error, outage or notification failure produces a passing
  CommitGuard check.

Rotate `COMMITGUARD_NOTIFICATION_SIGNING_KEY` if you suspect exposure (every
endpoint's secret changes; re-register receivers), and rotate SMTP credentials
at your provider.

## Organization governance

In scope in addition to the above:

- reading or changing another organization's groups, policy drafts,
  approvals, exceptions, rollouts, simulations, bulk operations, schedules,
  settings, rules or reports, or learning that they exist;
- publishing a policy without approval when approval is required (other than
  the audited emergency publication by an owner), approving one's own policy
  change or exception, or publishing a document different from the approved one;
- weakening a mandatory requirement through a group policy, repository policy,
  `.commitguard.yaml`, group membership or onboarding mode without an approved,
  scoped, expiring exception;
- an exception that applies outside its scope or after it expired or was
  revoked, or a permanent exception without the organization setting and
  approval;
- a scan evaluated with an outdated effective policy after a change was
  committed, or a rollout applying a version to repositories not enrolled;
- a policy simulation, report or search that changes state, bypasses
  repository visibility, or leaks another tenant's data;
- code execution, regular-expression denial of service or rule override
  through organization rules;
- CSV or spreadsheet formula injection through exported reports;
- a security posture of `secure` while enforcement, installation
  synchronisation or policy propagation is unavailable.

Compliance reports describe CommitGuard policy enforcement only; claims that
they certify SOC 2, ISO 27001 or any framework are not made by the project.

## Security documentation

| Document | Covers |
|---|---|
| [docs/security/threat-model.md](docs/security/threat-model.md) | Assets, trust boundaries, threats and the evidence for each mitigation |
| [docs/security/vulnerability-response.md](docs/security/vulnerability-response.md) | Report to fix to advisory, and the fixes so far |
| [docs/security/incident-response.md](docs/security/incident-response.md) | Key rotation, compromise, corruption, false security state |
| [docs/security/supply-chain.md](docs/security/supply-chain.md) | Dependencies, CI integrity, and the gaps |
| [docs/security/ci-pipeline-security.md](docs/security/ci-pipeline-security.md) | Fork pull request safety in the workflows |
| [docs/security/review-guide.md](docs/security/review-guide.md) | Orientation for an independent reviewer |

## Installing safely

CommitGuard is published as **`commitguardian`**. The names `commitguard` and
`commitguard-cli` on PyPI belong to unrelated projects, so never install those.
See [docs/getting-started/quickstart.md](docs/getting-started/quickstart.md) and
[ADR-009](docs/adr/009-published-to-pypi-as-commitguardian.md).

## Out of scope

- Bypassing **local** hooks (`--no-verify`, deleting hooks): documented and
  expected; see `docs/threat-model.md`.
- Merges allowed because branch protection does not require the check, or a
  pull request edited the workflow file: documented GitHub configuration issues.
- Removing attribution from a commit entirely: metadata is self-asserted, and
  CommitGuard does not claim to prove authorship.
- Features documented as not implemented.
