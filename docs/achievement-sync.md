# Achievement sync and manual progress

The app combines achievement definitions from metadata providers such as Atlas with progress from connected platforms such as Steam, PlayStation and RetroAchievements. A metadata catalogue can add an achievement when a platform has no progress row, or fill in details and artwork for a matching platform achievement.

## Automatic and scheduled sync

New games are added to a durable achievement queue. The background job refreshes each queued game after the game has been saved, including its metadata catalogue and any available platform progress. If a provider is unavailable, the game stays queued for a later retry.

Open **Settings → Tasks → Achievements sync** to run a full library refresh or enable a recurring schedule. The recurring schedule is off by default. **Run now** refreshes every active game; the job records progress and resumes large queues in bounded batches.

## Manually setting progress

On a game's **Achievements** tab, choose **Mark unlocked** or **Mark locked** on an achievement. This is useful for Atlas catalogue entries that are not connected to a platform account. A **Manual** label shows which progress value you set.

Manual progress is stored separately from provider data. It takes precedence in the app's achievement list and remains in effect across later platform and Atlas refreshes. Mark the achievement again to change the manual value, or choose **Use synced** to remove the override and return to the latest provider state.

The API accepts `PUT /api/game/{game_id}/achievements/{achievement_id}/progress` with a JSON body such as `{"unlocked": true}`. The achievement list includes `manual_override` so clients can distinguish this state from provider-reported progress.

## Screenshots

The local app test stack ran against Atlas 0.7.1. The Background tasks page shows a successful manual run that refreshed one game:

![Achievements sync completed for one game in the local app](https://github.com/user-attachments/assets/6b73dbd9-2bc5-47ed-8219-3d5626b045d9)

The game page shows the Atlas catalogue achievement marked manually, the **Use synced** action, and the neutral **Edit** button beside the primary Steam launch action:

![Atlas achievement manual progress and consistent Edit button styling](https://github.com/user-attachments/assets/1635aaa0-0c9a-48ff-9f97-f278aa2c96b8)
