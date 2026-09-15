# Security Policy

## Supported versions

CommitGuard is pre-alpha (`0.1.0.dev0`). Only the `main` branch receives fixes.
Local hooks, the GitHub Action and the GitHub App enforce policy, but the project
has not had an external security review: treat it as pre-release software.

## Reporting a vulnerability

Please report privately using **GitHub private vulnerability reporting**
("Report a vulnerability" under the repository's *Security* tab). Do not open a
public issue, pull request or discussion.

<!-- TODO: add a security contact address once one exists. -->

Include:

- affected version or commit;
- a description and impact;
- reproduction steps or a proof of concept (a crafted commit or config is ideal);
- whether the issue is already public.

We aim to acknowledge reports within 5 working days.

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

## Out of scope

- Bypassing **local** hooks (`--no-verify`, deleting hooks): documented and
  expected; see `docs/threat-model.md`.
- Merges allowed because branch protection does not require the check, or a
  pull request edited the workflow file: documented GitHub configuration issues.
- Removing attribution from a commit entirely: metadata is self-asserted, and
  CommitGuard does not claim to prove authorship.
- Features documented as not implemented.
