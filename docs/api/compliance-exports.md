# Compliance reports

Point-in-time exports of an organization's posture, coverage, violations,
exceptions, policy changes and installations, as JSON or CSV. A report is not a
certification. For their contents and limits, see
[../compliance-reporting.md](../compliance-reporting.md).

Source: `SecurityPostureService.report` in
`src/commitguard/governance/posture.py`; handler in
`src/commitguard/api/governance.py`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| `GET /api/v1/organizations/{organization_id}/reports/{kind}` | `security:read`; `audit:read` for `policy_changes` | `search` |

## GET /api/v1/organizations/{organization_id}/reports/{kind}

| Parameter | In | Values | Default |
|---|---|---|---|
| `kind` | path | `compliance`, `coverage`, `violations`, `exceptions`, `policy_changes`, `installations` | |
| `format` | query | `json`, `csv` | `json` |

Checks, in this order:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not a member / lacks `security:read` |
| 400 | `VALIDATION_ERROR` (`field` `report`) | unknown `kind` |
| 400 | `VALIDATION_ERROR` (`field` `format`) | "format must be json or csv" |
| 404 | `NOT_FOUND` | `policy_changes` without `audit:read` |

**The response is a file, not the `{data, meta}` envelope.** Errors still use the
JSON error format.

Headers:

- `Content-Disposition: attachment; filename="commitguard-<login>-<kind>-<YYYYMMDDTHHMMSSZ>.<format>"`
- `Content-Type: application/json` for JSON, `text/csv; charset=utf-8` for CSV

**JSON** has these top-level keys, with indentation and sorted keys: `report`,
`organization`, `organization_id`, `generated_at`, `generated_by`, `notice`,
`summary`, `rows`.

**CSV** layout:

1. The first line is `# <notice>`.
2. The second line is `# organization=... generated_at=... report=...`.
3. The third line is the header row: the sorted column names, or `empty` when
   there are no rows.
4. Data rows follow.

Nested values are JSON-encoded. Cells starting with `=`, `+`, `-`, `@`, tab or
carriage return are prefixed with `'`.

At most 10,000 rows are exported.

| `kind` | Rows | `summary` keys |
|---|---|---|
| `compliance`, `coverage` | One per repository listed for the session: `repository`, `posture`, `reasons`, `protection`, `connection`, `mode`, `onboarding`, `policy_state`, `organization_policy_version`, `open_violations`, `critical_open`, `active_exceptions`, `drift`, `last_scan_at` | `posture`, `repositories`, `protected`, `at_risk`, `unprotected`, `critical_findings`, `active_exceptions`, `compliance` |
| `violations` | Counts grouped by `rule`, `severity`, `action`, `status`, `repository`, in column `open` (it counts every status in the group) | `groups`, `truncated` |
| `exceptions` | `id`, `rule`, `scope`, `scope_id`, `action`, `status`, `reason`, `requested_by`, `approved_by`, `expires_at`, `permanent`; repository exceptions for repositories not listed for the session are left out | `exceptions`, `truncated` |
| `policy_changes` | Policy and settings audit events: `occurred_at`, `type`, `actor`, `details` | `changes`, `truncated` |
| `installations` | Installation health, organization-wide | `installations` |

Side effect: a `report_exported` audit event.

```bash
curl -s -OJ 'https://commitguard.example.com/api/v1/organizations/5001/reports/compliance?format=csv' \
  -H 'Cookie: __Host-commitguard_session=<session-token>'
```
