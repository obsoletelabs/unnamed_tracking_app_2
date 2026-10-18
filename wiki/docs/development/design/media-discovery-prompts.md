# Design proposal: opt-in prompts for untracked media discovery

Status: proposal for maintainer review. Nothing here is implemented. It answers
[#266](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/266) (part of
#248; upstream Rosefall #214, which had no body beyond the title). Items needing a
decision are marked **OPEN QUESTION**.

## 1. Problem and current state

The idea is a way to surface "random untracked media": titles the user does not yet
track. The task is to define where candidates come from, what "untracked" means per
media kind, how users opt in, and where presentation lives.

What exists today:

- **Random game picker.** `src/frontend/src/components/RandomGamePicker.vue` filters the
  user's own library by status, platform, genre, length and priority and picks one
  (`utils/randomPicker.ts`). It runs client-side, covers only games already in the
  library, and keeps its filters in browser `localStorage`. It is a decision aid for
  owned items, not discovery.
- **Home widgets.** `services/homeWidgets.ts` defines `CORE_HOME_WIDGETS` (including
  `random-picker`, `backlog`, `on-this-day`). Plugins add widgets through `UiHomeWidget`
  in `plugin_api/ui_contracts.py` (`home_widgets`, up to 32 per manifest, with a
  `HOME_AFTER_WIDGETS` extension slot and optional personal configuration fields that
  may not contain secrets). Widgets are account-selected.
- **Metadata providers.** Search is per provider and media type (`features/metadata/*`,
  `api/routes/metadata.py`, [metadata providers](../metadata-providers.md)). Accounts can
  opt providers out (`metadata_providers_enabled`) and order them
  (`UserScanSettings.provider_order`). Providers answer a user-entered query; the public
  metadata contract has no "browse" or "popular" operation.
- **Related-title data already exists in places:** AniList franchise and recommendations
  (`features/anime_franchise.py`, `features/metadata/anime/franchise.py`) and a
  `relations_cache` on anime.
- **Notifications.** The notification stack (`features/notification_*`) owns consent,
  routing, timing and retention and is under active work (see the
  [notification overhaul](../notification-overhaul.md), tracking #104). This proposal does
  not edit it.

## 2. Definitions

"Untracked" is kind-specific and is computed only from the user's own data:

| Kind | A candidate is untracked when |
| --- | --- |
| Game | No non-deleted `games` row in the account has a matching provider identity (`provider_ids`, game identity records) |
| Movie, TV, anime | No non-deleted row in the matching table has the candidate's `provider_ids` entry for that provider |
| Trashed items | A soft-deleted row counts as tracked for suppression, so a title removed deliberately is not suggested again |

Matching is by provider ID only. Title-string matching would wrongly equate remakes and
same-name titles. Permanently deleted items are not remembered unless the dismissal list
(section 4.3) holds them.

## 3. Goals and non-goals

Goals:

- Let a user who has explicitly enabled it see occasional suggestions of titles they do
  not track, with obvious accept, dismiss and disable controls.
- Keep suggestion origin explicit: each candidate states which source produced it.
- Reuse home widgets and the existing metadata/plugin boundary.

Non-goals:

- No recommendation engine, ML ranking or collaborative filtering.
- No new scheduler or background worker; candidates are produced on demand.
- No automatic adding, tracking or wishlisting of any title.
- No change to the random game picker, which keeps meaning "choose among what I own".
- No edits to the notification stack; notifications are an optional later phase that
  consumes the existing contract.
- No use of private data the candidate producer cannot already access.

## 4. Proposal

### 4.1 Candidate sources and ownership

| Source | Owner | Input it reads | Notes |
| --- | --- | --- | --- |
| Related titles of items the user tracks (AniList relations and recommendations; TV/movie "similar" where a provider supports it) | Host, reusing cached provider data | The user's own tracked items | Needs no new outbound calls when cached; requires the library-derived consent below |
| Provider "discover" operation | Optional provider plugin through a future additive metadata operation | Media type and optional genre only | Not in the current public contract |
| Curated or editorial list | Optional plugin | None from the user | Presentation-only plugin widget |

A producer receives only what its declared permission allows. A plugin is never handed
the library, tracked IDs or dismissals. The host compares candidates with the library
locally afterwards, so the "untracked" filter does not leak library contents to the
producer.

### 4.2 Presentation

Recommendation: a core **Home widget** (default off), because widgets are already
account-selected, have empty states, and need no scheduling. Plugins can supply
alternatives through the existing `UiHomeWidget` contract without host changes. The widget
is labelled "Suggested, not in your library" so it is not confused with the random game
picker.

```text
+--------------------------------------------+
| Discover something new       [Settings] [x] |
| Suggested, not in your library              |
| [poster]  Title (Year, Anime)               |
|           Because you track "Other Title"   |
|  [Add to wishlist] [Not interested] [Next]  |
+--------------------------------------------+
```

- "Add to wishlist" uses the normal add flow for that kind with its wishlist status and
  happens only on click.
- "Not interested" records a dismissal.
- "Next" shows another candidate and records nothing.
- The reason line names the source and, if library data was used, which tracked item, so
  the user can see exactly what was used.

### 4.3 Opt-in, dismiss, disable

A new account preference, default **off**, held with the existing user preferences
(`database/models/user_preferences.py`, `api/routes/preferences.py`) and validated and
bounded like `metadata_providers_enabled`:

- `enabled`;
- `kinds`: subset of game, movie, tv, anime;
- `sources`: subset of the available sources;
- `use_library_for_suggestions`: a separate, explicit consent to derive suggestions from
  tracked items.

Behavior:

- **Dismiss:** stored per account as a bounded set of `(kind, provider, external_id)`
  entries (cap and eviction of oldest) so a dismissed title is not shown again. The user
  can list and clear it.
- **Disable:** turning the preference off stops candidate generation immediately and
  removes the widget from Home. Clearing dismissals is a separate one-click action.
- **Per-source disable:** disabling a provider through the existing account opt-out also
  removes its candidates.
- Opt-in and dismissals are server-side, so they are consistent across devices. Only
  transient display state may use `localStorage`.

### 4.4 API sketch

```text
GET    /api/discovery/candidate?kind=anime   -> one candidate, or 204
POST   /api/discovery/dismissals             -> {kind, provider, external_id}
GET    /api/discovery/dismissals             -> bounded list
DELETE /api/discovery/dismissals             -> clear all
```

`candidate` returns 403 unless the preference is enabled, produces at most one candidate
per request, reuses the metadata pacing and deadlines, and returns `source` and `reason`
fields. There is no push and no polling loop.

### 4.5 Notifications (optional, later)

If notifications are ever proposed, they would be a new notification **type** registered
through the existing contract after #104 lands, reusing its consent, routing, timing and
retention behavior: separate opt-in per destination, the user's timing preferences, the
standard inbox retention setting, and no second preference system. A low-frequency digest
style type is the most that is suggested. No new scheduler is assumed, so which existing
job could emit such an event is an OPEN QUESTION.

## 5. Privacy and abuse considerations

- Default off; library-derived suggestions require a second explicit switch.
- No title is added, tracked, wishlisted or notified without a user click or a separately
  enabled notification type.
- Plugins never receive library data. The untracked filter runs in the host.
- Candidates and dismissals are visible to and deletable by the user. Dismissals hold only
  provider IDs, no titles or free text.
- Outbound requests carry only media type and filters. If a provider fails, the widget
  shows an empty state rather than switching sources silently.
- The endpoint is authenticated, owner-scoped and rate limited through existing metadata
  pacing; it returns nothing when disabled.
- Age ratings: candidates with unknown age rating are excluded (OPEN QUESTION 6).
- Delegated integrations and API keys do not get this endpoint without a new scope.

## 6. Alternatives

| Option | Benefit | Cost |
| --- | --- | --- |
| Extend the random picker to untracked titles | One component | Conflates owned and unknown titles, which the issue warns against |
| Plugin-only | No host change | A plugin cannot know untracked state without library access, which it must not have |
| Notification-first | Reaches users proactively | Depends on #104, risks noise, no independent consent model |
| Recommendation engine | Better relevance | Explicitly excluded; new infrastructure and privacy surface |

## 7. Phasing

1. Preference, dismissals, `candidate` endpoint using cached related-title data only
   (no new provider operations), and a core Home widget. Anime first, as its related-title
   data already exists.
2. Movies and TV where a configured provider exposes similar titles.
3. Additive public contract for a provider "discover" operation and an example plugin
   widget in the plugins repository.
4. Optional notification type after #104 has landed.

## 8. Test strategy

- Untracked computation per kind, including trashed items and provider-ID versus title
  matching.
- Preference gating: 403 when disabled and no outbound calls when disabled.
- Owner scoping of dismissals; cap and eviction; clear-all.
- No auto-add: a candidate request creates no row in any library table (asserted by
  counts).
- A fake plugin producer cannot read library identifiers (permission test).
- Frontend: widget empty, disabled and loading states; Vitest for dismiss and wishlist
  actions; lint, format and typecheck.
- If a table is used for dismissals: migration upgrade, downgrade and single-head check.

## 9. OPEN QUESTIONS for the maintainer

1. Is a core Home widget (default off) the right presentation, with plugins free to add
   their own? Recommendation: yes.
2. Which kinds ship first? Recommendation: anime, because related-title data is already
   cached, then movies and TV.
3. Is the separate "use my library for suggestions" switch desirable, or too much
   friction? Recommendation: keep it separate.
4. Store dismissals in a new table or in a bounded JSON preference? Recommendation: a
   bounded JSON preference for phase 1 (no migration), revisit if it grows.
5. Should a public "discover" operation be added to the metadata plugin contract, and in
   which version? Recommendation: defer to phase 3.
6. How should candidates with unknown age rating be treated? Recommendation: exclude them.
7. Should notifications be offered at all? If yes, which existing job (if any) may emit
   the event, given that no new scheduler is assumed? Recommendation: defer until #104
   lands and treat it as a separate decision.
8. If books are accepted (see [book tracking](book-tracking.md)), should they become a
   discovery kind? Recommendation: not initially.

## 10. Acceptance mapping

| #266 criterion | Where addressed |
| --- | --- |
| Examples, candidate-source ownership, explicit opt-in/dismiss/disable | Sections 2 and 4.1 to 4.3 |
| Nothing silently added/tracked/recommended from private data the producer cannot access | Sections 3, 4.1 and 5 |
| Notifications use the existing consent/routing/timing/retention contract after #104 | Section 4.5 |
| Phases, validation, unresolved choices with recommendations; no engine or scheduler | Sections 3, 7, 8 and 9 |
