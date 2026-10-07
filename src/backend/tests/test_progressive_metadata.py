"""Public orchestration behavior with gated provider callbacks, without external API claims."""

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from src.features.metadata.handler import MetadataHandler
from src.features.metadata.identity import same_entity
from src.plugin_api.metadata_contracts import (
    MediaAsset,
    MetadataCandidate,
    MetadataPatch,
    MetadataProviderRegistration,
    MetadataProviderRequest,
    ProviderFailure,
    ProviderHealth,
    ProviderOperations,
    ProviderResponse,
)


@dataclass
class ContractProvider:
    id: str
    candidates: tuple[MetadataCandidate, ...] = ()
    priority: int = 100
    state: ProviderHealth = ProviderHealth.HEALTHY
    revision: str = "test-context"
    gates: dict[str, asyncio.Event] = field(default_factory=dict)
    calls: list[tuple[str, str | None]] = field(default_factory=list)
    cancelled: list[tuple[str, str | None]] = field(default_factory=list)
    failure: ProviderFailure | None = None

    @property
    def name(self):
        return self.id

    @property
    def declaration(self):
        return MetadataProviderRegistration(
            provider_id=self.id,
            name=self.id,
            media_types=("game",),
            operations=ProviderOperations(
                search="search", metadata="metadata", media="media", health="health"
            ),
        )

    async def invoke(self, operation, request, candidate=None):
        identity = candidate.external_id if candidate else None
        self.calls.append((operation, identity))
        try:
            if operation in self.gates:
                await self.gates[operation].wait()
        except asyncio.CancelledError:
            self.cancelled.append((operation, identity))
            raise
        if self.failure:
            return ProviderResponse(failure=self.failure)
        if operation == "search":
            return ProviderResponse(candidates=self.candidates)
        if operation == "metadata":
            return ProviderResponse(metadata=MetadataPatch(description="Useful partial metadata"))
        if operation == "media":
            return ProviderResponse(
                assets=(MediaAsset(kind="key_art", url="https://example.test/cover.png"),)
            )
        return ProviderResponse(health=ProviderHealth.HEALTHY)


def request(query="Portal"):
    return MetadataProviderRequest(request_id=uuid4(), user_id=uuid4(), query=query)


def candidate(title="Portal", external_id="1", provider="test.provider", year=None, **fields):
    return MetadataCandidate(
        title=title, external_id=external_id, provider=provider, year=year, **fields
    )


async def eventually(condition):
    async with asyncio.timeout(1):
        while not condition():
            await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_fast_results_are_visible_while_slow_provider_is_running():
    gate = asyncio.Event()
    fast = ContractProvider("test.fast", (candidate(),))
    slow = ContractProvider("test.slow", gates={"search": gate})
    handler = MetadataHandler()
    session = handler.start(request("por"), [slow, fast])
    await eventually(lambda: len(session.candidates) == 1)
    assert session.provider_states[slow.id] == "searching"
    assert ("search", None) in slow.calls
    assert any(event["event"] == "result_added" for event in session.events)
    gate.set()
    await session.search_task
    assert session.state == "completed"
    handler.cancel(session)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["unavailable", "invalid_configuration", "rate_limited"])
async def test_failure_is_data_and_does_not_destroy_fast_results(code):
    handler = MetadataHandler()
    good = ContractProvider("test.good", (candidate(),))
    broken = ContractProvider("test.bad", failure=ProviderFailure(code=code))
    session = handler.start(request("por"), [broken, good])
    await session.search_task
    assert len(session.candidates) == 1
    assert session.state == "degraded"
    failure = next(event for event in session.events if event["event"] == "provider_failed")
    assert failure["message"] == "test.bad is unavailable"
    assert failure["status"] == code
    handler.cancel(session)


@pytest.mark.asyncio
async def test_hard_deadline_survives_a_provider_suppressing_cancellation():
    gate = asyncio.Event()
    started = asyncio.Event()

    class UncooperativeProvider(ContractProvider):
        async def invoke(self, operation, request, candidate=None):
            started.set()
            try:
                await gate.wait()
            except asyncio.CancelledError:
                await gate.wait()
            return ProviderResponse(candidates=(globals()["candidate"]("Late"),))

    handler = MetadataHandler(operation_timeout=0.02)
    session = handler.start(
        request("por"),
        [UncooperativeProvider("test.hanging"), ContractProvider("test.fast", (candidate(),))],
    )
    await started.wait()
    await asyncio.wait_for(session.search_task, 0.2)
    assert session.provider_states["test.hanging"] == "timeout"
    assert len(session.candidates) == 1
    gate.set()
    await asyncio.sleep(0.01)
    assert all(item["title"] != "Late" for item in session.candidates.values())
    handler.cancel(session)


