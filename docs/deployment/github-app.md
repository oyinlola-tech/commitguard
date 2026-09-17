# GitHub App deployment

> Status: **Experimental.** The App service is implemented and covered by
> integration tests that use real Git repositories and an offline model of the
> GitHub API and OAuth flow. It has **not** been run against github.com by this
> project's test suite, and no external production deployment has been recorded.
> Treat a first deployment as a trial next to the GitHub Action.

```text
GitHub ──HTTPS webhook──▶ TLS reverse proxy ──▶ CommitGuard App process
                                                  │ POST /webhooks/github
                                                  │   HMAC-SHA256 signature · delivery-ID dedup · payload validation
                                                  │   scan job stored in SQLite, job ID put on the in-process queue
                                                  ▼
                                            scan worker threads (COMMITGUARD_APP_WORKERS, default 2)
                                                  │ installation token for one repository
                                                  │ metadata-only git fetch into mirrors/ (no checkout)
                                                  ▼
                                            CommitGuard core ──▶ Check Run "commitguard-app" (Checks API)
```

The App is a service **you** run: there is no hosted CommitGuard App. Creating
the App on GitHub, its permissions, events and the trust model are described
in [../github-app.md](../github-app.md). How to run the service is in
[self-hosted.md](self-hosted.md), and what a production operator must provide
is in [production.md](production.md).

## What you deploy

| Component | Provided by CommitGuard | Provided by you |
|---|---|---|
| Webhook receiver, scan workers, maintenance and notification threads | `commitguard github serve` or the WSGI entry point `commitguard.github.app:wsgi_app_from_environment()` | a host, a service user, process supervision |
| HTTPS | no (the service speaks plain HTTP) | a TLS-terminating reverse proxy |
| State | SQLite database `commitguard-app.sqlite3` and `mirrors/` in `COMMITGUARD_APP_DATA_DIR` | persistent disk and backups |
| Credentials | read from environment variables or files | the App ID, private key, webhook secret (and client secret for the dashboard), stored securely |
| Dashboard (optional) | API under `/api/v1/`, static files from `web/dist` | a Node.js 20.19+ build step, the App's client ID and secret |
| Merge protection | Check Runs | branch protection or rulesets that require `commitguard-app` |

No Redis, message broker, separate database server or container orchestrator is
used.

## Minimum steps

1. Create the GitHub App with the permissions and events in
   [../github-app.md](../github-app.md#1-create-the-github-app).
2. Install CommitGuard with the `app` extra from source, pinned to a commit
   (not from PyPI; the PyPI name `commitguard` is an unrelated project):

   ```bash
   python -m venv /opt/commitguard/venv
   /opt/commitguard/venv/bin/python -m pip install \
       "commitguard[app] @ git+https://github.com/oyinlola-tech/commitguard@<commit-sha>"
   ```

3. Set `COMMITGUARD_GITHUB_APP_ID`, `COMMITGUARD_GITHUB_PRIVATE_KEY_FILE`,
   `COMMITGUARD_GITHUB_WEBHOOK_SECRET_FILE` and `COMMITGUARD_APP_DATA_DIR`.
4. `commitguard github validate` until it prints `READY`.
5. Run the service behind a TLS reverse proxy ([self-hosted.md](self-hosted.md)).
6. Install the App on the account or organization, open a test pull request,
   and confirm the `commitguard-app` check appears.
7. Require `commitguard-app` in branch protection or a ruleset only after it has
   run reliably.

## Security properties

| Property | GitHub App |
|---|---|
| Repository code | never checked out, built or executed; mirrors store commits and trees, not file contents (except CommitGuard configuration blobs) |
| Tokens | installation tokens down-scoped to one repository and the four required permissions; kept in memory only |
| Webhooks | rejected unless the HMAC-SHA256 signature matches; replayed delivery IDs are acknowledged without re-processing |
| Policy | trusted commit, then the optional mandatory policy file and organization policy, which can only tighten it |
| Repository control | a repository cannot edit or disable the App's check |
| Merge prevention | only when branch protection or a ruleset requires `commitguard-app`; CommitGuard does not configure or verify this |
| Failure | fails closed: GitHub, Git or storage failures never publish success |

## Limitations

- One host, one service process per database (see
  [production.md](production.md#single-instance-constraint)).
- Not tested against github.com.
- No TLS in the service; no metrics endpoint (counters are in memory only).
- Push checks (`commitguard-app/push`) run after the commits are on GitHub.
- Tested on Linux only.

The full list is in [../github-app.md](../github-app.md#limitations).
