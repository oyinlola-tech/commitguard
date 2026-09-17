# Exceptions: allowing something, narrowly and temporarily

What this shows: a policy stays in force everywhere, while one repository is
granted a scoped, expiring exception with a recorded reason.

**Status: experimental** (organization governance, dashboard and API).

## Shape of an exception

| Field | Example | Why it matters |
|---|---|---|
| Scope | one repository, or a repository group | never the whole organization by accident |
| Rule | `bot_identity` | one rule, not "everything" |
| Reason | "Release bot commits during the migration (TICKET-1423)" | recorded in the audit log |
| Expiry | a date, required | an exception that never expires is a policy change in disguise |
| Requested / approved by | two different people | separation of duties |

## Lifecycle

```text
requested -> approved -> active -> expired
                     \-> rejected
```

An expired exception stops applying on its own; nothing has to be remembered.
Expiring and expired exceptions are shown in the dashboard and raise a
notification.

## Try it

In the dashboard: **Organization -> Exceptions -> Request exception**, then
approve it as a security manager or admin. Through the API:
[docs/api/exceptions.md](../../docs/api/exceptions.md).

Details and the decision rules: [docs/policy-exceptions.md](../../docs/policy-exceptions.md).
