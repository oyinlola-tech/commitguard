# Authentication

The `/api/v1` API authenticates people, not programs. A caller is a GitHub user
who signed in through the GitHub App's user authorization flow and holds a
server-side session identified by a cookie.

There are no API keys, personal access tokens, bearer tokens or service
accounts. Every protected endpoint reads the session cookie and nothing else.
Scripts can call the API with the session cookie and CSRF token of a signed-in
user, but no separate credential for automation exists.

Contents:

- [Sign-in flow](#sign-in-flow)
- [Sessions](#sessions)
- [CSRF protection](#csrf-protection)
- [CORS](#cors)
- [Re-authentication for sensitive changes](#re-authentication-for-sensitive-changes)
- [Endpoints](#endpoints)

Source: `src/commitguard/controlplane/identity.py`, `src/commitguard/api/app.py`,
`src/commitguard/api/settings.py`.

## Sign-in flow

Sign-in uses the OAuth web flow of the GitHub App, with PKCE (`S256`).
CommitGuard never asks for a password.

```text
browser ── GET /api/v1/auth/login?return_to=/violations
        ◄─ 302 to https://github.com/login/oauth/authorize?client_id=…&state=…&code_challenge=…
           Set-Cookie: __Host-commitguard_oauth_state=<state>; Max-Age=600
browser ── user approves on GitHub
        ── GET /api/v1/auth/callback?code=…&state=…
           state must equal the cookie and match an unexpired, unused server record
           code + PKCE verifier  → user access token
           GET /user, GET /user/installations, GET /user/installations/{id}/repositories
           token discarded; session stored with the installation and repository lists
        ◄─ 302 to return_to
           Set-Cookie: __Host-commitguard_session=<session-token>; Max-Age=28800
```

What the server records at sign-in, and what it means for later requests:

- **User.** The GitHub user ID and login from `GET /user`.
- **Installations.** Installations of this GitHub App that GitHub lists for the
  user. An installation whose stored account does not match what GitHub
  reports, or that is marked deleted, is skipped.
- **Repositories.** For each installation, the repositories GitHub lists for the
  user (at most 10,000 per installation).
- **The user access token is not stored.** It is used for the calls above and
  then discarded.

Changes to a user's GitHub access take effect at their next sign-in. Changes to
their CommitGuard role take effect on their next request, because memberships
are read again for every request.

The OAuth `state` record is valid for 10 minutes and can be used once.

`return_to` must be a same-origin application path. The server replaces it
with `/dashboard` when it is missing, longer than 512 characters, does not
start with `/`, starts with `//` or `/api/`, contains a backslash, a control
character or a space, or has a scheme or host.

When sign-in fails, the callback does not return JSON. It redirects to the
dashboard's login page with an `error` query parameter:

| Redirect | When |
|---|---|
| `/login?error=access_denied` | GitHub returned an `error` parameter (for example, the user declined) |
| `/login?error=github_unavailable` | GitHub could not be reached, or failed while CommitGuard exchanged the code or listed the user's access |
| `/login?error=sign_in_failed` | `code`, `state` or the state cookie missing; `state` longer than 128 or `code` longer than 512 characters; state does not match the cookie; state expired or already used; GitHub rejected the code or the token |

Every failed callback also clears the state cookie.

## Sessions

| Property | Value |
|---|---|
| Cookie name | `__Host-commitguard_session` |
| Cookie attributes | `Path=/`, `Secure`, `SameSite=Lax`, `HttpOnly`, `Max-Age=28800`, no `Domain` |
| Token | `secrets.token_urlsafe(32)` (256 random bits); the database stores only its SHA-256 hash |
| Absolute lifetime | 8 hours from sign-in |
| Idle timeout | 2 hours without a request |
| Public session ID | 16 lowercase hexadecimal characters, used by the session endpoints |
| Last-seen updates | at most once a minute |

A session ends when:

- it reaches its absolute lifetime or idle timeout;
- the user signs out (`POST /api/v1/auth/logout`);
- the user revokes it (`DELETE /api/v1/auth/sessions/{session_id}`);
- the user's last membership in any organization is removed (all of that
  user's sessions are deleted).

Authentication answers:

| Situation | Status | Code |
|---|---|---|
| No cookie, or a cookie that matches no session | 401 | `UNAUTHENTICATED` |
| A session that exists but has expired or been idle too long | 401 | `SESSION_EXPIRED`; the response also clears the cookie, and the session is deleted |

A cookie value longer than 128 characters or containing non-ASCII characters is
treated as no cookie.

Route matching runs before authentication. An unknown path answers
`404 NOT_FOUND`, and a known path with the wrong method answers
`405 METHOD_NOT_ALLOWED`, even without a session.

Signing in does not require a CommitGuard role. A user without any membership
can call `GET /api/v1/auth/session` and receives an empty `organizations` list.
Endpoints that check a permission then answer `403 FORBIDDEN`. See
[authorization.md](authorization.md).

## CSRF protection

Every `POST`, `PUT`, `PATCH` and `DELETE` request must pass two checks, after
authentication and rate limiting and before authorization:

1. **Origin.** The `Origin` header must be present and equal to the dashboard
   origin (`COMMITGUARD_DASHBOARD_URL`) or one of
   `COMMITGUARD_DASHBOARD_ALLOWED_ORIGINS`. A missing `Origin`, `Origin: null` or
   any other origin fails.
2. **Token.** The `X-CSRF-Token` header must equal the session's CSRF token.

Both failures answer `403 CSRF_FAILED`, with the message "The request origin is
not allowed." or "The request is missing a valid CSRF token.".

The CSRF token is the lowercase hexadecimal SHA-256 of
`"commitguard-csrf\0" + session token`. It is bound to one session: another
user's token fails. Clients get it from the `csrf_token` field of
`GET /api/v1/auth/session`; they never compute it.

Request bodies must be JSON (`Content-Type: application/json`). A body with any
other content type answers `415 UNSUPPORTED_MEDIA_TYPE`, so an HTML form on
another site cannot submit one. The session cookie is `SameSite=Lax`, so
browsers do not send it with cross-site `POST` requests either.

## CORS

By default the API allows no cross-origin requests. The dashboard is served
from the API's own origin.

When `COMMITGUARD_DASHBOARD_ALLOWED_ORIGINS` lists extra origins (each an
explicit `http(s)` origin; `*` is refused at start-up):

- responses to requests from a listed origin carry
  `Access-Control-Allow-Origin: <that origin>`,
  `Access-Control-Allow-Credentials: true` and
  `Access-Control-Expose-Headers: X-Request-ID`;
- an `OPTIONS` preflight from a listed origin answers `204` with
  `Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE`,
  `Access-Control-Allow-Headers: Content-Type, X-CSRF-Token` and
  `Access-Control-Max-Age: 600`;
- an `OPTIONS` request from any other origin, or without `Origin`, answers
  `403 CORS_REJECTED`.

Every API response carries `Vary: Origin`. The dashboard origin itself is not
part of the CORS allow-list, because same-origin requests do not need it.

## Re-authentication for sensitive changes

Some changes require that the session was **signed in** within the last 15
minutes. The time is `authenticated_at` of the session. Using the session does
not extend it, so the user must sign in again, which creates a new session.
Otherwise the request answers `401 REAUTHENTICATION_REQUIRED` ("Sign in again to
confirm your identity before this change.").

`GET /api/v1/auth/session` returns `reauthentication_required_after`: the time
after which such changes require a new sign-in.

The endpoint pages list which operations need a recent sign-in:

- pausing repository monitoring;
- saving or publishing a policy change that weakens enforcement, including
  emergency publication;
- a policy or rollout rollback that weakens enforcement;
- relaxing organization settings;
- adding a notification webhook.

## Endpoints

| Method and path | Purpose | Session | Rate-limit category |
|---|---|---|---|
| [`GET /api/v1/auth/login`](#get-apiv1authlogin) | Start sign-in | not required | `auth` |
| [`GET /api/v1/auth/callback`](#get-apiv1authcallback) | Complete sign-in | not required | `auth` |
| [`GET /api/v1/auth/session`](#get-apiv1authsession) | Current user, organizations, CSRF token | required | `read` |
| [`POST /api/v1/auth/logout`](#post-apiv1authlogout) | Sign out | required | `write` |
| [`GET /api/v1/auth/sessions`](#get-apiv1authsessions) | List your sessions | required | `read` |
| [`DELETE /api/v1/auth/sessions/{session_id}`](#delete-apiv1authsessionssession_id) | Revoke one of your sessions | required | `write` |

None of these endpoints checks a permission.

### GET /api/v1/auth/login

Starts sign-in. It creates a single-use `state` and a PKCE verifier, stores them
for 10 minutes, and redirects to GitHub.

| Query parameter | Required | Description |
|---|---|---|
| `return_to` | no | Application path to open after sign-in. It must pass the rules in [Sign-in flow](#sign-in-flow); otherwise `/dashboard` is used. |

Response: `302 Found`, with no body.

- `Location` is `https://github.com/login/oauth/authorize` with `client_id`,
  `redirect_uri` (`<dashboard origin>/api/v1/auth/callback`), `state`,
  `code_challenge`, `code_challenge_method=S256` and `allow_signup=false`.
- `Set-Cookie` sets `__Host-commitguard_oauth_state=<state>` with `Path=/`,
  `Secure`, `SameSite=Lax`, `HttpOnly` and `Max-Age=600`.

```http
GET /api/v1/auth/login?return_to=/violations HTTP/1.1
Host: commitguard.example.com
```

```http
HTTP/1.1 302 Found
Location: https://github.com/login/oauth/authorize?client_id=<client-id>&redirect_uri=...&state=<state>&code_challenge=<challenge>&code_challenge_method=S256&allow_signup=false
Set-Cookie: __Host-commitguard_oauth_state=<state>; Path=/; Secure; SameSite=Lax; HttpOnly; Max-Age=600
```

### GET /api/v1/auth/callback

GitHub redirects the browser here. Clients do not call it directly.

| Query parameter | Description |
|---|---|
| `code` | Authorization code from GitHub (at most 512 characters) |
| `state` | The `state` from the login redirect (at most 128 characters) |
| `error` | Set by GitHub when authorization did not happen |

Success: `302 Found` to the stored `return_to` path, with two `Set-Cookie`
headers: the state cookie cleared (`Max-Age=0`), and
`__Host-commitguard_session=<session-token>` with `Path=/`, `Secure`,
`SameSite=Lax`, `HttpOnly` and `Max-Age=28800`.

Failure: `302 Found` to `/login?error=...`, as described in
[Sign-in flow](#sign-in-flow). The callback never answers with a JSON error.

Side effect: one `user_signed_in` audit event for each organization in which
the user has a membership.

### GET /api/v1/auth/session

Returns the signed-in user, the current session, the CSRF token and the
organizations the user can access.

Response `200`. `data` is a `SessionInfo`:

| Field | Type | Description |
|---|---|---|
| `user.id` | integer | GitHub user ID |
| `user.login` | string | GitHub login |
| `session` | object | The current session, in the format of [`GET /api/v1/auth/sessions`](#get-apiv1authsessions) |
| `csrf_token` | string | Value for the `X-CSRF-Token` header (64 hexadecimal characters) |
| `organizations` | array | Same items as `GET /api/v1/organizations`; see [organizations.md](organizations.md#get-apiv1organizations) |
| `reauthentication_required_after` | string (date-time) | Sign-in time plus 15 minutes |

```bash
curl -s https://commitguard.example.com/api/v1/auth/session \
  -H 'Cookie: __Host-commitguard_session=<session-token>'
```

```json
{
  "data": {
    "user": { "id": 1001, "login": "octo-admin" },
    "session": {
      "id": "3f9c2a1b7d4e8f60",
      "created_at": "2026-09-17T09:00:00Z",
      "last_seen_at": "2026-09-17T09:12:00Z",
      "expires_at": "2026-09-17T17:00:00Z",
      "user_agent": "Mozilla/5.0 ...",
      "current": true
    },
    "csrf_token": "<csrf-token>",
    "organizations": [
      {
        "organization": { "id": 5001, "login": "example-org", "type": "Organization" },
        "role": "admin",
        "implicit_role": false,
        "permissions": ["audit:read", "exceptions:approve", "..."],
        "installation_ids": [42]
      }
    ],
    "reauthentication_required_after": "2026-09-17T09:15:00Z"
  },
  "meta": {}
}
```

### POST /api/v1/auth/logout

Deletes the current session. It needs `Origin` and `X-CSRF-Token` like every
other write. The body is ignored.

Response `200`: `{"data": {"signed_out": true}, "meta": {}}`, with a
`Set-Cookie` header that clears `__Host-commitguard_session`.

Side effect: one `user_signed_out` audit event for each organization in which
the user has a membership.

```bash
curl -s -X POST https://commitguard.example.com/api/v1/auth/logout \
  -H 'Cookie: __Host-commitguard_session=<session-token>' \
  -H 'Origin: https://commitguard.example.com' \
  -H 'X-CSRF-Token: <csrf-token>'
```

### GET /api/v1/auth/sessions

Lists the signed-in user's own unexpired sessions: at most 50, most recently
used first. It is not paginated.

Response `200`. `data` is an array of `SessionView`:

| Field | Type | Description |
|---|---|---|
| `id` | string | Public session ID (16 hexadecimal characters) |
| `created_at` | string (date-time) | When the session was created |
| `last_seen_at` | string (date-time) | Last request, accurate to one minute |
| `expires_at` | string (date-time) | Absolute expiry |
| `user_agent` | string | `User-Agent` at sign-in (at most 200 characters, cleaned) |
| `current` | boolean | `true` for the session making the request |

The list is filtered by absolute expiry only. A session that has passed its
idle timeout but has not yet been purged can still appear. It cannot be used.

### DELETE /api/v1/auth/sessions/{session_id}

Revokes one of the caller's own sessions.

| Path parameter | Format |
|---|---|
| `session_id` | exactly 16 lowercase hexadecimal characters; any other value does not match the route (`404`) |

Response `200`: `{"data": {"revoked": true}, "meta": {}}`. When the revoked
session is the current one, the response also clears the session cookie.

Errors:

| Status | Code | When |
|---|---|---|
| 404 | `NOT_FOUND` | No session with this ID belongs to the caller. Another user's session is also "not found". |

Side effect: one `session_revoked` audit event for each organization in which
the user has a membership.
