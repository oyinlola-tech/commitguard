# `commitguard github` and `commitguard ci github`

## `commitguard github setup`

Prints the workflow status, the required check name (`commitguard`) and the exact
branch-protection steps. It cannot configure or verify branch protection - that
is GitHub's setting, and it says so instead of guessing.

## `commitguard github validate`

Validates a GitHub App deployment: configuration, authentication (the App JWT),
installations and their permissions. This is the only command that can tell you
an installation actually works, because it asks GitHub.

Needs `COMMITGUARD_GITHUB_APP_ID`, a private key, a webhook secret and a data
directory - see [../deployment/github-app.md](../deployment/github-app.md).

## `commitguard github webhook-test`

Verifies and normalises a webhook payload locally: no network, no scan. Useful
when a delivery is being rejected and you want to know why.

## `commitguard github serve`

Runs the App service: webhook endpoint, scan workers, and the dashboard when
configured. Put a TLS-terminating reverse proxy in front of it.

```text
commitguard github serve [--host 127.0.0.1] [--port 8080]
```

## `commitguard ci github`

The GitHub Actions check. It reads `$GITHUB_EVENT_PATH` and scans what the event
introduces:

| Event | Scanned |
|---|---|
| `pull_request` | every commit the pull request introduces (`head ^base`) |
| `merge_group` | the merge queue's candidate commit |
| `push` | the commits the push introduced, including new branches and tags |

The policy comes from the **base** commit, so a pull request cannot relax the
rules judging it, and the rules come from the installed CommitGuard, never from
the repository. It needs `contents: read` and no secrets, so fork pull requests
work.

## Exit codes

| Code | When |
|---|---|
| `0` | no violation (`ci github`), or the command succeeded |
| `1` | a violation was found (`ci github`) |
| `2` | error: missing or invalid event, configuration that cannot be evaluated, authentication failure, or GitHub is unreachable |

`ci github` fails closed: if the policy cannot be evaluated, the job fails rather
than reporting success.
