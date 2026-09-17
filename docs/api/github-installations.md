# GitHub installations

Installations of the CommitGuard GitHub App, their repositories and
synchronization with GitHub. Installations are created and removed by GitHub
webhooks (see [webhooks.md](webhooks.md)), not through this API. For setup, see
[../github-app.md](../github-app.md).

Source: handlers in `src/commitguard/api/app.py`; `DashboardQueries` and
`ControlPlaneCommands.sync_installation` in `src/commitguard/controlplane/`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| [`GET /api/v1/github/installations`](#get-apiv1githubinstallations) | `repositories:read` | `read` |
| [`GET /api/v1/github/installations/{installation_id}`](#get-apiv1githubinstallationsinstallation_id) | `repositories:read` | `read` |
| [`GET /api/v1/github/installations/{installation_id}/repositories`](#get-apiv1githubinstallationsinstallation_idrepositories) | `repositories:read` | `read` |
| [`POST /api/v1/github/installations/{installation_id}/sync`](#post-apiv1githubinstallationsinstallation_idsync) | `github:manage` | `github` |

An installation is visible when its organization grants `repositories:read`
and GitHub listed the installation for the session. Otherwise the answer is
`404 NOT_FOUND`.

## InstallationView

| Field | Type | Description |
|---|---|---|
| `id` | integer | GitHub installation ID |
| `account` | object | `{id, login, type}` |
| `status` | string | `connected`, `suspended` or `disconnected` |
| `repository_selection` | string | As reported by GitHub |
| `repositories` | integer | |
| `permissions` | object | Permission name → level, as granted on GitHub |
| `missing_permissions` | array of strings | `"<name>: <level>"` |
| `excessive_permissions` | array of strings | `"<name>: <level>"` |
| `installed_at`, `updated_at` | string (date-time) | |
| `last_event_at` | string (date-time) or null | |
| `github_settings_url` | string | |
| `can_manage` | boolean | Whether the caller has `github:manage` |

## GET /api/v1/github/installations

Lists visible installations, ordered by account login. It is not paginated.

Response `200`. `data` is an array of `InstallationView`.

## GET /api/v1/github/installations/{installation_id}

Response `200`. `data` is `{installation, recent_events}`:

- `installation` is an `InstallationView`.
- `recent_events` holds [`AuditEventView`](audit.md#auditeventview) items. It is
  filled only for callers with `audit:read`. It takes the organization's 10
  newest events and keeps those for this installation or for no installation,
  so it can hold fewer than 10.

Errors: `404 NOT_FOUND`.

## GET /api/v1/github/installations/{installation_id}/repositories

Repositories of the installation that GitHub listed for the session. Connected
repositories come first, then the list is ordered by name.

| Query parameter | Description |
|---|---|
| `cursor`, `limit` | Offset cursor; see [pagination.md](pagination.md) |

Response `200`. `data` is an array of
`{id, full_name, connected, monitoring_enabled, added_at, removed_at}`. `meta` is
`{next_cursor, limit}`.

Errors: `404 NOT_FOUND`.

## POST /api/v1/github/installations/{installation_id}/sync

Reads the installation and its repository list from GitHub now, and replaces
the stored list. This endpoint takes no body.

| Status | Code | When |
|---|---|---|
| 404 | `NOT_FOUND` | installation not visible |
| 403 | `FORBIDDEN` | caller lacks `github:manage` in the installation's organization |
| 409 | `CONFLICT` | "GitHub denied access: <detail>", for example when the installation was removed or suspended |
| 502 | `GITHUB_UNAVAILABLE` | "GitHub could not be reached. Try again later." |

Response `200`. `data` is a `SyncResult`:

| Field | Type |
|---|---|
| `installation_id` | integer |
| `repositories` | integer |
| `added` | array of strings (full names) |
| `removed` | array of strings (full names) |
| `synced_at` | string (date-time) |

Side effects:

- A `repositories_synced` audit event.
- Newly discovered repositories go through organization onboarding, which may
  write `repository_discovered`, `repository_onboarded` or
  `repository_excluded` events.

This route uses the `github` rate-limit category: 10 requests per minute per
user. See [rate-limits.md](rate-limits.md).

```bash
curl -s -X POST https://commitguard.example.com/api/v1/github/installations/42/sync \
  -H 'Cookie: __Host-commitguard_session=<session-token>' \
  -H 'Origin: https://commitguard.example.com' \
  -H 'X-CSRF-Token: <csrf-token>'
```