@pytest.mark.asyncio
async def test_new_session_cannot_receive_old_results_and_is_owner_scoped():
    gate = asyncio.Event()
    handler = MetadataHandler()
    first = handler.start(
        request("car"), [ContractProvider("test.old", (candidate("Car"),), gates={"search": gate})]
    )
    await eventually(lambda: bool(first.provider_states))
    handler.cancel(first)
    second = handler.start(
        request("caram"), [ContractProvider("test.new", (candidate("Caramel"),))]
    )
    gate.set()
    await second.search_task
    assert not first.candidates
    assert [item["title"] for item in second.candidates.values()] == ["Caramel"]
    with pytest.raises(LookupError):
        handler.owned(second.id, uuid4())
    handler.cancel(second)


@pytest.mark.asyncio
async def test_better_late_match_changes_rank_and_provider_arrival_does_not():
    gate = asyncio.Event()
    handler = MetadataHandler()
    weak = ContractProvider("test.first", (candidate("Portal Stories"),))
    strong = ContractProvider("test.last", (candidate("Portal"),), gates={"search": gate})
    session = handler.start(request(), [weak, strong])
    await eventually(lambda: len(session.candidates) == 1)
    gate.set()
    await session.search_task
    assert next(iter(session.candidates.values()))["title"] == "Portal"
    opposite = handler.start(request(), [strong, weak])
    await opposite.search_task
    assert [item["id"] for item in session.candidates.values()] == list(opposite.candidates)
    handler.cancel(session)
    handler.cancel(opposite)


@pytest.mark.parametrize(
    "other",
    [
        candidate("Portal", provider="other", year=None),
        candidate("Portal", provider="other", year=2012),
        candidate("Portal", provider="other", year=2011, media_type="movie"),
        candidate("Portal 2", provider="other", year=2011),
    ],
)
def test_similar_titles_without_identity_corroboration_do_not_merge(other):
    assert not same_entity(candidate("Portal", year=2011), other)


def test_shared_provider_ids_merge_but_conflicting_ids_do_not():
    first = candidate("Portal", provider_ids={"steam": "620"})
    assert same_entity(
        first, candidate("Alternate title", provider="other", provider_ids={"steam": "620"})
    )
    assert not same_entity(
        first, candidate("Portal", provider="other", provider_ids={"steam": "621"})
    )
    assert same_entity(
        candidate("Portal-2", year=2011), candidate("Portal 2", provider="other", year=2011)
    )


@pytest.mark.asyncio
async def test_no_metadata_below_five_characters_and_no_unselected_media():
    provider = ContractProvider("test.provider", (candidate(),))
    handler = MetadataHandler()
    session = handler.start(request("port"), [provider])
    await session.search_task
    await asyncio.sleep(0.01)
    assert provider.calls == [("search", None)]
    handler.cancel(session)


@pytest.mark.asyncio
async def test_preload_window_change_cancels_demotion_and_preserves_completed_metadata():
    search_gate = asyncio.Event()
    metadata_gate = asyncio.Event()
    initial = ContractProvider(
        "test.initial",
        tuple(
            candidate(title, str(index))
            for index, title in enumerate(("Portal A", "Portal B", "Portal C", "Portal D"))
        ),
        gates={"metadata": metadata_gate},
    )
    better = ContractProvider(
        "test.better", (candidate("Portal", "best"),), gates={"search": search_gate}
    )
    handler = MetadataHandler()
    session = handler.start(request(), [initial, better])
    await eventually(lambda: len([call for call in initial.calls if call[0] == "metadata"]) == 4)
    assert set(call[1] for call in initial.calls if call[0] == "metadata") == {"0", "1", "2", "3"}
    search_gate.set()
    await session.search_task
    await eventually(lambda: ("metadata", "3") in initial.cancelled)
    metadata_gate.set()
    await eventually(lambda: not session.work)
    assert len(session.candidates) == 5
    assert all(
        item["metadata"].get("description") for item in list(session.candidates.values())[:3]
    )
    assert not any(call[0] == "media" for call in initial.calls + better.calls)
    calls_before = list(initial.calls)
    handler._rank(session)
    await asyncio.sleep(0.01)
    assert initial.calls == calls_before
    handler.cancel(session)


@pytest.mark.asyncio
async def test_preloading_refills_four_slots_without_waiting_for_slow_results_or_fetching_media():
    gates = {str(index): asyncio.Event() for index in range(7)}

    class RollingProvider(ContractProvider):
        async def invoke(self, operation, request, candidate=None):
            if operation != "metadata":
                return await super().invoke(operation, request, candidate)
            self.calls.append((operation, candidate.external_id))
            await gates[candidate.external_id].wait()
            return ProviderResponse(metadata=MetadataPatch(description=candidate.title))

    provider = RollingProvider(
        "test.rolling", tuple(candidate(f"Toaster {index}", str(index)) for index in range(7))
    )
    handler = MetadataHandler()
    session = handler.start(request("toaster"), [provider])
    await session.search_task

    def preloading_calls():
        return [identity for operation, identity in provider.calls if operation == "metadata"]

    await eventually(lambda: len(preloading_calls()) == 4)
    assert preloading_calls() == ["0", "1", "2", "3"]
    assert len(session.work) == 4
    gates["0"].set()
    await eventually(lambda: len(preloading_calls()) == 5)
    assert preloading_calls() == ["0", "1", "2", "3", "4"]
    assert len(session.work) == 4
    gates["4"].set()
    await eventually(lambda: len(preloading_calls()) == 6)
    assert preloading_calls() == ["0", "1", "2", "3", "4", "5"]
    assert len(session.work) == 4
    gates["1"].set()
    gates["2"].set()
    await eventually(lambda: len(preloading_calls()) == 7)
    for index in ("3", "5"):
        gates[index].set()
    gates["6"].set()
    await eventually(lambda: not session.work)
    assert all(item["metadata"].get("description") for item in session.candidates.values())
    assert all(not item["assets"] for item in session.candidates.values())
    assert not any(operation == "media" for operation, _ in provider.calls)
    handler._rank(session)
    assert len(preloading_calls()) == 7
    handler.cancel(session)


