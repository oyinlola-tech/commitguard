# Pagination, filtering and sorting

Source: `src/commitguard/controlplane/pagination.py`; list handlers in
`src/commitguard/api/app.py` and `src/commitguard/api/governance.py`.

## Paginated lists

A paginated list accepts `limit` and `cursor` query parameters and returns
`meta.next_cursor`:

```json
{ "data": [ ], "meta": { "next_cursor": "WzI1XQ", "limit": 25 } }
```

| Parameter | Rules |
|---|---|
| `limit` | Integer from 1 to 100; default 25. Anything else: `400 VALIDATION_ERROR`, `field` `limit`. |
| `cursor` | The `next_cursor` of the previous page. Omit it (or send it empty) for the first page. |

- **Last page.** `next_cursor` is `null`.
- **Opaque cursors.** A cursor is at most 512 URL-safe characters. Clients must
  pass it back unchanged and must not construct or interpret it: its encoding
  can change. A malformed cursor answers `400 VALIDATION_ERROR` ("invalid
  cursor", `field` `cursor`).
- **Cursors grant nothing.** A cursor only encodes a position. Every page is
  filtered by the caller's access at the time of the request.
- **Same filters and sort.** Send the same filter and `sort` parameters with
  every page. The server does not check that they match the cursor.

There are two kinds of cursor.

**Keyset cursors** encode the sort key of the last row. Pages stay stable while
new rows arrive. On `GET /api/v1/violations`, a cursor from a different `sort`
usually fails with "invalid cursor".

**Offset cursors** encode a position. Rows inserted or removed between requests
can shift the pages. The largest offset accepted is 100,000.

| Endpoint | Cursor |
|---|---|
| `GET /api/v1/scans` | keyset |
| `GET /api/v1/violations` | keyset |
| `GET /api/v1/audit` | keyset |
| `GET /api/v1/notifications` | keyset |
| `GET /api/v1/organizations/{organization_id}/notification-deliveries` | keyset |
| `GET /api/v1/repositories` | offset |
| `GET /api/v1/organizations/{organization_id}/members` | offset |
| `GET /api/v1/policies/{organization_id}/versions` | offset |
| `GET /api/v1/organizations/{organization_id}/policy-targets/{target_type}/versions` | offset |
| `GET /api/v1/github/installations/{installation_id}/repositories` | offset |
| `GET /api/v1/organizations/{organization_id}/security/repositories` | offset; `meta` also has `total` and `computed_at` |
| `GET /api/v1/organizations/{organization_id}/exceptions` | offset |

## Lists without pagination

These lists take no `limit` or `cursor`. Each has a fixed maximum size:

| Endpoint | Maximum items |
|---|---|
| `GET /api/v1/auth/sessions` | 50 |
| `GET /api/v1/scans/{scan_id}/executions` | 100 executions |
| `GET /api/v1/organizations/{organization_id}/security/events` | 50 |
| `GET /api/v1/organizations/{organization_id}/security/exceptions` | 500 |
| `GET /api/v1/organizations/{organization_id}/search` | 50 |
| `GET /api/v1/organizations/{organization_id}/repository-groups` | 1,000 |
| `GET /api/v1/organizations/{organization_id}/policy-drafts` | 200 |
| `GET /api/v1/organizations/{organization_id}/simulations` | 50 |
| `GET /api/v1/organizations/{organization_id}/rollouts` | 100 |
| `GET /api/v1/organizations/{organization_id}/rules/history` | 50 |
| `GET /api/v1/organizations/{organization_id}/bulk-operations` | 50 (items are loaded only by the detail endpoint, up to 500) |
| `GET /api/v1/organizations/{organization_id}/scan-schedules` | 100 |
| `GET /api/v1/scan-schedules/{schedule_id}` | 20 runs |

`GET /api/v1/organizations`, `GET /api/v1/policies`, `GET /api/v1/rules`,
`GET /api/v1/github/installations` and `GET /api/v1/notification-preferences`
return one item per accessible organization, installation or rule.

## Filtering

Filtering always happens on the server.

- **Enumerations** come from fixed lists. A value outside the list answers `400`
  with the allowed values in the message, for example
  "sort must be one of: name, recent, risk".
- **IDs** are positive integers of at most 16 digits (GitHub IDs) or
  32 lowercase hexadecimal characters (CommitGuard resource IDs).
- **`from` and `to`** are ISO 8601 dates or date-times of at most 40
  characters. A value without a time zone is read as UTC. `from` is inclusive
  and `to` is exclusive.
- **Empty values.** For most filters an empty value is treated as absent. On
  `GET /api/v1/organizations/{organization_id}/exceptions` and `.../policy-drafts`
  an empty value is rejected.
- **`q` (free-text search).**
  - Whitespace is collapsed. The value may be at most 100 characters and may not
    contain control characters.
  - It matches as a literal substring; `%` and `_` have no special meaning.
  - On scans and violations, a value of 4 to 64 hexadecimal characters also
    matches commit SHA prefixes.
  - Organization search (`/organizations/{organization_id}/search`) requires 2
    to 100 characters.
- **Query strings** may have at most 32 parameters and 4,096 characters. A
  parameter may not be repeated.

## Sorting

`sort` accepts a fixed set of keys per endpoint. A query parameter never names a
database column.

| Endpoint | `sort` values | Default |
|---|---|---|
| `GET /api/v1/repositories` | `name`, `risk`, `recent` | `name` |
| `GET /api/v1/scans` | `newest`, `oldest` | `newest` |
| `GET /api/v1/violations` | `newest`, `oldest`, `severity`, `repository` | `newest` |
| `GET /api/v1/audit` | `newest`, `oldest` | `newest` |
| `GET /api/v1/organizations/{organization_id}/security/repositories` | `posture`, `name`, `violations`, `last_scan` | `posture` |

Other lists have a fixed order, which each endpoint page describes.
