# Automatic Search contract and enforcement

The existing preferences endpoint stores `metadata_automatic_search_enabled`
(boolean, default true) and `metadata_automatic_search_providers` (bounded map of
provider identifiers to booleans). An omitted provider choice means enabled.
The account master switch takes priority over all per-provider choices.

`POST /api/metadata/search` accepts additive `trigger: "automatic" | "explicit"`.
Omitting it retains explicit behavior for existing callers. The shared frontend
composable marks debounced typing requests automatic; Search/Enter are explicit.
Cancellation and retained local filtering keep the existing session lifecycle.

Authenticated `GET /api/metadata/automatic-search` exposes the server master
state. Administrator-only `PUT` accepts `enabled` and an optional `provider_id`;
omitting the identifier updates the server master. Provider identifiers must
belong to registered, available providers. The existing provider listing includes
effective automatic policy and safe reason/personal-credential flags.

`features/metadata/automatic_search.py` resolves the existing credential-presence
contract and the singleton server policy. Complete personal credentials may
override server policy; mixed personal/server and credential-free providers may
not. It does not create another credential resolver or disclose credential values.

The route filters disabled automatic providers before availability/health work.
The handler also omits them from search and typing preloading. Built-in and plugin
adapters recheck current policy before invocation, so enforcement is not merely a
frontend toggle. Explicit selection enrichment and artwork operations clear the
automatic marker and retain their normal authorization boundaries.

Plugin API v1's existing `MetadataRequest.options` map carries additive
`automatic_search: true/false`. No public version bump or optional provider source
copy is required. Existing provider implementations continue to consume requests
through the host gateway. The host controls automatic invocation; providers must
not start background searches independently from a typing request. Contract tests
cover credential-free and optional providers, explicit selection, master/provider
precedence and administrator-only writes.

The additive `metadata_automatic_search_policy` JSONB column stores server flags,
with `*` representing the master flag. Existing databases start with `{}` (enabled).
The presentation default is a separate nullable column. Both new revisions extend
the merged main head, support adopted/replayed databases and preserve existing
preferences. Back up and run the normal Alembic upgrade before deploying.