@pytest.mark.asyncio
async def test_failed_preload_frees_a_slot_while_other_results_are_still_pending():
    gate = asyncio.Event()

    class PartialProvider(ContractProvider):
        async def invoke(self, operation, request, candidate=None):
            if operation != "metadata":
                return await super().invoke(operation, request, candidate)
            self.calls.append((operation, candidate.external_id))
            if candidate.external_id in {"0", "1", "2"}:
                await gate.wait()
            if candidate.external_id == "3":
                return ProviderResponse(failure=ProviderFailure(code="rate_limited"))
            return ProviderResponse(metadata=MetadataPatch(description=candidate.title))

    provider = PartialProvider(
        "test.partial", tuple(candidate(f"Toaster {index}", str(index)) for index in range(8))
    )
    handler = MetadataHandler()
    session = handler.start(request("toaster"), [provider])
    await session.search_task
    await eventually(lambda: len(provider.calls) == 9)
    await eventually(lambda: len(session.work) == 3)
    assert len(session.work) == 3
    assert [identity for operation, identity in provider.calls if operation == "metadata"] == [
        str(index) for index in range(8)
    ]
    assert any(event.get("status") == "rate_limited" for event in session.events)
    assert not list(session.candidates.values())[3]["metadata"]
    assert list(session.candidates.values())[-1]["metadata"]["description"] == "Toaster 7"
    gate.set()
    await eventually(lambda: not session.work)
    handler.cancel(session)


@pytest.mark.asyncio
async def test_cancelled_preload_does_not_refill_slots():
    gate = asyncio.Event()
    provider = ContractProvider(
        "test.rolling",
        tuple(candidate(f"Toaster {index}", str(index)) for index in range(6)),
        gates={"metadata": gate},
    )
    handler = MetadataHandler()
    session = handler.start(request("toaster"), [provider])
    await session.search_task
    await eventually(lambda: len(provider.calls) == 5)
    handler.cancel(session)
    gate.set()
    await asyncio.sleep(0.01)
    assert [identity for operation, identity in provider.calls if operation == "metadata"] == [
        "0",
        "1",
        "2",
        "3",
    ]
    assert not any(item["metadata"] for item in session.candidates.values())


@pytest.mark.asyncio
async def test_selection_is_immediate_and_media_arrives_independently():
    gate = asyncio.Event()
    provider = ContractProvider("test.provider", (candidate(),), gates={"media": gate})
    handler = MetadataHandler()
    session = handler.start(request(), [provider])
    await session.search_task
    await eventually(lambda: not session.work)
    selected = handler.select(session, next(iter(session.candidates)))
    assert selected["title"] == "Portal"
    assert selected["assets"] == []
    await eventually(lambda: ("media", "1") in provider.calls)
    gate.set()
    await eventually(lambda: bool(selected["assets"]))
    assert selected["metadata"]["description"] == "Useful partial metadata"
    handler.cancel(session)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [ProviderHealth.DISABLED, ProviderHealth.NOT_CONFIGURED])
async def test_optional_unavailable_providers_do_not_participate_or_emit_errors(state):
    provider = ContractProvider("test.optional", state=state)
    handler = MetadataHandler()
    session = handler.start(request(), [provider])
    await session.search_task
    assert not provider.calls
    assert not any(event["event"] == "provider_failed" for event in session.events)


@pytest.mark.asyncio
async def test_search_cache_is_scoped_to_query_user_and_configuration():
    provider = ContractProvider("test.provider", (candidate(),))
    handler = MetadataHandler()
    first_request = request("port")
    for _ in range(2):
        session = handler.start(first_request, [provider])
        await session.search_task
        handler.cancel(session)
    assert provider.calls == [("search", None)]
    for next_request in [request("port"), first_request.model_copy(update={"query": "portal"})]:
        session = handler.start(next_request, [provider])
        await session.search_task
        handler.cancel(session)
    provider.revision = "new-configuration"
    session = handler.start(first_request, [provider])
    await session.search_task
    assert len([call for call in provider.calls if call[0] == "search"]) == 4
    handler.cancel(session)
