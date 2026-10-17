# Notification overhaul coordination board

Coordination page for the notification overhaul
([#104](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/104)). It tracks
order, pull requests, invariants and verification. It does not restate the design:

- the wire and behaviour contract is [Canonical notification contract](notification-contract.md);
- the host objects, routing security, retention and operations are [Notification core](notification-core.md);
- provider authoring is [Plugin notification providers](notification-providers.md).

The state below was read from GitHub on 2026-10-17. Re-check it before acting; the table is
a snapshot, not a live view.

## Versions

| Item | Version | Defined by |
| --- | --- | --- |
| Notification contract (`NOTIFICATION_CONTRACT_VERSION`) | 1.0.0 | #105, extended without wire changes by #106 to #110 |
| Plugin API | 1.1.14 | #115 (first gateway that accepts the contract documents) |
| Plugin SDK helpers | companion plugin PR #53 | #115 |

## Dependency graph

```text
#105 contract
  -> #106 stories and idempotency
       -> #107 aggregation and digests
       -> #108 timing policy and urgency
            -> #109 rich content, live story pages
                 -> #110 capability-aware dispatcher
                      -> #115 plugin contract (Plugin API 1.1.14)
                           -> #111 SMTP      #112 Discord    #113 full page
                              #114 PWA       #116 producers  #117 diagnostics
                                   -> #118 rollout and acceptance gate
#58 provider settings UI    stacks on #113
#63 plugin update links     stacks on #116
```

#107 and #108 are stacked in sequence (#108 on #107) because both change the same
dispatcher and policy modules, even though the issue treats them as parallel.

## Pull request board

All pull requests target the base shown, so each diff contains only its own issue.
"Plugins" pull requests are in `obsoletelabs/unnamed_tracking_app_plugins`.

| Issue | Pull request | Base | State | Companion |
| --- | --- | --- | --- | --- |
| #105 contract | #173 | main | merged | none |
| #106 stories | #184 | main | open | none |
| #107 aggregation | #206 | #106 branch | open | none |
| #108 timing | #211 | #107 branch | open | none |
| #109 rich content | #215 | #108 branch | open | none |
| #110 dispatcher | #217 | #109 branch | open | none |
| #115 plugin contract | #218 | #110 branch | open | plugins #53 |
| #111 SMTP | #219 | #115 branch | open | none |
| #112 Discord | #221 | #115 branch | open | plugins #54 |
| #113 full page | #220 | #115 branch | open | none |
| #114 PWA | #225 | #115 branch | open | none |
| #116 producers | #226 | #115 branch | open | plugins #55 |
| #117 diagnostics | #227 | #115 branch | open | none |
| #58 provider settings UI | #224 | #113 branch | open, draft | none |
| #63 plugin update links | in progress | #116 branch | no pull request yet | to be determined |
| #118 rollout | in progress | integration | no pull request yet | to be determined |

Only #105 has merged. Every later pull request is stacked; none can land before its base.

## Merge order

1. #173 (done).
2. #184, then #206, #211, #215, #217, #218 strictly in that order. Retarget each
   pull request to `main` after its base merges.
3. After #218, these are independent and may merge in any order: #219, #220, #221, #225,
   #226, #227. Merge #224 after #220, and #63 after #226.
4. #118 last, as the integration gate. It should be a rebase onto the merged result.

Release order: **host before plugins.** Plugin API 1.1.14 hosts accept the new
documents, while old hosts reject them, so the companion plugin pull requests
(#53, #54, #55) are released only after the host containing #218 (and #221/#226 for
their features). Old plugins keep working on the new host through the compatibility
adapters, so the reverse order is never required.

## Alembic chain

One linear chain is required. The notification migrations are:

```text
e4a0157eef4b (admin user audit)
  -> 6f75008c02a6  persist notification stories            (#106)
  -> 61de2c6d5459  persist aggregation windows              (#107)
  -> a3c81e5d7f20  timing policy columns                    (#108)
  -> b7d41c9a2e65  secure rich content and live links       (#109)
  -> c91f3a27d5b8  capability-aware dispatch                (#110)
  -> ...           (no migration in #115 to #117 so far; later pull requests append here)
```

**Main's head has moved.** On `origin/main` (7d90dc7b) the head is `a7d3c91e5b20` (user
display name, #201), whose parent is also `e4a0157eef4b`. When #184 merges as it
stands, `6f75008c02a6` and `a7d3c91e5b20` would be two heads. Before merging #184, rebase
it so its `down_revision` is `a7d3c91e5b20`, then re-point nothing else: the rest of the
chain follows. Re-verify the head each time main moves (see verification below).

## Invariants every child must keep

| Invariant | Rule |
| --- | --- |
| Retention and receipts | Inbox retention is the existing requested/effective days setting (0 to 3650, inherited, server default and maximum). No second retention system. Dedupe receipts stay separate from content retention and keep no expiry. Retained history survives plugin or provider disable and uninstall. |
| Trust | PUBLIC, PRIVATE and SECURE are unchanged. Urgency never changes destination trust; plugins may request urgency, host policy and user limits decide. |
| Old APIs | Existing endpoints, producers, preferences and provider capabilities keep working through the contract's compatibility adapters until #118 retires them. |
| Destination proof | Destination revision, consent, ownership proof, verification and recovery restrictions and dispatch-time authorization are rechecked before every physical send, edit and follow-up. |
| At-least-once | External delivery is at least once. Reconciliation (edits, follow-ups, remote references) reduces duplicates but never claims exactly-once remote delivery. |
| Content safety | No executable JavaScript, sanitised Markdown and HTML, validated URLs and actions, no secrets in notifications or diagnostics. |
| Migrations and CI | One linear Alembic history with fresh and populated coverage. No test or CI weakening. |

## Per-child verification

Run from `src/backend` with the backend environment (`pytest -q -p no:cacheprovider <files>`)
unless noted. Frontend commands run from `src/frontend`.

| Issue | Focused tests |
| --- | --- |
| #105 | `test_notification_contract.py`, `test_notification_contract_adapters.py` |
| #106 | `test_notification_stories.py`, `test_notification_story_migration.py` |
| #107 | `test_notification_aggregation.py`, `test_notification_aggregation_migration.py` |
| #108 | `test_notification_timing.py`, `test_notification_timing_policy.py`, `test_notification_timing_migration.py`; frontend `notificationTiming.test.ts` |
| #109 | `test_notification_rich_content.py`, `test_notification_images.py`, `test_notification_story_pages.py`, `test_notification_rich_migration.py`; frontend `notificationStory.test.ts` |
| #110 | `test_notification_dispatch_capabilities.py`, `test_notification_dispatch_migration.py` |
| #115 | `test_plugin_notification_contract.py`, `test_plugin_notification_schemas.py`, `test_plugin_notification_packages.py`, `test_plugin_notification_provider_contract.py`, `test_plugin_version_diagnostics.py`; plugins repo SDK tests |
| #111 | `test_notification_smtp_rich.py` |
| #112 | `test_plugin_rich_delivery.py`; plugins repo Discord tests |
| #113 | `test_notification_inbox.py`; frontend `notificationHistoryPage.test.ts` |
| #114 | `test_notification_browser.py`, `test_notification_browser_story.py` |
| #116 | `test_notification_producer_stories.py`, `test_official_producer_stories.py`; plugins repo Jellyfin tests |
| #117 | `test_notification_diagnostics.py`; frontend `notificationDiagnostics.test.ts` |

Stack-wide gates, run on the topmost branch before the final merge:

```bash
alembic upgrade head && alembic heads        # exactly one head, and it is the expected one
ruff format --check . && ruff check .
mypy --config-file pyproject.toml src && pylint --rcfile=pyproject.toml src
pytest -q -p no:cacheprovider tests/test_notification_*.py tests/test_plugin_notification_*.py
# frontend: npm run lint && npm run format && npm run typecheck && npm run test && npm run build
```

## Decisions recorded

- Designed for immediate delivery, aggregation, mutable stories and scheduled digests
  from the start; digests batch related items first, provider edits come later.
- Policies are per category and destination with user overrides and quiet hours.
- Duplicates are skipped, related events group, plugin update stories move
  available to installed or failed while eligible; a read or dismissed item gets a new
  linked item and keeps history.
- Email cannot be edited, so email links go to host-owned live story pages and follow-ups
  are selective. Editable providers edit in place.
- The bell stays compact; rich management and history are on the full notification page.
- Security destinations are contract-ready only; no security application is built here.

## Known risks and limitations

- **Stack depth.** Seven sequential open pull requests; a late fix in #184 forces rebases
  of everything above it. Never force-push a stacked branch another author has pushed
  to; fetch first.
- **Migration head moved** (see above): required rebase of #184 before merge.
- **Plugin repository release order** (host before plugins) must be respected by the
  release process; #53, #54 and #55 are unsigned source changes and need the normal
  signing and catalog steps by the maintainers.
- **Remote edit support varies by provider.** Where a provider cannot edit, follow-ups
  follow the `NOTIFICATION_FOLLOW_UPS` policy, so users may see an additional message.
- **Delivery semantics.** At-least-once means a retry after an ambiguous failure can show a
  duplicate remotely.
- **Email unsubscribe (#52)** is not part of this scope; reconcile it with #111 separately.
- **Diagnostics test sends** are limited to the caller's own destinations and the existing
  hourly quota; provider failures are simulated, not injected.

## Definition of done for #104

- [x] Contract agreed and merged (#105).
- [ ] Children #106 to #117 merged in the order above.
- [ ] #58 and #63 reconciled and merged.
- [ ] #118 cross-provider acceptance gate merged and adapters retired or explicitly kept.
- [ ] One Alembic head verified on `main` after the merges.

The umbrella is therefore not complete today; this page is the status artefact only.
