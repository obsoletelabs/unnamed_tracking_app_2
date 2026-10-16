# API

The backend is a FastAPI application and generates its OpenAPI documentation from the running code.

## Interactive documentation

For a running instance, open:

```text
https://<your-host>/api/docs
```

The exact endpoints and request/response schemas shown there are generated from the installed application, so this is the authoritative API reference for that deployment.

## Authentication

The normal web application authenticates with a server-side session cookie.

API clients can also authenticate with a user API key:

```http
Authorization: Bearer utk_<secret>
```

User API keys are created for individual accounts and can be revoked. The backend hashes the key for persistence rather than storing the full secret.

The game API and other authenticated routes resolve the caller from either the bearer API key or the normal session cookie.

## Title protection

Library PATCH requests distinguish missing fields, nulls and real values. Omitted
fields retain their current value. A `null` for a required movie, TV or anime column
(such as title, status, favorite, provider IDs or a list) also retains its value.
A nullable column can be explicitly cleared with `null`. Zero, `false`, empty lists
and empty provider-ID objects are applied. Games retain their existing exception:
clearing `sort_title` derives it again from the title. Media `sort_title: null` keeps
the current ordering name; updating the title without a sorting name derives one.

`title_lock` is a protection control, not a nullable database column: it requires
an explicit JSON boolean when supplied. Null still returns `422`, and the actor
restrictions below continue to apply.

`PATCH /api/game/update/{id}`, `/api/movie/update/{id}`, `/api/tv/update/{id}`,
and `/api/anime/update/{id}` accept an optional JSON boolean `title_lock`:

- Omitted: preserve existing protection. A manual title change still automatically locks it.
- `true`: protect the title, including when its text is unchanged.
- `false`: remove protection. A title change and protection change in the same request are atomic.

Protection is stored in `locked_fields` as `"title"`. Providers, imports, and
background metadata updates preserve protected fields. Only a valid application
sign-in session (password or OIDC) may explicitly change protection or edit an
already-protected title. API keys may still edit an unprotected title; that edit
automatically protects it. Sending an unchanged title with an ordinary update is allowed.

**Title protection is enforced by the backend. An API client cannot remove
protection or modify a protected title merely by supplying `title_lock=false`
or another request parameter.** These attempts return `403 Forbidden`. API-key
authentication takes precedence even when a valid session cookie is also supplied.
User-Agent, Origin, Referer, and client-defined UI headers grant no authority.

Plugin credentials do not establish application sign-in sessions. Existing
`media.write` and `api.full` grants do not authorize protected-title management:
direct plugin sync title edits are forbidden, while metadata enrichment preserves
the title. There is currently no plugin protected-metadata capability.

For new mutation paths, use the shared metadata locking service. Ordinary edits
must pass the authenticated actor to `apply_updates_with_locking`; provider and
import updates must use `apply_metadata_updates`. Load existing rows under a
database row lock before checking protection, so a concurrent UI save cannot
leave a worker using stale lock state. Never assign a persisted library title
directly or accept a replacement `locked_fields` list from a caller.

## User preferences

User preferences are exposed through the existing preferences API. The AniList import adds these per-user fields:

| Field | Type | Purpose |
| --- | --- | --- |
| `anilist_import_username` | string | Public AniList username to import. Empty by default; maximum 100 characters. |
| `anilist_import_update_existing` | boolean | Allows matching existing titles to be updated from AniList. Defaults to `false`. |

The preferences are scoped to individual users rather than deployments.

The import schedule (enabled state, interval of 60 to 43,200 minutes, last run and last result) is not a preference. It is the personal task `user-anilist:<uuid>` returned by `GET /api/settings/user-jobs` and changed with `PUT /api/settings/user-jobs/{job_id}` or `POST /api/settings/user-jobs/{job_id}/run`. The former `anilist_import_enabled`, `anilist_import_interval_minutes` and `anilist_import_last_run_at` preferences are no longer accepted; a migration moves saved values into the task.

## Media trash

