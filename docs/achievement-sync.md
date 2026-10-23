# Achievement sync and manual progress

The app combines achievement definitions from metadata providers such as Atlas with progress from connected platforms such as Steam, PlayStation and RetroAchievements. A metadata catalogue can add an achievement when a platform has no progress row, or fill in details and artwork for a matching platform achievement.

## Automatic and scheduled sync

New games are added to a durable achievement queue. The background job refreshes each queued game after the game has been saved, including its metadata catalogue and any available platform progress. If a provider is unavailable, the game stays queued for a later retry.

Open **Settings → Tasks → Achievements sync** to run a full library refresh or enable a recurring schedule. The recurring schedule is off by default. **Run now** refreshes every active game; the job records progress and resumes large queues in bounded batches.

## Editing progress and notes

On a game's **Achievements** tab, each achievement has separate **Pin** and **Edit** controls. Pinning changes only its place in your pinned list. **Edit** opens a dialog where you can choose **Synced**, **Unlocked**, or **Locked**, and write or change a note. This is useful for Atlas catalogue entries that are not connected to a platform account. A **Manual** label appears when you choose a local unlocked or locked value.

Manual progress is stored separately from provider data. It takes precedence in the app's achievement list and remains in effect across later platform and Atlas refreshes. Choose **Synced** in the editor to remove the override and return to the latest provider state. Notes and pins remain local and can be changed without altering synced progress.

The API accepts `PUT /api/game/{game_id}/achievements/{achievement_id}/progress` with a JSON body such as `{"unlocked": true}`. The achievement list includes `manual_override` so clients can distinguish this state from provider-reported progress.

## Screenshots

The local app test stack ran against Atlas 0.7.1. The Background tasks page shows a successful manual run that refreshed one game:

![Achievements sync completed for one game in the local app](https://github.com/user-attachments/assets/6b73dbd9-2bc5-47ed-8219-3d5626b045d9)

The game page shows an Atlas catalogue achievement in a neutral row, with **Pin** and **Edit** as separate actions:

![Atlas achievement row with loaded icon and separate Pin and Edit actions](https://github.com/user-attachments/assets/3105f083-1949-4f8e-9eee-7f7958dd21ab)

The editor keeps the synced or manual progress choice together with the local note:

![Achievement editor with Synced, Unlocked, Locked and Note controls](https://github.com/user-attachments/assets/e1edb058-49d6-457b-9471-c2b115b7cdfb)
