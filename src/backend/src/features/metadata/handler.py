"""One host-owned progressive search, ranking and enrichment lifecycle."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID, uuid4

from src.plugin_api.metadata_contracts import (
    MetadataCandidate,
    MetadataPatch,
    MetadataProviderRegistration,
    MetadataProviderRequest,
    ProviderFailure,
    ProviderHealth,
    ProviderResponse,
)

from .core import CoreMetadataProvider
from .deadlines import bounded
from .identity import candidate_id, identities, rank_key, same_entity
from .providers import Operation, PluginMetadataProvider

logger = logging.getLogger(__name__)
PRELOAD_CONCURRENCY = 5
SEARCH_CACHE_SECONDS = 120
SEARCH_CACHE_CAPACITY = 256


class Provider(Protocol):
    """The host adapter and test doubles expose the same public operation contract."""

    @property
    def id(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def priority(self) -> int: ...
    @property
    def revision(self) -> str: ...
    @property
    def state(self) -> ProviderHealth: ...
    @property
    def declaration(self) -> MetadataProviderRegistration: ...

    async def invoke(
        self,
        operation: Operation,
        request: MetadataProviderRequest,
        candidate: MetadataCandidate | None = None,
    ) -> ProviderResponse: ...


@dataclass
class Source:
    """Keep patches per source so authority never depends on completion order."""

    candidate: MetadataCandidate
    priority: int
    position: int
    metadata: dict[str, MetadataPatch] = field(default_factory=dict)


@dataclass
# The session is a lifecycle record; independent phase state stays explicit.
# pylint: disable-next=too-many-instance-attributes
class SearchSession:
    """An owner-scoped interactive search and its independent enrichment work."""

    request: MetadataProviderRequest
    providers: tuple[Provider, ...]
    id: str = field(default_factory=lambda: str(uuid4()))
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    state: str = "searching"
    provider_states: dict[str, str] = field(default_factory=dict)
    sources: dict[str, Source] = field(default_factory=dict)
    candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    groups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    redirects: dict[str, str] = field(default_factory=dict)
    selected: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    work: dict[tuple[str, str, str], asyncio.Task] = field(default_factory=dict)
    completed: set[tuple[str, str, str]] = field(default_factory=set)
    preloaded: set[str] = field(default_factory=set)
    unresolved: dict[tuple[str, str, str], tuple] = field(default_factory=dict)
    media_requested: bool = True
    enrichment_finished: bool = False
    failed: bool = False
    cancelled: bool = False
    search_task: asyncio.Task | None = None

    def emit(self, event: str, **data: Any) -> None:
        """Monotonic event IDs support reconnect and prevent stale frontend updates."""
        self.updated_at = time.time()
        self.events.append(
            deepcopy({"id": len(self.events) + 1, "event": event, "session_id": self.id, **data})
        )
        self.changed.set()

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "query": self.request.query,
            "media_type": self.request.media_type,
            "state": self.state,
            "providers": self.provider_states,
            "results": list(self.candidates.values()),
            "selected": self.selected,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "last_event_id": len(self.events),
        }


class MetadataHandler:
    """Generic orchestration; external API policy lives in native or optional providers."""

    def __init__(self, operation_timeout: float = 8, session_ttl: float = 300) -> None:
        self.operation_timeout = operation_timeout
        self.session_ttl = session_ttl
        self.sessions: dict[str, SearchSession] = {}
        self.cache: OrderedDict[tuple, tuple[float, ProviderResponse]] = OrderedDict()

    def start(
        self, request: MetadataProviderRequest, providers: Sequence[Provider]
    ) -> SearchSession:
        """Schedule providers without making session creation wait for external calls."""
        self.expire()
        session = SearchSession(request, tuple(providers))
        self.sessions[session.id] = session
        session.emit("search_started", query=request.query, media_type=request.media_type)
        session.search_task = asyncio.create_task(self._search(session))
        return session

    def focus(
        self,
        request: MetadataProviderRequest,
        providers: Sequence[Provider],
        candidate: MetadataCandidate,
        *,
        include_media: bool = True,
    ) -> SearchSession:
        """An explicitly selected library identity starts media without another search."""
        self.expire()
        session = SearchSession(
            request, tuple(providers), state="selected", media_requested=include_media
        )
        self.sessions[session.id] = session
        key = candidate_id(candidate)
        session.sources[key] = Source(candidate, 0, 0)
        session.selected = key
        self._rank(session)
        session.emit("candidate_selected", candidate_id=key)
        return session

    def owned(self, session_id: str, user_id: UUID) -> SearchSession:
        session = self.sessions.get(session_id)
        if session is None or session.request.user_id != user_id:
            raise LookupError("search session not found")
        return session

    def expire(self) -> None:
        now = time.time()
        for session in list(self.sessions.values()):
            if now - session.updated_at > self.session_ttl:
                self.cancel(session)
                self.sessions.pop(session.id, None)

    def cancel(self, session: SearchSession) -> None:
        if session.cancelled:
            return
        session.cancelled = True
        session.state = "cancelled"
        if session.search_task:
            session.search_task.cancel()
        for task in session.work.values():
            task.cancel()
        session.work.clear()
        session.emit("search_cancelled")

    async def stream(self, session: SearchSession, after: int = 0) -> AsyncIterator[dict]:
        """The stream remains open for enrichment and selection until cancel/expiry."""
        cursor = max(0, after)
        while True:
            session.changed.clear()
            for event in session.events[cursor:]:
                cursor = event["id"]
                yield event
            if session.cancelled:
                return
            if time.time() - session.updated_at > self.session_ttl:
                self.cancel(session)
                continue
            try:
                await asyncio.wait_for(session.changed.wait(), 15)
            except TimeoutError:
                yield {"event": "heartbeat", "session_id": session.id}

    async def _call(
        self,
        session: SearchSession,
        provider: Provider,
        operation: Operation,
        candidate: MetadataCandidate | None = None,
        *,
        request: MetadataProviderRequest | None = None,
        timeout: float | None = None,
    ) -> ProviderResponse:
        started = time.monotonic()
        try:
            result = await bounded(
                provider.invoke(operation, request or session.request, candidate),
                timeout if timeout is not None else self._timeout(session),
            )
        except TimeoutError:
            result = ProviderResponse(failure=ProviderFailure(code="timeout"))
        except Exception:  # pylint: disable=broad-exception-caught
            # Exception text and remote bodies can contain credentials. Do not log them.
            result = ProviderResponse(failure=ProviderFailure(code="unavailable"))
        logger.debug(
            "metadata_operation provider=%s operation=%s elapsed=%.3f outcome=%s",
            provider.id,
            operation,
            time.monotonic() - started,
            result.failure.code if result.failure else "success",
        )
        return result

    async def _search(self, session: SearchSession) -> None:
        tasks = []
        for provider in session.providers:
            if not provider.declaration.operations.search:
                continue
            if provider.state in {ProviderHealth.DISABLED, ProviderHealth.NOT_CONFIGURED}:
                continue
            session.provider_states[provider.id] = "searching"
            session.emit("provider_started", provider_id=provider.id, name=provider.name)
            tasks.append(asyncio.create_task(self._search_provider(session, provider)))
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            raise
        if not session.cancelled:
            session.state = "degraded" if session.failed else "completed"
            session.emit("search_degraded" if session.failed else "search_completed")

    def _timeout(self, session: SearchSession) -> float:
        return 25 if session.request.policy == "background" else self.operation_timeout

    async def _cached_search(self, provider: Provider, cache_key: tuple) -> ProviderResponse | None:
        cached = self.cache.get(cache_key)
        response = cached[1] if cached and cached[0] > time.monotonic() else None
        # Cached data never bypasses current grants or lifecycle validation.
        if response is not None and isinstance(
            provider, (PluginMetadataProvider, CoreMetadataProvider)
        ):
            try:
                if not await bounded(provider.authorized("search"), self.operation_timeout):
                    response = ProviderResponse(failure=ProviderFailure(code="plugin_unavailable"))
            except Exception:  # pylint: disable=broad-exception-caught
                response = ProviderResponse(failure=ProviderFailure(code="plugin_unavailable"))
        return response

    async def _search_pages(
        self, session: SearchSession, provider: Provider
    ) -> AsyncIterator[ProviderResponse]:
        deadline = time.monotonic() + self._timeout(session)
        request = session.request
        seen: set[str] = set()
        for _ in range(256):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield ProviderResponse(failure=ProviderFailure(code="timeout"))
                return
            response = await self._call(
                session, provider, "search", request=request, timeout=remaining
            )
            yield response
            if response.failure or not response.next_cursor:
                return
            if response.next_cursor in seen:
                yield ProviderResponse(failure=ProviderFailure(code="invalid_response"))
                return
            seen.add(response.next_cursor)
            request = request.model_copy(update={"cursor": response.next_cursor})
        yield ProviderResponse(failure=ProviderFailure(code="invalid_response"))

    def _accept_candidates(
        self,
        session: SearchSession,
        provider: Provider,
        candidates: Sequence[MetadataCandidate],
        position: int,
    ) -> None:
        for index, candidate in enumerate(candidates):
            if candidate.media_type != session.request.media_type:
                continue
            candidate = candidate.model_copy(update={"provider": provider.id})
            key = candidate_id(candidate)
            existing = session.sources.get(key)
            session.sources[key] = Source(
                candidate,
                provider.priority,
                position + index,
                existing.metadata if existing else {},
            )
            self._rank(session)

    async def _search_provider(self, session: SearchSession, provider: Provider) -> None:
        cache_key = (
            session.request.user_id,
            session.request.media_type,
            session.request.query.strip().casefold(),
            session.request.limit,
            tuple(sorted(session.request.options.items())),
            provider.id,
            provider.revision,
        )
        cached = await self._cached_search(provider, cache_key)
        candidates: list[MetadataCandidate] = []

        async def pages():
            if cached is not None:
                yield cached
            else:
                async for page in self._search_pages(session, provider):
                    yield page

        async for response in pages():
            if session.cancelled:
                return
            if response.failure:
                self._failure(session, provider, response.failure)
                return
            incoming = response.candidates[: max(0, session.request.limit - len(candidates))]
            self._accept_candidates(session, provider, incoming, len(candidates))
            candidates.extend(incoming)
            if len(candidates) >= session.request.limit:
                break
        if cached is None:
            # A cache hit must not keep frequently reused provider data stale forever.
            self.cache[cache_key] = (
                time.monotonic() + SEARCH_CACHE_SECONDS,
                ProviderResponse(candidates=tuple(candidates)),
            )
            while len(self.cache) > SEARCH_CACHE_CAPACITY:
                self.cache.popitem(last=False)
        session.provider_states[provider.id] = "finished"
        session.emit("provider_finished", provider_id=provider.id, name=provider.name)

    @staticmethod
    def _failure(session: SearchSession, provider: Provider, failure: ProviderFailure) -> None:
        session.failed = True
        session.provider_states[provider.id] = failure.code
        session.emit(
            "provider_failed",
            provider_id=provider.id,
            name=provider.name,
            status=failure.code,
            message=f"{provider.name} is unavailable",
        )

    @staticmethod
    def _metadata(session: SearchSession, members: Sequence[Source]) -> dict[str, Any]:
        priorities = {provider.id: provider.priority for provider in session.providers}
        patches = sorted(
            (
                (priorities.get(provider_id, source.priority), provider_id, patch)
                for source in members
                for provider_id, patch in source.metadata.items()
            ),
            key=lambda item: (item[0], item[1]),
        )
        metadata: dict[str, Any] = {}
        for _, _, patch in patches:
            for name, value in patch.model_dump(mode="json").items():
                if value is None or value == [] or value == {} or value == "":
                    continue
                if name in {"provider_ids", "scores", "titles"}:
                    for namespace, external_id in value.items():
                        metadata.setdefault(name, {}).setdefault(namespace, external_id)
                elif name == "episodes":
                    MetadataHandler._merge_episodes(metadata.setdefault(name, {}), value)
                else:
                    metadata.setdefault(name, value)
        if "episodes" in metadata:
            metadata["episodes"] = sorted(
                metadata["episodes"].values(),
                key=lambda episode: (episode.get("season_number") or 0, episode["episode_number"]),
            )
        return metadata

    @staticmethod
    def _merge_episodes(entries: dict[tuple, dict], episodes: list[dict]) -> None:
        for episode in episodes:
            key = (episode.get("season_number"), episode["episode_number"])
            target = entries.setdefault(key, {})
            for name, value in episode.items():
                if value is not None and value != "":
                    target.setdefault(name, value)

    def _identity(self, session: SearchSession, source: Source) -> MetadataCandidate:
        metadata = self._metadata(session, [source])
        fields = {
            name: metadata[name]
            for name in ("title", "year", "alternate_titles", "platforms")
            if name in metadata
        }
        fields["provider_ids"] = {
            **source.candidate.provider_ids,
            **metadata.get("provider_ids", {}),
        }
        return MetadataCandidate.model_validate(
            {
                **source.candidate.model_dump(mode="json"),
                **fields,
            }
        )

    def _ranked_groups(self, session: SearchSession) -> list[list[str]]:
        identities_by_key = {
            key: self._identity(session, source) for key, source in session.sources.items()
        }
        grouped: list[list[str]] = []
        sources = sorted(
            session.sources,
            key=lambda key: (
                session.sources[key].priority,
                identities_by_key[key].provider,
                identities_by_key[key].external_id,
            ),
        )
        for key in sources:
            match = next(
                (
                    group
                    for group in grouped
                    if all(
                        same_entity(identities_by_key[key], identities_by_key[member])
                        for member in group
                    )
                ),
                None,
            )
            if match is None:
                grouped.append([key])
            else:
                match.append(key)
        return sorted(
            grouped,
            key=lambda group: min(
                rank_key(
                    session.request.query,
                    identities_by_key[key],
                    session.sources[key].priority,
                    session.sources[key].position,
                )
                for key in group
            ),
        )

    def _record(self, session: SearchSession, group: list[str], rank: int) -> dict[str, Any]:
        representative = self._identity(session, session.sources[group[0]])
        metadata = self._metadata(session, [session.sources[key] for key in group])
        provider_ids: dict[str, str] = {}
        for key in group:
            for namespace, external_id in identities(
                self._identity(session, session.sources[key])
            ).items():
                provider_ids.setdefault(namespace, external_id)
        return {
            **representative.model_dump(mode="json"),
            "id": min(group),
            "provider_name": next(
                (
                    provider.name
                    for provider in session.providers
                    if provider.id == representative.provider
                ),
                "Library",
            ),
            "rank": rank,
            "provider_ids": provider_ids,
            "metadata": metadata,
            "providers": sorted({session.sources[key].candidate.provider for key in group}),
            "assets": [],
        }

    @staticmethod
    def _retain_group(
        session: SearchSession,
        item: dict[str, Any],
        group: list[str],
        previous: dict[str, dict],
        previous_groups: dict[str, tuple],
    ) -> None:
        assets = []
        for old_id, old_group in previous_groups.items():
            if not set(old_group) & set(group):
                continue
            assets.extend(previous[old_id].get("assets", []))
            if old_id != item["id"]:
                session.redirects[old_id] = item["id"]
                if session.selected == old_id:
                    session.selected = item["id"]
        item["assets"] = sorted(
            {asset["kind"] + "|" + asset["url"]: asset for asset in assets}.values(),
            key=lambda asset: (asset["kind"], asset.get("priority", 100), asset["url"]),
        )

    def _rank(self, session: SearchSession) -> None:
        previous, previous_groups = session.candidates, session.groups
        session.candidates, session.groups = {}, {}
        for rank, group in enumerate(self._ranked_groups(session)):
            item = self._record(session, group, rank)
            self._retain_group(session, item, group, previous, previous_groups)
            group_id = item["id"]
            session.groups[group_id] = tuple(group)
            session.candidates[group_id] = item
            if item != previous.get(group_id):
                session.emit(
                    "result_updated" if group_id in previous else "result_added", result=item
                )
        for old_id in previous.keys() - session.candidates.keys():
            session.emit(
                "result_removed", candidate_id=old_id, replacement_id=session.redirects.get(old_id)
            )
        session.emit("ranking_updated", candidate_ids=list(session.candidates))
        self._enrich(session)

    def _enrich(self, session: SearchSession) -> None:
        if session.cancelled:
            return
        while True:
            target_ids = (
                [session.selected]
                if session.selected
                else [
                    group_id
                    for group_id, group in session.groups.items()
                    if not all(key in session.preloaded for key in group)
                ][:PRELOAD_CONCURRENCY]
                if len(session.request.query) >= 5
                else []
            )
            pending = self._enrich_targets(session, target_ids)
            finished = [
                group_id for group_id in target_ids if not set(session.groups[group_id]) & pending
            ]
            if session.selected or not finished:
                self._finish_enrichment(session)
                return
            # Every completed result frees a slot; slow results do not block the
            # next entry. Empty/failed operations finish too, without retry loops.
            session.preloaded.update(
                key for group_id in finished for key in session.groups[group_id]
            )

    def _enrich_targets(self, session: SearchSession, target_ids: list[str]) -> set[str]:
        pending: set[str] = set()
        targets = {key for group_id in target_ids for key in session.groups.get(group_id, ())}
        for work_key, task in list(session.work.items()):
            if work_key[1] not in targets:
                task.cancel()
                session.work.pop(work_key, None)
        for group_id in target_ids:
            for provider in session.providers:
                if provider.state in {ProviderHealth.DISABLED, ProviderHealth.NOT_CONFIGURED}:
                    continue
                group = session.groups[group_id]
                key = next(
                    (
                        key
                        for key in group
                        if session.sources[key].candidate.provider == provider.id
                    ),
                    group[0],
                )
                candidate = self._operation_candidate(session, key)
                known_identity = (
                    provider.id == candidate.provider
                    or provider.id in candidate.provider_ids
                    or provider.declaration.identifier_namespace in candidate.provider_ids
                )
                # Episode lists need a resolved provider identity. ID mapping capabilities
                # without their own namespace can still contribute authoritative cross-IDs.
                if (
                    session.request.resource == "episodes"
                    and not known_identity
                    and provider.declaration.identifier_namespace
                ):
                    continue
                operations: tuple[Operation, ...] = (
                    ("metadata", "media")
                    if session.selected and session.media_requested
                    else ("metadata",)
                )
                for operation in operations:
                    if operation == "metadata" and (
                        session.request.resource not in provider.declaration.metadata_resources
                        or (
                            not session.selected
                            and not known_identity
                            and provider.declaration.operations.search
                        )
                    ):
                        continue
                    work_key = (provider.id, key, operation)
                    if work_key in session.unresolved and session.unresolved[work_key] != tuple(
                        sorted(candidate.provider_ids.items())
                    ):
                        session.completed.discard(work_key)
                        session.unresolved.pop(work_key)
                    if (
                        not getattr(provider.declaration.operations, operation)
                        or work_key in session.completed
                    ):
                        continue
                    pending.add(key)
                    if work_key in session.work or (
                        not session.selected and len(session.work) >= PRELOAD_CONCURRENCY
                    ):
                        continue
                    session.enrichment_finished = False
                    session.work[work_key] = asyncio.create_task(
                        self._enrich_one(session, provider, key, operation)
                    )
        return pending

    def _operation_candidate(self, session: SearchSession, key: str) -> MetadataCandidate:
        identity = self._identity(session, session.sources[key])
        group_id = next(
            (group_id for group_id, group in session.groups.items() if key in group), None
        )
        if group_id is None:
            return identity
        return identity.model_copy(
            update={"provider_ids": session.candidates[group_id]["provider_ids"]}
        )

    @staticmethod
    def _finish_enrichment(session: SearchSession) -> None:
        if (
            session.selected
            and not session.work
            and not session.cancelled
            and not session.enrichment_finished
        ):
            session.enrichment_finished = True
            session.emit("selection_enrichment_completed", candidate_id=session.selected)

    async def _enrichment_pages(
        self, session: SearchSession, provider: Provider, key: str, operation: Operation
    ) -> AsyncIterator[ProviderResponse]:
        request = session.request
        deadline = time.monotonic() + self._timeout(session)
        cursors: set[str] = set()
        for _ in range(256):
            if deadline <= time.monotonic():
                yield ProviderResponse(failure=ProviderFailure(code="timeout"))
                return
            response = await self._call(
                session,
                provider,
                operation,
                self._operation_candidate(session, key),
                request=request,
                timeout=deadline - time.monotonic(),
            )
            yield response
            if response.failure or not response.next_cursor:
                return
            if response.next_cursor in cursors:
                yield ProviderResponse(failure=ProviderFailure(code="invalid_response"))
                return
            cursors.add(response.next_cursor)
            request = request.model_copy(update={"cursor": response.next_cursor})
        yield ProviderResponse(failure=ProviderFailure(code="invalid_response"))

    @staticmethod
    def _append_patch(source: Source, provider_id: str, patch: MetadataPatch) -> None:
        previous = source.metadata.get(provider_id)
        if previous is None:
            source.metadata[provider_id] = patch
            return
        # Pages belong to one ordered operation; absent fields preserve earlier pages.
        values = previous.model_dump(mode="json")
        for name, value in patch.model_dump(mode="json").items():
            if value is None or value == [] or value == {} or value == "":
                continue
            if name == "episodes":
                entries: dict[tuple, dict] = {}
                MetadataHandler._merge_episodes(entries, values.get(name, []) + value)
                values[name] = list(entries.values())
            elif name == "provider_ids":
                values[name] = {**values.get(name, {}), **value}
            elif name == "relations":
                entries = {
                    (
                        entry["candidate"]["provider"],
                        entry["candidate"]["external_id"],
                        entry.get("group"),
                        entry.get("parent_external_id"),
                    ): entry
                    for entry in values.get(name, []) + value
                }
                values[name] = list(entries.values())
            else:
                values[name] = value
        source.metadata[provider_id] = MetadataPatch.model_validate(values)

    async def _enrich_one(
        self, session: SearchSession, provider: Provider, key: str, operation: Operation
    ) -> None:
        work_key = (provider.id, key, operation)
        try:
            received_data = False
            async for response in self._enrichment_pages(session, provider, key, operation):
                if session.cancelled or session.work.get(work_key) is not asyncio.current_task():
                    return
                if response.failure:
                    self._failure(session, provider, response.failure)
                    break
                if operation == "metadata" and response.metadata:
                    received_data = True
                    self._append_patch(session.sources[key], provider.id, response.metadata)
                    self._rank(session)
                elif operation == "media":
                    received_data = received_data or bool(response.assets)
                    self._append_assets(session, key, response)
            session.completed.add(work_key)
            if not received_data:
                identity_ids = self._operation_candidate(session, key).provider_ids
                session.unresolved[work_key] = tuple(sorted(identity_ids.items()))
        except ValueError:
            # A valid page can still exceed a bounded aggregate. Keep earlier data
            # and classify the response rather than leaking a task exception.
            session.completed.add(work_key)
            self._failure(session, provider, ProviderFailure(code="invalid_response"))
        finally:
            if session.work.get(work_key) is asyncio.current_task():
                session.work.pop(work_key, None)
                self._enrich(session)

    @staticmethod
    def _append_assets(session: SearchSession, key: str, response: ProviderResponse) -> None:
        group_id = next(
            (group_id for group_id, group in session.groups.items() if key in group), None
        )
        if group_id is None:
            return
        item = session.candidates[group_id]
        assets = item["assets"] + [asset.model_dump(mode="json") for asset in response.assets]
        item["assets"] = sorted(
            {asset["kind"] + "|" + asset["url"]: asset for asset in assets}.values(),
            key=lambda asset: (asset["kind"], asset.get("priority", 100), asset["url"]),
        )
        session.emit("result_updated", result=item)

    async def enrich(
        self,
        request: MetadataProviderRequest,
        providers: Sequence[Provider],
        candidate: MetadataCandidate,
        *,
        include_media: bool = False,
    ) -> tuple[dict, list[str]]:
        """Background and compatibility callers use the same focused lifecycle and deadlines."""
        session = self.focus(request, providers, candidate, include_media=include_media)
        try:
            async with asyncio.timeout(self._timeout(session) + 1):
                while not session.enrichment_finished:
                    session.changed.clear()
                    await session.changed.wait()
        except TimeoutError:
            session.emit("provider_failed", message="Metadata providers are unavailable")
        record = session.candidates.get(session.selected or "", {})
        failures = list(
            dict.fromkeys(
                event["message"] for event in session.events if event["event"] == "provider_failed"
            )
        )
        self.cancel(session)
        self.sessions.pop(session.id, None)
        return record, failures

    def select(self, session: SearchSession, selected_id: str) -> dict[str, Any]:
        """Selection succeeds immediately; media provider failure cannot undo it."""
        while selected_id in session.redirects:
            selected_id = session.redirects[selected_id]
        if selected_id not in session.candidates:
            raise LookupError("metadata candidate not found")
        # A user may select a locally retained candidate while the next query is pending.
        # Resume focused enrichment only; the cancelled query itself never resumes.
        if session.cancelled:
            session.cancelled = False
            session.state = "selected"
        session.selected = selected_id
        session.enrichment_finished = False
        session.emit("candidate_selected", candidate_id=selected_id)
        self._enrich(session)
        return session.candidates[selected_id]


handler = MetadataHandler()