Movies, TV shows and anime share one trash life cycle (`api/routes/media_common.py`):
`DELETE /api/{kind}/delete/{id}` soft-deletes, `GET /api/{kind}/trash` lists,
`POST /api/{kind}/{id}/restore` restores and `DELETE /api/{kind}/{id}/purge`
permanently deletes one trashed title. `{kind}` is `movie`, `tv` or `anime`.

`POST /api/{kind}/trash/empty` permanently deletes the caller's titles in that
kind's trash:

```json
{"confirm": true, "deleted_through": 1792185461}
```

- `confirm` must be the JSON boolean `true`; a missing, `false`, `1` or `"true"`
  value is rejected with 422 before anything is deleted.
- `deleted_through` (optional Unix seconds) is the newest deletion time the user
  saw when confirming. Titles trashed later are kept. Omitted, it means now.
- Only the caller's rows with `deleted_at` set and at or before
  `deleted_through` qualify, oldest first, at most 200 per request. Rows are
  locked before deletion, so a concurrent restore either wins (the title is
  skipped) or waits. Each title is deleted with its seasons and episodes in its
  own savepoint: a database failure rolls back only that title, which remains
  in the trash and is reported.

```json
{
  "deleted": 199,
  "failed": 1,
  "failed_items": [{"id": "…", "title": "Dark", "deleted_at": 1792000000}],
  "remaining": 3,
  "deleted_through": 1792185461
}
```

`remaining` counts what is still in the trash up to `deleted_through`, failed
titles included. Repeat the request with the same `deleted_through` while
`remaining` is larger than `failed`; the frontend does this automatically.
Purging keeps the existing per-title semantics: activity history and list
membership rows are not rewritten.

The `media_trash_retention_days` preference (`0`, `7`, `14`, `30` or `90`;
default `0`, Never) opts an account into automatic deletion. Any other value,
including `true`, `30.0` and `"30"`, is rejected with 422. The existing trash
sweep (`features/trash/sweep.py`, at startup and every six hours) calls
`purge_expired_media_titles` in `features/trash/media_titles.py` after the game
file pass. It selects accounts whose stored value is one of the offered periods,
re-reads the choice under a share lock inside each account's transaction, and
purges titles whose `deleted_at` is at least that many days old through the same
per-title function as Empty trash. Work is bounded to 200 titles per kind and
account and 2,000 titles per sweep. A failed account transaction is logged and
retried by the next sweep. The game-file trash and its fixed seven-day retention
are separate and unchanged.

## Playnite API surface

The Playnite extension currently uses these authenticated endpoints:

- `GET /api/game/list`
- `POST /api/game/create`
- `PATCH /api/game/update/{game_id}`
- `POST /api/game/{game_id}/assets/{asset_kind}`

See [Playnite](../integrations/playnite.md) for the integration-specific behavior.

## OIDC API surface

The OIDC login flow is exposed under `/api/auth/oidc`:

- `GET /api/auth/oidc/status` — reports whether OIDC is available and the login providers exposed on the login page.
- `GET /api/auth/oidc/login` — starts the default OIDC login.
- `GET /api/auth/oidc/login/{provider_slug}` — starts a named provider when its autostart policy permits it.
- `GET /api/auth/oidc/callback` — receives the default provider callback.
- `GET /api/auth/oidc/callback/{provider_slug}` — receives a named-provider callback.

OIDC callback handling creates or links a local account and then creates the application's normal session.

## Configuration API

The setup/configuration API is generated from the backend configuration registry. It is used by the generic setup UI and by the post-install configuration editor.

The configuration schema deliberately masks secret values. Environment-owned fields are reported as locked rather than allowing the browser to override deployment configuration.

For the configuration model and environment precedence, see [Configuration](../setup/configuration.md).

## API changes

When changing an API route:

1. Update its Pydantic request/response models where needed.
2. Keep authentication and user scoping explicit.
3. Add or update backend tests.
4. Verify the generated `/api/docs` output.
5. Update integration documentation when a documented client such as Playnite depends on the contract.
