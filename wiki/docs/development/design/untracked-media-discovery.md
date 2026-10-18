# Design: opt-in discovery of untracked media

Status: **proposal for review** (task [#266](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/266), part of
[#248](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/248)). Nothing here is implemented. It avoids the
notification stack under active work in [#104](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/104).

## 1. The idea, made concrete

The upstream request is one line: *ask about random untracked media*. The useful version is a small, optional prompt on
Home: "You have never added **Paprika** (2006 film). Want to look at it?" with **Look**, **Not now**, **Never suggest this**
and **Turn this off**. It must not be a recommendation engine, and it must never add anything by itself.

## 2. Discovery is not the Random picker

| | Random picker (exists) | Discovery (proposed) |
| --- | --- | --- |
| Question it answers | "Which of the games *I own* should I play tonight?" | "Is there a title *I have never looked at* worth a look?" |
| Candidate source | the user's own library, filtered by status, platform, genre, length, priority | **outside** the library, from a provider |
| Side effects | none | none until the user explicitly adds |
| Where | Home widget (`random-picker`) and the library | a separate, optional Home widget |

Keeping them apart avoids a user mistaking a suggestion for something they own, and avoids changing the picker's meaning.

## 3. What "untracked" means

A candidate is **untracked** when *no record in the user's library for that media kind matches it*, using the same matching
the application already uses for adding titles (provider ids first, then normalised title + year), and it is **not**:

* in the user's trash (a deleted title is not "never seen": it stays suppressed),
* previously dismissed ("Not now" snoozes for 30 days; "Never suggest this" suppresses it permanently),
* in a collection or list of theirs (if they put it somewhere, they have seen it).

Per kind: games (provider ids: Steam app id, IGDB id), movies (TMDB/IMDb id), TV and anime (TMDB/TVDB/AniList id).

## 4. Where candidates come from, and who owns them

Only sources the host already uses and the user has **already configured**:

| Source | Used for | Notes |
| --- | --- | --- |
| The configured core metadata provider's own "popular/trending/similar" endpoints (TMDB, AniList, IGDB) | movies, TV, anime, games | requires the same provider setup and the same network policy as search; no new credentials |
| "Franchise gaps": other entries of a series the user already tracks (`series`, AniList relations) | all | needs no extra provider call for series already known; strongest "relevant" signal that uses only the user's own data |
| A plugin that declares the (new, optional) `discovery` capability | any | optional, permissioned; **no plugin is required**, and a plugin cannot see more than a normal metadata provider can |

**Ownership.** The host owns the candidate *list*, suppression and the user's choices. A provider only answers "what exists".
No candidate is ever fetched from, or sent to, a service the user has not configured. Candidate lists are fetched on demand
when the widget is visible, cached per user for 24 h, bounded to 50, and fall back silently to nothing on failure.

## 5. Opt-in, dismiss, disable

* **Off by default.** The widget does not appear in Home until the user adds it under **Customize Home** and accepts one plain
  sentence about what it does and which provider it asks.
* Each card has **Look** (opens the normal metadata preview, *not* an add), **Not now**, **Never suggest this**.
* **Turn off** removes the widget and stops all fetching; a **Clear what I dismissed** button resets suppression.
* A per-kind switch (games / movies / TV / anime) and a per-source switch live in the widget's settings.
* Dismissals are stored per user as a small table (`discovery_dismissals`: kind, provider id, expires_at) and deleted with the
  account.

## 6. Privacy rules (non-negotiable)

1. **No silent adds.** Nothing is added to the library, a list or a collection without an explicit click on a normal Add flow.
2. **No private data the producer cannot access.** Suggestions use only the user's *own library* (to avoid duplicates and to
   find franchise gaps) and public provider data. Nothing from Playnite/Steam private inventories is sent to a provider; only
   provider ids of the candidate are compared locally.
3. **Nothing about the user leaves the server** in a candidate request: no user id, no library contents, no query derived from
   the library beyond a *series name already in the public metadata*.
4. **No ranking by behaviour profiling.** Order is the provider's own popularity ranking filtered by the rules above, or the
   franchise order.

## 7. Notifications

Not in the first phases. If proposed later, discovery notifications must use the existing routing, consent, quiet hours and
retention contract **after** the notification overhaul lands: a new notification type with its own opt-in, default off,
digest only (never immediate), and a dismissal that mirrors the widget's.

## 8. Phases and validation

1. **Contract only (no UI):** matching function "is this candidate tracked/dismissed/trashed" with tests against
   representative libraries; suppression table.
2. **Franchise gaps** for series already tracked (no provider calls), shown in a Home widget behind the opt-in.
3. **Provider popular/similar** for movies, TV and anime through the configured provider, with the failure and
   "not configured" behaviour from [#254](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/254).
4. **Games** (IGDB), then the optional plugin capability if there is demand.

Validation: a suggestion never appears for a tracked, trashed, dismissed or listed title; "Never" survives restart and
export/restore; turning the widget off performs **no** outbound request (assert in tests); a provider outage shows nothing
rather than an error; an empty candidate set shows a quiet empty state; screenshots at desktop and phone widths in both UI
styles.

## 9. Open product choices

| Choice | Recommendation |
| --- | --- |
| Start with franchise gaps only | **Yes**: it is relevant by construction and needs no new outbound traffic |
| Snooze length | 30 days |
| Ever default-on for new accounts | **No** |
| Allow plugins to supply candidates | later, behind a new permissioned capability |
| Suggest across kinds in one widget | no: one kind at a time, user-chosen |
