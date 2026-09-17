# GitHub App: one service for many repositories

What this shows: instead of a workflow in every repository, a service you host
receives signed webhooks and publishes the Check Run `commitguard-app` on the
exact commit it scanned. It is the only mode that supports central policy,
exceptions, rollouts and the dashboard.

**Status: experimental.** It is covered by integration tests against a fake
GitHub, and it has not been run against github.com in production by anyone yet.

## What you need

1. A GitHub App you create (Settings -> Developer settings -> GitHub Apps) with:
   - **Checks**: read and write
   - **Contents**: read-only, **Metadata**: read-only, **Pull requests**: read-only
   - Webhook events: `pull_request`, `push`, `merge_group`, `check_run`,
     `check_suite`, `installation`, `installation_repositories`
   - A webhook secret (a long random string) and a private key.
2. A host that GitHub can reach over HTTPS, and a TLS-terminating reverse proxy.

## Run it

```bash
export COMMITGUARD_GITHUB_APP_ID=123456
export COMMITGUARD_GITHUB_PRIVATE_KEY_FILE=/etc/commitguard/app.pem   # chmod 600
export COMMITGUARD_GITHUB_WEBHOOK_SECRET_FILE=/etc/commitguard/webhook.secret
export COMMITGUARD_APP_DATA_DIR=/var/lib/commitguard

commitguard github validate     # configuration, authentication, permissions, installations
commitguard github serve --host 127.0.0.1 --port 8080
```

Point the App's webhook URL at `https://your-host/webhooks/github`.

## What you should see

| Situation | Result |
|---|---|
| Pull request with an AI co-author trailer | Check Run `commitguard-app` completes as **failure**, with the trailer as evidence |
| Webhook with a wrong or missing signature | HTTP 401, nothing is scanned |
| The same delivery sent twice | the second is a duplicate; one scan |
| GitHub unreachable during a scan | the check is **error**, never success |
| The installation is suspended | audit entry and a critical notification; no silent pass |

Each row is a recorded experiment in
`tests/integration/github/app/security/test_server_enforcement_experiments.py`.

## Deployment and operations

See [docs/deployment/github-app.md](../../docs/deployment/github-app.md),
[docs/deployment/production.md](../../docs/deployment/production.md) and
[docs/operations/runbook.md](../../docs/operations/runbook.md).
