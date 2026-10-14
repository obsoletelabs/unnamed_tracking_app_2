# Rosefall #469 source coverage ledger

This index accompanies the [numbered reconciliation choices](rosefall-469-reconciliation.md).
It records the complete source PR range, rather than a diff between the two divergent repositories.

- Source base: `0948b6616e1074998df7a48900ed0df48169d7ef`
- Source head: `2785649b061f59bf6b567b5cb17a0bded74a56e9`
- Target comparison: Obsolete Labs main `2246df175fbb556b76d5a600c32ae33ae524a567` (merged #121).
- Files: 188 (36 added, 150 modified, 2 deleted; no renames).
- No Docker, workflow, wiki/documentation or binary asset changes in the source range.
- Cosmetic Classic/Automatic Search and CI requests are independently tracked in host #123 and #122.

## Commits

| Commit | Source subject |
| --- | --- |
| [ce1fdab1b](https://github.com/Rosefall-a/unnamed_tracking_app/commit/ce1fdab1bf1809f38df8373d274962c49815f041) | Add comprehensive tests for various features and implement settings search component |
| [886e6c568](https://github.com/Rosefall-a/unnamed_tracking_app/commit/886e6c568f8220a34715b6062d2bdeb70f060074) | Refactor error messages for consistency and clarity across multiple views |
| [c0f2c7a68](https://github.com/Rosefall-a/unnamed_tracking_app/commit/c0f2c7a68df0a9fb3cb6f45d75f8dc04f3770e3f) | Refactor game and media collection terminology; update UI components and logic for improved user experience |
| [e191eb9d5](https://github.com/Rosefall-a/unnamed_tracking_app/commit/e191eb9d5634bbdecd0aac55120a8120a55a3498) | Refactor media forms and library views for improved editing experience |
| [2785649b0](https://github.com/Rosefall-a/unnamed_tracking_app/commit/2785649b061f59bf6b567b5cb17a0bded74a56e9) | Refactor components and improve code readability |

## Complete changed-file list

A = added, M = modified, D = deleted. Deleted-file links open the base revision.
The source migration is a reference for additive fields; its migration identifier/history must not be copied into the host.

### Backend routes and schemas

| Change | File |
| --- | --- |
| M | [src/backend/src/api/routes/anime.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/routes/anime.py) |
| M | [src/backend/src/api/routes/auth.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/routes/auth.py) |
| M | [src/backend/src/api/routes/auth_oidc.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/routes/auth_oidc.py) |
| M | [src/backend/src/api/routes/default_game_assets.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/routes/default_game_assets.py) |
| M | [src/backend/src/api/routes/deployment_settings.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/routes/deployment_settings.py) |
| M | [src/backend/src/api/routes/games.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/routes/games.py) |
| M | [src/backend/src/api/routes/jobs.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/routes/jobs.py) |
| M | [src/backend/src/api/routes/library_sync.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/routes/library_sync.py) |
| M | [src/backend/src/api/routes/media_common.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/routes/media_common.py) |
| M | [src/backend/src/api/routes/media_lists.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/routes/media_lists.py) |
| M | [src/backend/src/api/routes/media_stats.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/routes/media_stats.py) |
| M | [src/backend/src/api/routes/movies.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/routes/movies.py) |
| M | [src/backend/src/api/routes/setup.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/routes/setup.py) |
| M | [src/backend/src/api/routes/stats.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/routes/stats.py) |
| M | [src/backend/src/api/routes/steam_import_steps.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/routes/steam_import_steps.py) |
| M | [src/backend/src/api/routes/tv_shows.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/routes/tv_shows.py) |
| M | [src/backend/src/api/schemas/game.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/api/schemas/game.py) |

### Backend configuration, models and migration

| Change | File |
| --- | --- |
| M | [src/backend/src/core/config_registry.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/core/config_registry.py) |
| M | [src/backend/src/core/crypto.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/core/crypto.py) |
| M | [src/backend/src/core/preferences.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/core/preferences.py) |

### Database

| Change | File |
| --- | --- |
| A | [src/backend/src/database/migrations/versions/a9c3e5b7d2f4_time_to_beat_all_styles.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/database/migrations/versions/a9c3e5b7d2f4_time_to_beat_all_styles.py) |
| M | [src/backend/src/database/models/game.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/database/models/game.py) |
| M | [src/backend/src/database/models/user_scan_settings.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/database/models/user_scan_settings.py) |

### Backend features and helpers

| Change | File |
| --- | --- |
| M | [src/backend/src/features/imports/lists.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/features/imports/lists.py) |
| A | [src/backend/src/features/imports/ratings.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/features/imports/ratings.py) |
| M | [src/backend/src/features/imports/restore.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/features/imports/restore.py) |
| M | [src/backend/src/features/imports/yamtrack.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/features/imports/yamtrack.py) |
| M | [src/backend/src/features/jobs.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/features/jobs.py) |
| M | [src/backend/src/features/metadata/anime/anilist.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/features/metadata/anime/anilist.py) |
| M | [src/backend/src/features/metadata/anime/jikan.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/features/metadata/anime/jikan.py) |
| M | [src/backend/src/features/metadata/anime/kitsu.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/features/metadata/anime/kitsu.py) |
| M | [src/backend/src/features/metadata/games/hltb.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/features/metadata/games/hltb.py) |
| M | [src/backend/src/features/metadata/games/igdb.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/features/metadata/games/igdb.py) |
| M | [src/backend/src/features/metadata/games/search.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/features/metadata/games/search.py) |
| M | [src/backend/src/features/metadata/games/wiseoldman.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/features/metadata/games/wiseoldman.py) |
| M | [src/backend/src/features/metadata/locked_fields.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/features/metadata/locked_fields.py) |
| M | [src/backend/src/features/notifications.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/features/notifications.py) |
| A | [src/backend/src/features/sequel_check.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/features/sequel_check.py) |
| M | [src/backend/src/features/trash/sweep.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/features/trash/sweep.py) |

### Backend helpers

| Change | File |
| --- | --- |
| M | [src/backend/src/helpers/game_art_download.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/helpers/game_art_download.py) |
| M | [src/backend/src/helpers/media.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/helpers/media.py) |
| M | [src/backend/src/helpers/range_response.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/helpers/range_response.py) |
| M | [src/backend/src/helpers/remote_images.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/src/helpers/remote_images.py) |

### Backend regression tests

| Change | File |
| --- | --- |
| A | [src/backend/tests/test_account_security.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_account_security.py) |
| M | [src/backend/tests/test_achievements.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_achievements.py) |
| A | [src/backend/tests/test_activity_day.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_activity_day.py) |
| M | [src/backend/tests/test_epic_import.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_epic_import.py) |
| M | [src/backend/tests/test_exact_numbers.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_exact_numbers.py) |
| A | [src/backend/tests/test_explicit_null_updates.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_explicit_null_updates.py) |
| A | [src/backend/tests/test_game_art_revalidation.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_game_art_revalidation.py) |
| A | [src/backend/tests/test_game_folder_rename.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_game_folder_rename.py) |
| A | [src/backend/tests/test_game_form_save.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_game_form_save.py) |
| A | [src/backend/tests/test_game_search_ranking.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_game_search_ranking.py) |
| A | [src/backend/tests/test_import_ratings.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_import_ratings.py) |
| A | [src/backend/tests/test_jobs_failed_runs.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_jobs_failed_runs.py) |
| A | [src/backend/tests/test_library_sort.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_library_sort.py) |
| M | [src/backend/tests/test_media.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_media.py) |
| M | [src/backend/tests/test_media_ranks.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_media_ranks.py) |
| A | [src/backend/tests/test_media_trash.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_media_trash.py) |
| A | [src/backend/tests/test_new_season_notices.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_new_season_notices.py) |
| A | [src/backend/tests/test_oidc_email_verified.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_oidc_email_verified.py) |
| A | [src/backend/tests/test_remote_image_safety.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_remote_image_safety.py) |
| A | [src/backend/tests/test_setup_configuration_secrets.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_setup_configuration_secrets.py) |
| A | [src/backend/tests/test_stats_ignore_trash.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_stats_ignore_trash.py) |
| A | [src/backend/tests/test_time_to_beat.py](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/backend/tests/test_time_to_beat.py) |

### Frontend components

| Change | File |
| --- | --- |
| M | [src/frontend/src/components/AnimeFormModal.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/AnimeFormModal.vue) |
| M | [src/frontend/src/components/BulkEditModal.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/BulkEditModal.vue) |
| M | [src/frontend/src/components/CollectionPickerModal.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/CollectionPickerModal.vue) |
| M | [src/frontend/src/components/EpisodeList.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/EpisodeList.vue) |
| M | [src/frontend/src/components/GameArtPicker.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/GameArtPicker.vue) |
| M | [src/frontend/src/components/GameArtSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/GameArtSection.vue) |
| M | [src/frontend/src/components/GameCard.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/GameCard.vue) |
| M | [src/frontend/src/components/GameCollectionsButton.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/GameCollectionsButton.vue) |
| M | [src/frontend/src/components/GameFormModal.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/GameFormModal.vue) |
| A | [src/frontend/src/components/GameLookupPanel.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/GameLookupPanel.vue) |
| M | [src/frontend/src/components/GameNotesPanel.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/GameNotesPanel.vue) |
| A | [src/frontend/src/components/GameQuickEdit.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/GameQuickEdit.vue) |
| M | [src/frontend/src/components/GameStatsPanel.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/GameStatsPanel.vue) |
| M | [src/frontend/src/components/ListFormModal.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/ListFormModal.vue) |
| M | [src/frontend/src/components/MediaExtrasPanel.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/MediaExtrasPanel.vue) |
| A | [src/frontend/src/components/MediaFormModal.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/MediaFormModal.vue) |
| M | [src/frontend/src/components/MediaFormShell.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/MediaFormShell.vue) |
| M | [src/frontend/src/components/MediaKindSwitch.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/MediaKindSwitch.vue) |
| A | [src/frontend/src/components/MediaLookupPanel.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/MediaLookupPanel.vue) |
| D | [src/frontend/src/components/MediaMetadataSearch.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/0948b6616e1074998df7a48900ed0df48169d7ef/src/frontend/src/components/MediaMetadataSearch.vue) |
| M | [src/frontend/src/components/MediaTopBar.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/MediaTopBar.vue) |
| M | [src/frontend/src/components/MovieFormModal.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/MovieFormModal.vue) |
| M | [src/frontend/src/components/NotificationBell.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/NotificationBell.vue) |
| M | [src/frontend/src/components/PasswordInput.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/PasswordInput.vue) |
| M | [src/frontend/src/components/SidebarNav.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/SidebarNav.vue) |
| M | [src/frontend/src/components/TVShowFormModal.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/TVShowFormModal.vue) |
| M | [src/frontend/src/components/library/MediaLibraryView.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/library/MediaLibraryView.vue) |
| M | [src/frontend/src/components/library/QuickAddDialog.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/library/QuickAddDialog.vue) |
| M | [src/frontend/src/components/settings/AdminSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/AdminSection.vue) |
| M | [src/frontend/src/components/settings/AniListImportSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/AniListImportSection.vue) |
| M | [src/frontend/src/components/settings/ApiKeysSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/ApiKeysSection.vue) |
| M | [src/frontend/src/components/settings/AppearanceSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/AppearanceSection.vue) |
| M | [src/frontend/src/components/settings/CalendarNotificationsSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/CalendarNotificationsSection.vue) |
| M | [src/frontend/src/components/settings/ConnectionsSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/ConnectionsSection.vue) |
| M | [src/frontend/src/components/settings/DevToolsSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/DevToolsSection.vue) |
| M | [src/frontend/src/components/settings/ExportImportSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/ExportImportSection.vue) |
| M | [src/frontend/src/components/settings/GamePageSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/GamePageSection.vue) |
| M | [src/frontend/src/components/settings/InterfaceSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/InterfaceSection.vue) |
| M | [src/frontend/src/components/settings/KeyboardShortcutsSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/KeyboardShortcutsSection.vue) |
| M | [src/frontend/src/components/settings/LibraryManagementSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/LibraryManagementSection.vue) |
| M | [src/frontend/src/components/settings/LimitsSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/LimitsSection.vue) |
| M | [src/frontend/src/components/settings/MediaPreferencesSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/MediaPreferencesSection.vue) |
| M | [src/frontend/src/components/settings/MediaRefreshSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/MediaRefreshSection.vue) |
| M | [src/frontend/src/components/settings/MediaTrashSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/MediaTrashSection.vue) |
| M | [src/frontend/src/components/settings/MetadataSourcesSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/MetadataSourcesSection.vue) |
| M | [src/frontend/src/components/settings/OidcSettingsSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/OidcSettingsSection.vue) |
| M | [src/frontend/src/components/settings/PasswordPolicySection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/PasswordPolicySection.vue) |
| M | [src/frontend/src/components/settings/PasswordRequirements.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/PasswordRequirements.vue) |
| M | [src/frontend/src/components/settings/ProfileSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/ProfileSection.vue) |
| M | [src/frontend/src/components/settings/ScanSettingsSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/ScanSettingsSection.vue) |
| M | [src/frontend/src/components/settings/ServerIntegrationsSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/ServerIntegrationsSection.vue) |
| A | [src/frontend/src/components/settings/SettingsSearch.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/SettingsSearch.vue) |
| M | [src/frontend/src/components/settings/StatsSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/StatsSection.vue) |
| M | [src/frontend/src/components/settings/SteamTagsSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/SteamTagsSection.vue) |
| M | [src/frontend/src/components/settings/SteamWishlistSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/SteamWishlistSection.vue) |
| M | [src/frontend/src/components/settings/TasksSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/TasksSection.vue) |
| M | [src/frontend/src/components/settings/ToggleButton.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/ToggleButton.vue) |
| M | [src/frontend/src/components/settings/TrustedProxyControls.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/TrustedProxyControls.vue) |
| M | [src/frontend/src/components/settings/UploadSection.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/settings/UploadSection.vue) |
| M | [src/frontend/src/components/stats/ActivityHeatmap.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/components/stats/ActivityHeatmap.vue) |

### Frontend services and state

| Change | File |
| --- | --- |
| M | [src/frontend/src/services/admin.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/admin.ts) |
| M | [src/frontend/src/services/anime.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/anime.ts) |
| M | [src/frontend/src/services/appearanceSettings.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/appearanceSettings.ts) |
| M | [src/frontend/src/services/auth.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/auth.ts) |
| M | [src/frontend/src/services/deploymentSettings.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/deploymentSettings.ts) |
| M | [src/frontend/src/services/exportImport.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/exportImport.ts) |
| M | [src/frontend/src/services/gameArchives.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/gameArchives.ts) |
| M | [src/frontend/src/services/gameProfiles.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/gameProfiles.ts) |
| M | [src/frontend/src/services/games.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/games.ts) |
| M | [src/frontend/src/services/media.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/media.ts) |
| M | [src/frontend/src/services/mediaApi.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/mediaApi.ts) |
| M | [src/frontend/src/services/mediaExtras.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/mediaExtras.ts) |
| M | [src/frontend/src/services/mediaStats.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/mediaStats.ts) |
| M | [src/frontend/src/services/movies.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/movies.ts) |
| M | [src/frontend/src/services/notifications.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/notifications.ts) |
| M | [src/frontend/src/services/passwordPolicy.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/passwordPolicy.ts) |
| M | [src/frontend/src/services/preferences.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/preferences.ts) |
| M | [src/frontend/src/services/psn.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/psn.ts) |
| M | [src/frontend/src/services/settings.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/settings.ts) |
| M | [src/frontend/src/services/setup.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/setup.ts) |
| M | [src/frontend/src/services/stats.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/stats.ts) |
| M | [src/frontend/src/services/tvShows.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/services/tvShows.ts) |

### Frontend state

| Change | File |
| --- | --- |
| M | [src/frontend/src/state/startup.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/state/startup.ts) |

### Frontend utilities and types

| Change | File |
| --- | --- |
| A | [src/frontend/src/utils/artVersion.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/utils/artVersion.ts) |
| M | [src/frontend/src/utils/libraryFilters.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/utils/libraryFilters.ts) |
| A | [src/frontend/src/utils/libraryPaging.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/utils/libraryPaging.ts) |
| A | [src/frontend/src/utils/librarySort.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/utils/librarySort.ts) |
| A | [src/frontend/src/utils/mediaLookup.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/utils/mediaLookup.ts) |
| M | [src/frontend/src/utils/mediaStatus.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/utils/mediaStatus.ts) |
| A | [src/frontend/src/utils/numberInput.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/utils/numberInput.ts) |
| A | [src/frontend/src/utils/settingsSearch.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/utils/settingsSearch.ts) |
| M | [src/frontend/src/utils/useMetadataSearch.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/utils/useMetadataSearch.ts) |

### Frontend types

| Change | File |
| --- | --- |
| M | [src/frontend/src/types/game.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/types/game.ts) |

### Frontend tests

| Change | File |
| --- | --- |
| A | [src/frontend/src/tests/artVersion.test.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/tests/artVersion.test.ts) |
| A | [src/frontend/src/tests/bucketCounts.test.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/tests/bucketCounts.test.ts) |
| M | [src/frontend/src/tests/completion.test.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/tests/completion.test.ts) |
| A | [src/frontend/src/tests/numberInput.test.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/tests/numberInput.test.ts) |
| A | [src/frontend/src/tests/settingsSearch.test.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/tests/settingsSearch.test.ts) |
| M | [src/frontend/src/tests/startup.test.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/tests/startup.test.ts) |

### Frontend views

| Change | File |
| --- | --- |
| M | [src/frontend/src/views/AchievementDetail.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/AchievementDetail.vue) |
| M | [src/frontend/src/views/AnimeDetail.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/AnimeDetail.vue) |
| M | [src/frontend/src/views/AnimeLibrary.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/AnimeLibrary.vue) |
| M | [src/frontend/src/views/Calendar.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/Calendar.vue) |
| M | [src/frontend/src/views/CollectionDetail.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/CollectionDetail.vue) |
| M | [src/frontend/src/views/Collections.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/Collections.vue) |
| M | [src/frontend/src/views/GameDetail.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/GameDetail.vue) |
| M | [src/frontend/src/views/GameLibrary.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/GameLibrary.vue) |
| M | [src/frontend/src/views/HomeHub.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/HomeHub.vue) |
| M | [src/frontend/src/views/Login.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/Login.vue) |
| M | [src/frontend/src/views/MediaListDetail.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/MediaListDetail.vue) |
| M | [src/frontend/src/views/MediaLists.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/MediaLists.vue) |
| M | [src/frontend/src/views/MovieDetail.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/MovieDetail.vue) |
| M | [src/frontend/src/views/MovieLibrary.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/MovieLibrary.vue) |
| M | [src/frontend/src/views/Notifications.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/Notifications.vue) |
| M | [src/frontend/src/views/Settings.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/Settings.vue) |
| M | [src/frontend/src/views/Setup.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/Setup.vue) |
| M | [src/frontend/src/views/Statistics.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/Statistics.vue) |
| M | [src/frontend/src/views/TVShowDetail.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/TVShowDetail.vue) |
| M | [src/frontend/src/views/TVShowLibrary.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/views/TVShowLibrary.vue) |

### Frontend styles

| Change | File |
| --- | --- |
| D | [src/frontend/src/styles/shared/mediaForm.css](https://github.com/Rosefall-a/unnamed_tracking_app/blob/0948b6616e1074998df7a48900ed0df48169d7ef/src/frontend/src/styles/shared/mediaForm.css) |
| A | [src/frontend/src/styles/shared/sortHead.css](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/styles/shared/sortHead.css) |

### Frontend root and router

| Change | File |
| --- | --- |
| M | [src/frontend/src/App.vue](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/App.vue) |
| M | [src/frontend/src/router/index.ts](https://github.com/Rosefall-a/unnamed_tracking_app/blob/2785649b061f59bf6b567b5cb17a0bded74a56e9/src/frontend/src/router/index.ts) |
