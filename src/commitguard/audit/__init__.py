"""Audit events: a structured record of what CommitGuard decided and why.

The GitHub App records installation changes, scans, violations, policy
modifications and errors as :class:`~commitguard.audit.models.AuditEvent`
objects. Every event is written as a structured log line and, when a storage
backend is configured, appended to it (the App uses its SQLite state store).

Audit events are deliberately small: IDs, commit SHAs, rule IDs, finding
fingerprints, counts and decisions - never commit messages, author names or
e-mail addresses, file contents, tokens or keys. They are retained for a
bounded period (see docs/github-app.md). The local CLI and Git hooks do not
record audit events.
"""
