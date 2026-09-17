# Rules

**Bundled rules** are the detection rules that ship with CommitGuard. They
are read-only. **Organization rules** are extra AI agent and bot identities
that an organization adds as data. They are versioned, and each version is
immutable.

Source: `src/commitguard/controlplane/rules.py` (bundled),
`src/commitguard/governance/rules.py` (organization); handlers in
`src/commitguard/api/app.py` and `src/commitguard/api/governance.py`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| [`GET /api/v1/rules`](#get-apiv1rules) | `rules:read` | `read` |
| [`GET /api/v1/rules/{rule_id}`](#get-apiv1rulesrule_id) | `rules:read` | `read` |
| [`GET /api/v1/organizations/{organization_id}/rules`](#get-apiv1organizationsorganization_idrules) | `rules:read` | `read` |
| [`PUT /api/v1/organizations/{organization_id}/rules`](#put-apiv1organizationsorganization_idrules) | `rules:manage` | `sensitive` |
| [`GET /api/v1/organizations/{organization_id}/rules/history`](#get-apiv1organizationsorganization_idruleshistory) | `rules:read` | `read` |

## GET /api/v1/rules

Lists the bundled rules. The route requires `rules:read` in at least one
organization; otherwise the answer is `403 FORBIDDEN`. It is not paginated.

Response `200`. `data` is an array of `RuleView`:

| Field | Type | Description |
|---|---|---|
| `id` | string | |
| `name` | string | |
| `description` | string | |
| `detector` | string | |
| `severity` | string | |
| `default_action` | string | `allow`, `warn` or `block` |
| `source` | string | Always `bundled` |
| `trusted` | boolean | Always `true` |
| `status` | string | Always `active` |
| `rules_version` | string | First 12 characters of the bundled rules fingerprint |
| `tool_version` | string | CommitGuard version |

The bundled rules are:

| `id` | `detector` | `severity` | `default_action` |
|---|---|---|---|
| `ai_coauthor` | `coauthor` | `high` | `block` |
| `ai_identity` | `identity` | `high` | `block` |
| `ai_trailer` | `trailer` | `high` | `block` |
| `malformed_trailer` | `trailer` | `low` | `warn` |
| `bot_identity` | `bot` | `low` | `warn` |

## GET /api/v1/rules/{rule_id}

`rule_id` must match `[a-z][a-z0-9_]{0,63}`.

Response `200`. `data` is a `RuleDetail`:

| Field | Type | Description |
|---|---|---|
| `rule` | object | `RuleView` |
| `remediation` | array of strings | |
| `evidence_sources` | array of strings | |
| `data_files` | array | `{name, entries}` for the bundled data files the rule uses |
| `editable` | boolean | Always `false` |

Errors: `404 NOT_FOUND` for an unknown rule.

## GET /api/v1/organizations/{organization_id}/rules

Response `200`. `data` is an `OrganizationRulesView`:

| Field | Type | Description |
|---|---|---|
| `organization_id` | integer | |
| `version` | integer | `0` while none has been saved |
| `fingerprint` | string or null | SHA-256 of the canonical document |
| `document` | object | `{ai_identities: [...], bot_identities: [...]}`; see the entry format below |
| `rules_version` | string | The bundled fingerprint, or `<first 16 characters>+org-v<version>` |
| `created_at` | string (date-time) or null | |
| `created_by` | string or null | |
| `reason` | string or null | |
| `trust_levels` | array | Three `{level, source, can}` objects describing built-in, organization and repository rules |
| `can_manage` | boolean | Whether the caller has `rules:manage` |

If a stored version fails its integrity check, `document` is returned empty
with the stored `version`.

Errors: `404 NOT_FOUND` (not a member).

## PUT /api/v1/organizations/{organization_id}/rules

Publishes a new version of the organization rules.

| Body field | Type | Required | Description |
|---|---|---|---|
| `expected_version` | integer ≥ 0 | yes | The `version` you read |
| `rules` | object | yes | The complete document |
| `reason` | string | yes | At most 500 characters |

Document format. Unknown fields are rejected.

| Field | Constraint |
|---|---|
| `ai_identities`, `bot_identities` | arrays of at most 100 entries each |
| entry `id` | `^[a-z][a-z0-9_]{0,47}$`, unique within its list |
| entry `display_name` | 1 to 128 characters |
| entry `names`, `name_prefixes`, `emails`, `github_logins` | arrays of at most 20 values, each 1 to 128 characters without control characters; trimmed and de-duplicated. At least one identifier is required per entry. |

The canonical document may be at most 65,536 bytes.

```json
{
  "expected_version": 0,
  "reason": "Internal release automation identity",
  "rules": {
    "ai_identities": [],
    "bot_identities": [
      {
        "id": "release_bot",
        "display_name": "Release bot",
        "names": ["release-bot"],
        "name_prefixes": [],
        "emails": ["release-bot@example.com"],
        "github_logins": []
      }
    ]
  }
}
```

Checks, in this order:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not a member / lacks `rules:manage` |
| 400 | `VALIDATION_ERROR` | invalid `expected_version`; `rules` not an object; invalid entry (`field` `rules.<location>`); duplicate `id`; entry without identifiers; document too large; conflict with the bundled rules; missing or invalid `reason` |
| 409 | `CONFLICT` | "The organization rules were changed by someone else (now version N)." |
| 400 | `VALIDATION_ERROR` | "The rules are identical to the current version." |

Response `200`. `data` is the new `OrganizationRulesView`.

Side effects: an `organization_rules_changed` audit event; the effective policy
of every repository in the organization is recomputed.

## GET /api/v1/organizations/{organization_id}/rules/history

Lists the organization's rule versions, newest first: at most 50. It is not paginated.

Response `200`. `data` is an array of
`{version, fingerprint, created_at, created_by, reason}`. Here `created_at` is
formatted with `+00:00` instead of `Z`.

Errors: `404 NOT_FOUND` (not a member).
