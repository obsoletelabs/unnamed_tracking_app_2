# Notification overhaul coordination board

Coordination page for the notification overhaul
([#104](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/104)). It tracks
order, pull requests, invariants and verification. It does not restate the design:

- the wire and behaviour contract is [Canonical notification contract](notification-contract.md);
- the host objects, routing security, retention and operations are [Notification core](notification-core.md);
- provider authoring is [Plugin notification providers](notification-providers.md).

The state below was checked against GitHub and host `main` (`5e0f6143`) on 2026-10-19.
The feature stack and the first rollout gate (#231) have merged. #118 remains the final
acceptance audit; do not confuse a merged implementation with a completed release check.

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
| #106 stories | #184 | main | merged | none |
| #107 aggregation | #206 | main | merged | none |
| #108 timing | #211 | main | merged | none |
| #109 rich content | #215 | main | merged | none |
| #110 dispatcher | #217 | main | merged | none |
| #115 plugin contract | #218 | main | merged | plugins #53 |
| #111 SMTP | #219 | main | merged | none |
| #112 Discord | #221 | #115 branch | merged | plugins #54 |
| #113 full page | #220 | #115 branch | merged | none |
| #114 PWA | #225 | #115 branch | merged | none |
| #116 producers | #226 | #115 branch | merged | plugins #55 |
| #117 diagnostics | #227 | #115 branch | merged | none |
| #58 provider settings UI | #224 | #113 branch | merged; issue closed | none |
| #63 plugin update links | included in the integrated producer work | main | issue closed | existing plugin lifecycle contract |
| #118 rollout | #231 baseline; final acceptance follow-up | main | baseline merged; acceptance in progress | current companion main |

The base column records the final PR base, including historical stacked branches. The complete
implementation is now on `main`; no unmerged child branch is needed to run the gate.

## Historical merge order

1. #173 (done).
2. The sequential prerequisites were #184, #206, #211, #215, #217 and #218.
3. Provider/UI/producer changes followed: #219, #220, #221, #225, #226 and #227, with
   #224 following the notification page and #63 reconciled with producer lifecycle work.
4. The baseline gate #231 integrated the result. Run final #118 acceptance on current `main`.

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

This is a milestone diagram, not the complete migration graph. Existing merge revisions
reconciled the historical branches (including `9d4b2e6f1a35` before aggregation). On the checked
`main`, `alembic heads` reports only `a4c7e19b52d8`. Preserve that history and append new revisions;
do not rewrite old parents to match the simplified diagram. Re-check the head as main moves.

## Invariants every child must keep

| Invariant | Rule |
| --- | --- |
| Retention and receipts | Inbox retention is the existing requested/effective days setting (0 to 3650, inherited, server default and maximum). No second retention system. Dedupe receipts stay separate from content retention and keep no expiry. Retained history survives plugin or provider disable and uninstall. |
| Trust | PUBLIC, PRIVATE and SECURE are unchanged. Urgency never changes destination trust; plugins may request urgency, host policy and user limits decide. |
| Old APIs | Existing endpoints, producers, preferences and provider capabilities keep working through the contract's compatibility adapters. #118 retains them; retirement requires a separate reviewed change. |
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

Stack-wide gates, run on the proposed integrated `main` state before release:

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

- **Historical stack depth and migration branches** have been resolved by the merged
  integration. Validate the current single head rather than recreating the old stack.
- **Plugin repository release order** (host before plugins) must be respected by the
  release process; #53, #54 and #55 are unsigned source changes and need the normal
  signing and catalog steps by the maintainers.
- **Remote edit support varies by provider.** Where a provider cannot edit, follow-ups
  follow the `NOTIFICATION_FOLLOW_UPS` policy, so users may see an additional message.
- **Delivery semantics.** At-least-once means a retry after an ambiguous failure can show a
  duplicate remotely.
- **Email unsubscribe (#52)** is closed; preserve its existing destination-specific behaviour
  while verifying SMTP and do not create another unsubscribe policy.
- **Diagnostics test sends** are limited to the caller's own destinations and the existing
  hourly quota; provider failures are simulated, not injected.

## Definition of done for #104

- [x] Contract agreed and merged (#105).
- [x] Children #106 to #117 merged.
- [x] #58 and #63 reconciled; both issues are closed.
- [x] Baseline cross-provider gate #231 merged; compatibility adapters explicitly kept.
- [x] One Alembic head verified on the checked `main` after the merges.
- [ ] Final #118 acceptance results, including current Docker/companion/frontend checks,
  recorded and reviewed. See [Rollout and acceptance](notification-rollout.md).

Close the umbrella only when the final acceptance evidence is complete; the historical
open-stack warnings above must not be used as a substitute for checking the integrated state.
