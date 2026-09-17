# Repository groups

A repository group is a named set of repositories in one organization, for
example "Production" or "Mobile". Groups can have their own policy and
exceptions. Groups are archived, never deleted. For the concepts, see
[../repository-management.md](../repository-management.md).

Source: `src/commitguard/governance/groups.py`; handlers in
`src/commitguard/api/governance.py`.

| Method and path | Permission | Rate-limit category |
|---|---|---|
| [`GET /api/v1/organizations/{organization_id}/repository-groups`](#get-apiv1organizationsorganization_idrepository-groups) | `repositories:read` | `read` |
| [`POST /api/v1/organizations/{organization_id}/repository-groups`](#post-apiv1organizationsorganization_idrepository-groups) | `repositories:manage` | `write` |
| [`GET /api/v1/repository-groups/{group_id}`](#get-apiv1repository-groupsgroup_id) | `repositories:read` | `read` |
| [`PATCH /api/v1/repository-groups/{group_id}`](#patch-apiv1repository-groupsgroup_id) | `repositories:manage` | `write` |
| [`DELETE /api/v1/repository-groups/{group_id}`](#delete-apiv1repository-groupsgroup_id) | `repositories:manage` | `sensitive` |
| [`POST /api/v1/repository-groups/{group_id}/repositories`](#post-apiv1repository-groupsgroup_idrepositories) | `repositories:manage` | `write` |
| [`POST /api/v1/repository-groups/{group_id}/repositories/remove`](#post-apiv1repository-groupsgroup_idrepositoriesremove) | `repositories:manage` | `write` |

A non-member gets `404 NOT_FOUND`, and a member without the permission gets
`403 FORBIDDEN`. For `/repository-groups/{group_id}`, the organization is taken
from the stored group. An unknown group is `404`. `group_id` is 32 lowercase
hexadecimal characters.

## Resource models

**RepositoryGroupView**

| Field | Type | Description |
|---|---|---|
| `id` | string | |
| `organization_id` | integer | |
| `name` | string | |
| `description` | string or null | |
| `repository_count` | integer | All members, including repositories not listed for the session |
| `policy_version` | integer | `0` when the group has no policy |
| `active_exceptions` | integer | |
| `created_at`, `updated_at` | string (date-time) | |
| `created_by` | string | |
| `archived_at` | string (date-time) or null | |

**RepositoryGroupDetail**

| Field | Type | Description |
|---|---|---|
| `group` | object | `RepositoryGroupView` |
| `repositories` | array | `{repository_id, full_name, added_at, added_by}` for members listed for the session, by name |
| `hidden_repositories` | integer | Members not listed for the session |
| `can_manage` | boolean | Whether the caller has `repositories:manage` |

## GET /api/v1/organizations/{organization_id}/repository-groups

| Query parameter | Description |
|---|---|
| `archived` | `true` includes archived groups |

Returns up to 1,000 groups, ordered by name. The list is not paginated.

Response `200`. `data` is an array of `RepositoryGroupView`.

## POST /api/v1/organizations/{organization_id}/repository-groups

| Body field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | At most 100 characters |
| `description` | string | no | At most 500 characters |

Errors:

| Status | Code | When |
|---|---|---|
| 404 / 403 | `NOT_FOUND` / `FORBIDDEN` | not a member / lacks `repositories:manage` |
| 400 | `VALIDATION_ERROR` | missing or invalid `name`, invalid `description` |
| 409 | `CONFLICT` | "An organization can have at most 500 groups." |
| 409 | `CONFLICT` | "A group with this name already exists." (case and repeated spaces ignored; active groups only) |

Response `201`. `data` is a `RepositoryGroupView`.

Side effect: a `repository_group_created` audit event.

## GET /api/v1/repository-groups/{group_id}

Also returns archived groups.

Response `200`. `data` is a `RepositoryGroupDetail`.

## PATCH /api/v1/repository-groups/{group_id}

| Body field | Type | Required | Description |
|---|---|---|---|
| `name` | string | no | At most 100 characters; omitted or `null` keeps the name |
| `description` | string | no | At most 500 characters; omitted or `null` keeps it, blank clears it |

Errors: `404`; `403`; `409 CONFLICT` ("An archived group cannot be changed.");
`400 VALIDATION_ERROR` (invalid text, or "Nothing to change."); `409 CONFLICT`
(duplicate name).

Response `200`. `data` is the updated `RepositoryGroupView`.

Side effect: a `repository_group_updated` audit event.

## DELETE /api/v1/repository-groups/{group_id}

Archives the group. The body is not read.

| Query parameter | Description |
|---|---|
| `confirm` | `true` is required when the group has a policy or active exceptions |

Errors: `404`; `403`; `409 CONFLICT` ("The group is already archived.");
`409 CONFIRMATION_REQUIRED` when confirmation is needed and missing.

Response `200`: `{"data": {"archived": true}, "meta": {}}`.

Side effects:

- Membership rows are kept.
- The group's exceptions end: requested exceptions are cancelled, and active
  exceptions are revoked.
- The effective policy of the group's repositories is marked stale.
- A `repository_group_archived` audit event is written.

```bash
curl -s -X DELETE 'https://commitguard.example.com/api/v1/repository-groups/9a1b2c3d4e5f60718293a4b5c6d7e8f9?confirm=true' \
  -H 'Cookie: __Host-commitguard_session=<session-token>' \
  -H 'Origin: https://commitguard.example.com' \
  -H 'X-CSRF-Token: <csrf-token>'
```

## POST /api/v1/repository-groups/{group_id}/repositories

Adds repositories. Repositories that are already members are ignored.

| Body field | Type | Required | Description |
|---|---|---|---|
| `repository_ids` | array of integers | yes | 1 to 5,000 IDs, all in the group's organization and listed for the session |

Errors: `404`; `403`; `400 VALIDATION_ERROR` (`field` `repository_ids`);
`404 NOT_FOUND` ("N of the selected repositories were not found in this
organization."); `409 CONFLICT` ("An archived group cannot be changed.").

Response `200`. `data` is the updated `RepositoryGroupDetail`.

Side effects when something was added: a `repository_group_members_added` audit
event; the effective policy of the added repositories is marked stale.

## POST /api/v1/repository-groups/{group_id}/repositories/remove

Removes repositories. The body is the same as for adding. Repositories that
are not members are ignored. Removal from an archived group is allowed.

Errors: `404`; `403`; `400 VALIDATION_ERROR`; `404 NOT_FOUND` for repositories
outside the organization or not listed for the session.

Response `200`. `data` is the updated `RepositoryGroupDetail`.

Side effects when something was removed: a `repository_group_members_removed`
audit event; the effective policy of the removed repositories is marked stale.
