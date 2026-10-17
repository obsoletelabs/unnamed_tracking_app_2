# Rosefall copy and consistency review

Final pass of the [Rosefall #469 queue](rosefall-469-implementation.md) (issue #154).
It covers only the workflows the queue changed. It is not an app-wide rewrite:
older screens keep their wording until they are next touched for another reason.

## Rules used

- **Sentence case** for titles, buttons, labels and toggles. Proper names and
  product terms stay as they are (TV, AniList, Steam, and the media status names
  Plan to Watch and On Hold, which are one shared vocabulary used app-wide).
- **Saved, empty and destructive states** say what happened or what will happen.
  A destructive control names its consequence and, where it is irreversible, says
  "This can't be undone." and asks first.
- **Errors** read "Could not &lt;action&gt;." followed by what to do when there is
  something to do. A server message, validation detail or permission reason is
  always shown in preference to the generic fallback; the fallback only replaces
  a failure that carries no message.
- **Kept on purpose:** accessibility names and live-region text, permission and
  dependency explanations ("Hidden while ... is on", "Turn off ... below"), and
  diagnostic detail such as counts, titles and reasons in trash results.
- **Modern and Classic** share one template and one set of strings. Classic only
  restyles (`.editor-shell`, tokens), so every change below applies to both.
  `copyConsistency.test.ts` asserts that `classic.css` injects no copy.

## Screens reviewed and changes

| Screen | Workflow | Change | Before | After |
| --- | --- | --- | --- | --- |
| Game editor, Edit | #143 / #149 | Sentence-case title, save and delete | Edit Game, Save Changes, Delete Game | Edit game, Save changes, Delete game |
| Game editor, Add and Quick add | #143 | Sentence-case title, button and hint | Add Game, Quick Add Game | Add game, Quick add game |
| Game library | #143 | Add button | + Add Game | + Add game |
| Movie editor | #144 | Title, save, add | Edit Movie, Save Changes | Edit movie, Save changes |
| TV show editor | #144 | Title, save, add | Edit TV Show, Save Changes | Edit TV show, Save changes |
| Anime editor | #144 | Title, save, add | Edit Anime, Save Changes | Edit anime, Save changes |
| Movie, TV, anime libraries | #144 / #149 | Add buttons | + Add Movie, + Add Anime, + Add Show | + Add movie, + Add anime, + Add show |
| Game quick edit | #149 | Save failure fallback | Failed to save game | Could not save the game. |
| Movie, TV, anime and game editors | #143 / #144 | Save and delete failure fallbacks | Failed to save movie / delete show ... | Could not save the movie. / Could not delete the show. ... |
| Calendar settings | #153 | Toggle name, note, save and load fallbacks | Hide all Games layers, Failed to load settings. | Hide all games layers, Could not load the settings. |
| Preference availability notes | #153 | Same toggle named in the reasons | Turn off Hide all Games layers below. | Turn off Hide all games layers below. |
| Notification settings | #153 | Load and save fallbacks | Failed to save. | Could not save the settings. |
| AniList and anime franchise settings | #145 | Load and save fallbacks | Failed to save. | Could not save the settings. |
| Media trash | #146 | Load, restore, delete fallbacks | Failed to restore | Could not restore the title. |
| Media refresh | #145 | Lost connection while polling | Lost the connection | Could not check progress. The connection was lost. |
| Profile and display name | #125 | Update, picture and policy fallbacks | Failed to update profile | Could not update the profile. |

Reviewed and left unchanged because they already meet the rules: the media
trash confirmations ("Permanently delete ...? This can't be undone.", the Empty
trash count, "Delete forever"), automatic-deletion confirmation, the Empty trash
result with deleted/failed/remaining counts, quick-editor "Edit more" handover,
settings search no-result and permission-aware messages, day activity empty
states, display name and username validation messages, the metadata provider
settings, and every accessibility label.

Intentionally not changed: media status names (Plan to Watch, On Hold), the
sidebar and settings navigation labels ("TV Shows", "Keyboard Shortcuts") and
game-detail errors outside the editor. They are shared by many screens and belong
to a separate vocabulary decision.

## Screenshots

Before is the stacked branches with no wording change; after is this branch. Both
use the same data. Each pair shows Modern, then Classic.

| Screen | Before (Modern) | After (Modern) | Before (Classic) | After (Classic) |
| --- | --- | --- | --- | --- |
| Game editor | ![](../assets/wording-review/before-modern-game-edit.jpg) | ![](../assets/wording-review/after-modern-game-edit.jpg) | ![](../assets/wording-review/before-classic-game-edit.jpg) | ![](../assets/wording-review/after-classic-game-edit.jpg) |
| Add game | ![](../assets/wording-review/before-modern-game-add.jpg) | ![](../assets/wording-review/after-modern-game-add.jpg) | ![](../assets/wording-review/before-classic-game-add.jpg) | ![](../assets/wording-review/after-classic-game-add.jpg) |
| Movie editor | ![](../assets/wording-review/before-modern-movie-edit.jpg) | ![](../assets/wording-review/after-modern-movie-edit.jpg) | ![](../assets/wording-review/before-classic-movie-edit.jpg) | ![](../assets/wording-review/after-classic-movie-edit.jpg) |
| TV show editor | ![](../assets/wording-review/before-modern-tv-edit.jpg) | ![](../assets/wording-review/after-modern-tv-edit.jpg) | ![](../assets/wording-review/before-classic-tv-edit.jpg) | ![](../assets/wording-review/after-classic-tv-edit.jpg) |
| Anime editor | ![](../assets/wording-review/before-modern-anime-edit.jpg) | ![](../assets/wording-review/after-modern-anime-edit.jpg) | ![](../assets/wording-review/before-classic-anime-edit.jpg) | ![](../assets/wording-review/after-classic-anime-edit.jpg) |
| Calendar settings | ![](../assets/wording-review/before-modern-calendar-settings.jpg) | ![](../assets/wording-review/after-modern-calendar-settings.jpg) | ![](../assets/wording-review/before-classic-calendar-settings.jpg) | ![](../assets/wording-review/after-classic-calendar-settings.jpg) |
| Movie library | ![](../assets/wording-review/before-modern-movie-library.jpg) | ![](../assets/wording-review/after-modern-movie-library.jpg) | ![](../assets/wording-review/before-classic-movie-library.jpg) | ![](../assets/wording-review/after-classic-movie-library.jpg) |
| Trash, destructive confirmation | ![](../assets/wording-review/before-modern-trash-empty-confirm.jpg) | ![](../assets/wording-review/after-modern-trash-empty-confirm.jpg) | ![](../assets/wording-review/before-classic-trash-empty-confirm.jpg) | ![](../assets/wording-review/after-classic-trash-empty-confirm.jpg) |

The trash confirmation is unchanged on purpose; the pair shows that the
destructive wording and its consequence survived the pass.

## Migration consistency

- **Migrations:** this review adds none. `alembic heads` reports one head
  (`a7d3c91e5b20`, the optional display name) on top of the stacked branches.
- **Shared behaviour:** all dialogs above use `EditorShell` or `UiModal` and one
  string set for Modern and Classic. Quick editors hand unsaved values to the full
  editor (`GameEditDraft`) without changing wording between them.
- **Stack:** this branch was built on #200 (which includes #181) with #175, #198
  and the #204 status board merged in; it can be retargeted once they merge.

## Checking a new screen

1. Search the screen's strings for Title Case, "Failed to", "Please", "Oops" and
   exclamation marks, and rewrite only those in the workflow you are changing.
2. For an error, keep the server's message; the fallback is a "Could not" sentence.
3. For a destructive action, name the object, the consequence and, if permanent,
   "This can't be undone."; do not remove the confirmation.
4. Verify Modern and Classic, and run `npm run test -- copyConsistency`.
