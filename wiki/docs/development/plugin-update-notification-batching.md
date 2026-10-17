# Plugin update notification batching

## Goal

Treat a completed plugin update-check/auto-update cycle as one notification batch. A batch can include multiple plugins and multiple outcomes; it must not send one email, Discord DM, or in-app notification per plugin.

Each item should identify the plugin, its old version and target/new version, and the outcome. Relevant outcomes include automatically updated, automatic update failed, and update still available. Include skipped/not-eligible items only when doing so explains why an update remains pending.

Every supported transport should receive one event for the batch and render the same item list using its existing format and limits. The notification action must resolve against the current installation origin to:

`/settings?area=administration&section=plugins`

Do not hard-code any deployment domain or link to the app root.

## Required investigation

- Trace the scheduler/job, update discovery, auto-update eligibility, executor, installed-version refresh and notification delivery paths.
- Find why auto-updates appear not to run and identify whether the cause is a real opt-out, global configuration, stale state, eligibility logic, scheduler behaviour or an execution failure.
- Preserve explicit plugin opt-outs and existing plugin security/permission checks.
- Inspect current notification idempotency and history semantics before changing event identity or state transitions.

## Merge checklist

- [ ] Emit at most one consolidated notification per completed update-check/update batch, regardless of plugin count.
- [ ] Include all relevant plugins in that batch with plugin identity, old version → target/new version and accurate outcome.
- [ ] Preserve current per-update deduplication; repeated unchanged checks do not send repeated notifications.
- [ ] Reconcile successful updates so they no longer appear as still available.
- [ ] Fix the root cause of auto-update failures without overriding legitimate plugin opt-outs.
- [ ] Send the batch through each existing supported transport once, using existing routing, preferences, verification, privacy and retry rules.
- [ ] Use the installation's own origin plus `/settings?area=administration&section=plugins` for every notification action.
- [ ] Add regression coverage: four available updates produce one notification, not four.
- [ ] Cover mixed outcomes (success/failure/still available), repeated/no-op cycles, partial delivery failures/retries, and version accuracy.
- [ ] Cover local-origin URL generation and ensure the root URL is not the destination.
- [ ] Preserve existing notification history/lifecycle, plugin update functionality and all unrelated features.
- [ ] Update relevant developer/wiki documentation and companion provider contracts only if the implementation actually changes them.
- [ ] Run relevant backend/frontend/plugin/notification tests, lint/format/type checks, builds and strict docs checks.
- [ ] Verify all hosted CI checks pass on the final PR head; do not weaken tests or CI to make them pass.

## Explicitly out of scope

Do not implement application self-updating in this change. A separate future design should check a trusted release source, compare versions, and send an admin-facing update-available event through the Notification Manager without silently installing application updates.
