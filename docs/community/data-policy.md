# Data policy

## CommitGuard collects nothing

The CLI, the hooks and the GitHub Action have **no telemetry**. No usage
statistics, no crash reporting, no update checks, no "anonymous" counters.
Nothing is sent anywhere. The only network traffic CommitGuard makes is to the
GitHub API, and only from the commands that exist to talk to GitHub
(`commitguard github ...`, `commitguard ci github`) and from the App service you
host yourself.

There is no plan to add telemetry. If it ever were added it would be opt-in,
documented in detail, and off by default.

## What the App service stores (on your own host)

If you run the GitHub App, its SQLite database holds:

| Stored | Not stored |
|---|---|
| Repository and organization identifiers and names | Repository file contents |
| Scan records: commit SHAs, ranges, decisions, timings | Full commit messages |
| Findings: the matched evidence (a trailer, an identity) | Diffs or source code |
| Policies and their immutable versions | GitHub access tokens (short-lived, never written to disk) |
| Audit entries and notifications | Passwords (there are none; sign-in is GitHub's) |
| Sessions for dashboard users | |

Findings keep the exact metadata that triggered them, because that is the
evidence a human needs to judge the decision. Commit messages as a whole are not
part of reports.

Retention is configurable (`COMMITGUARD_APP_RETENTION_DAYS`); expired scans,
findings and events are purged by a background task.

## Evaluation and pilot data

If you send an evaluation or pilot report:

| | |
|---|---|
| **What** | Whatever you choose to include: versions, commands, outputs, counts, your impressions |
| **Why** | To fix problems, and as evidence that people outside this project have used it |
| **Where** | A public GitHub issue you opened, and - only with your permission - a file in `evidence/external-validation/` |
| **Retention** | Indefinitely, as part of the public repository history |
| **Access** | Public, because the issue is public |
| **Deletion** | Ask, and the content is removed from the repository; the Git history of a public repository cannot be fully unpublished |
| **Anonymisation** | Say how you want to be described, or ask not to be named |

Please do not send private repository content, tokens or personal data about
other people. Redact before you send: a detection issue needs the shape of a
trailer, not your colleague's e-mail address.
