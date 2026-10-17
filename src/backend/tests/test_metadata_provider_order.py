"""Plugin-aware provider order, artwork authority and provider-use history (#132)."""

from dataclasses import dataclass
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import delete, select
from test_core_metadata import native_boundary  # noqa: F401
from test_metadata_provider_choices import policy_boundary, set_choices  # noqa: F401
from test_metadata_provider_configuration import provider_boundary, register  # noqa: F401
from test_plugin_authorization_http import boundary, grant  # noqa: F401

from src.api.routes import metadata as routes
from src.api.routes import settings as settings_routes
from src.api.routes.utils.games import _record_provider_use
from src.api.schemas.scan_settings import ScanSettingsUpdate
from src.core.auth import session_cookie_name
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.user_scan_settings import UserScanSettings
from src.features.metadata import providers, service
from src.features.metadata.configuration import UNRANKED
from src.features.metadata.handler import MetadataHandler
from src.plugin_api.metadata_contracts import (
    MediaAsset,
    MediaType,
    MetadataCandidate,
    MetadataProviderRegistration,
    MetadataProviderRequest,
    ProviderHealth,
    ProviderOperations,
    ProviderResponse,
)


@pytest.fixture
def order_boundary(policy_boundary):  # noqa: F811 - imported pytest fixture
    """Core providers, one registered plugin provider and per-account scan settings."""
    return policy_boundary


def save_orders(item, user, provider_order, image_provider_order):
    row = item.session.scalar(select(UserScanSettings).where(UserScanSettings.user_id == user.id))
    if row is None:
        row = UserScanSettings(user_id=user.id)
        item.session.add(row)
    row.provider_order = provider_order
    row.image_provider_order = image_provider_order
    item.session.commit()


async def discovered(item, user, **kwargs):
    return {
        provider.id: provider
        for provider in await providers.discover_providers(item.database, user.id, **kwargs)
    }


@dataclass
class FakeProvider:
    """A provider double that only returns what its test declares."""

    id: str
    operations: tuple[str, ...]
    artwork_priority: int = UNRANKED
    asset_priority: int = 100
    priority: int = UNRANKED
    state: ProviderHealth = ProviderHealth.HEALTHY
    revision: str = "test"
    found: tuple[MetadataCandidate, ...] = ()

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
                **{operation: operation for operation in self.operations}, health="health"
            ),
        )

    async def invoke(self, operation, request, candidate=None):
        del request, candidate
        if operation == "search":
            return ProviderResponse(candidates=self.found)
        if operation == "media":
            url = f"https://example.test/{self.id}.png"
            return ProviderResponse(
                assets=(MediaAsset(kind="key_art", url=url, priority=self.asset_priority),)
            )
        return ProviderResponse()


def test_scan_order_accepts_registered_provider_ids_and_legacy_names():
    update = ScanSettingsUpdate(
        provider_order=["official.metadata-gog.gog", "core.steam", "IGDB"],
        image_provider_order=["core.steam", "SteamGridDB", "official.metadata-tmdb.tmdb"],
    )
    assert update.provider_order == ["official.metadata-gog.gog", "core.steam", "IGDB"]
    for invalid in (
        ["core.steam", "core.steam"],
        ["Not a provider"],
        ["SteamGridDB"],
        ["x" * 129],
        [f"plugin.provider-{index}" for index in range(257)],
    ):
        with pytest.raises(ValidationError):
            ScanSettingsUpdate(provider_order=invalid)
    with pytest.raises(ValidationError):
        ScanSettingsUpdate(image_provider_order=["IGDB"])


async def test_saved_provider_ids_rank_core_and_plugin_providers(order_boundary):
    item = order_boundary
    owner, other = item.users
    row = await register(item)
    save_orders(
        item,
        owner,
        [row.provider_id, "core.tvmaze", "core.steam", "core.igdb"],
        ["core.steam", "core.steamgriddb"],
    )
    mine = await discovered(item, owner)
    assert [mine[key].priority for key in (row.provider_id, "core.steam", "core.igdb")] == [0, 2, 3]
    assert mine["core.tvmaze"].priority == 1
    assert mine["core.anilist"].priority == UNRANKED
    assert mine["core.steam"].artwork_priority == 0
    assert mine["core.steamgriddb"].artwork_priority == 1
    assert mine[row.provider_id].artwork_priority == UNRANKED
    # Each account keeps its own order; an unsaved account uses the defaults.
    theirs = await discovered(item, other)
    assert theirs["core.igdb"].priority < theirs["core.steam"].priority
    assert theirs[row.provider_id].priority == UNRANKED
    # Explicit caller preferences still take precedence over the saved row.
    explicit = await discovered(
        item, owner, preferences={"provider_order": ["core.igdb"], "image_provider_order": []}
    )
    assert explicit["core.igdb"].priority == 0
    assert explicit[row.provider_id].priority == UNRANKED
    status = await routes.providers_status(item.database, owner)
    ranks = {
        provider["provider_id"]: (provider["priority"], provider["artwork_priority"])
        for provider in status["providers"]
    }
    assert ranks[row.provider_id] == (0, UNRANKED)
    assert ranks["core.steam"] == (2, 0)


async def test_background_and_selection_paths_use_the_saved_order(order_boundary, monkeypatch):
    item = order_boundary
    owner = item.users[0]
    row = await register(item)
    save_orders(item, owner, [row.provider_id, "core.steam"], ["core.steam"])
    enrich = AsyncMock(return_value=({}, []))
    monkeypatch.setattr(service.handler, "enrich", enrich)
    candidate = service.library_candidate("A title", MediaType.GAME, {"steam": "1"})
    # Scheduled and refresh work pass no scan preferences of their own.
    await service.collect_record(item.database, owner.id, candidate)
    ranks = {provider.id: provider.priority for provider in enrich.await_args.args[1]}
    assert ranks[row.provider_id] == 0
    assert ranks["core.steam"] == 1
    assert ranks["core.igdb"] == UNRANKED


async def test_legacy_names_keep_their_order_but_plugins_cannot_claim_core_names(
    order_boundary,
):
    item = order_boundary
    owner = item.users[0]
    item.declaration["name"] = "GiantBomb"
    giant = await register(item)
    await providers.register_provider(
        item.database,
        item.plugin["plugin_id"],
        item.installation_id,
        {**item.declaration, "provider_id": "audit.plugin.impostor", "name": "Steam"},
    )
    await providers.register_provider(
        item.database,
        item.plugin["plugin_id"],
        item.installation_id,
        {**item.declaration, "provider_id": "audit.plugin.alias", "name": "core.steam"},
    )
    save_orders(item, owner, ["GiantBomb", "Steam", "core.steam"], ["SteamGridDB", "Steam"])
    mine = await discovered(item, owner)
    assert mine[giant.provider_id].priority == 0
    assert mine["core.steam"].priority == 1
    assert mine["audit.plugin.impostor"].priority == UNRANKED
    # Display names match only the fixed legacy names, never another provider's ID.
    assert mine["audit.plugin.alias"].priority == UNRANKED
    assert mine["core.steamgriddb"].artwork_priority == 0
    assert mine["core.steam"].artwork_priority == 1
    assert mine["audit.plugin.impostor"].artwork_priority == UNRANKED


async def test_unsaved_accounts_use_defaults_without_creating_scan_settings(order_boundary):
    item = order_boundary
    mine = await discovered(item, item.users[0])
    assert mine["core.igdb"].priority == 0
    assert mine["core.steam"].priority == 2
    assert mine["core.steamgriddb"].artwork_priority == 0
    assert mine["core.steam"].artwork_priority == UNRANKED
    assert item.session.scalar(select(UserScanSettings)) is None


async def test_plugin_lifecycle_keeps_server_state_visible_and_order_by_identity(
    order_boundary,
):
    item = order_boundary
    owner = item.users[0]
    row = await register(item)
    save_orders(item, owner, ["core.steam", row.provider_id], [])

    async def entry():
        status = await routes.providers_status(item.database, owner)
        return next((p for p in status["providers"] if p["provider_id"] == row.provider_id), None)

    running = await entry()
    assert (running["state"], running["priority"]) == ("not_configured", 1)
    item.plugin["enabled"] = False
    disabled = await entry()
    assert (disabled["state"], disabled["account_enabled"], disabled["priority"]) == (
        "disabled",
        True,
        1,
    )
    item.plugin["enabled"] = True
    item.runtime.plugins.return_value = []
    assert (await entry())["state"] == "plugin_unavailable"
    # An account opt-out keeps the provider's saved position for re-enabling.
    item.runtime.plugins.return_value = [item.plugin]
    set_choices(item, owner, {row.provider_id: False})
    opted_out = await entry()
    assert (opted_out["account_enabled"], opted_out["priority"]) == (False, 1)
    set_choices(item, owner, {})
    # Uninstalling purges grants, which removes the provider from discovery.
    item.session.execute(
        delete(PluginPermissionGrant).where(PluginPermissionGrant.plugin_id == "audit.plugin")
    )
    item.session.commit()
    assert await entry() is None
    # A reinstall registers the same provider ID for a new installation and keeps its rank.
    reinstalled = uuid4()
    item.plugin["installation_id"] = str(reinstalled)
    for capability in ("register", "configuration", "search", "metadata", "health"):
        grant(item, "metadata_providers." + capability, installation_id=reinstalled)
    await providers.register_provider(
        item.database, item.plugin["plugin_id"], reinstalled, item.declaration
    )
    restored = await entry()
    assert (restored["state"], restored["priority"]) == (running["state"], 1)


async def test_artwork_order_decides_between_providers_before_their_own_ranking():
    providers_in_session = [
        FakeProvider("test.unranked-late", ("media",), asset_priority=5),
        FakeProvider("test.second", ("media",), artwork_priority=1, asset_priority=0),
        FakeProvider("test.unranked-early", ("media",), asset_priority=1),
        FakeProvider("test.first", ("media",), artwork_priority=0, asset_priority=90),
    ]
    library_identity = service.library_candidate("Portal", MediaType.GAME, {})
    record, failures = await MetadataHandler().enrich(
        MetadataProviderRequest(request_id=uuid4(), user_id=uuid4(), query="Portal"),
        providers_in_session,
        library_identity,
        include_media=True,
    )
    assert failures == []
    assert [asset["url"].rsplit("/", 1)[1] for asset in record["assets"]] == [
        "test.first.png",
        "test.second.png",
        "test.unranked-early.png",
        "test.unranked-late.png",
    ]
    # The library identity itself is not a provider and is never recorded as used.
    assert record["contributors"] == sorted(provider.id for provider in providers_in_session)


async def test_selecting_a_result_records_contributing_provider_ids(order_boundary):
    item = order_boundary
    owner = item.users[0]
    result = MetadataCandidate(
        title="Portal", external_id="7", provider="test.search", media_type="game"
    )
    session = routes.handler.start(
        MetadataProviderRequest(request_id=uuid4(), user_id=owner.id, query="Por"),
        [
            FakeProvider("test.search", ("search",), found=(result,)),
            FakeProvider("test.empty", ("search",)),
        ],
    )
    try:
        await session.search_task
        selected_id = next(iter(session.candidates))
        selected = await routes.select_candidate(
            session.id,
            routes.SelectionInput(candidate_id=UUID(selected_id)),
            item.database,
            owner,
        )
        assert selected["id"] == selected_id
    finally:
        routes.handler.cancel(session)
        routes.handler.sessions.pop(session.id, None)
    row = item.session.scalar(select(UserScanSettings).where(UserScanSettings.user_id == owner.id))
    assert set(row.provider_last_used) == {"test.search"}


async def test_compatibility_search_reports_only_providers_that_supplied_results(
    order_boundary, monkeypatch
):
    item = order_boundary
    owner = item.users[0]
    result = MetadataCandidate(
        title="Portal", external_id="7", provider="test.search", media_type="game"
    )
    monkeypatch.setattr(
        service,
        "discover_providers",
        AsyncMock(
            return_value=[
                FakeProvider("test.search", ("search",), found=(result,)),
                FakeProvider("test.empty", ("search",)),
            ]
        ),
    )
    monkeypatch.setattr(service.monitor, "available", lambda provider: provider)
    response = await service.search_records(item.database, owner.id, "Portal", MediaType.GAME)
    assert response["used_provider_ids"] == ["test.search"]
    media = await service.search_media(item.database, owner.id, "Portal", MediaType.GAME)
    assert "used_provider_ids" not in media
    settings = UserScanSettings(user_id=owner.id, provider_last_used={"Steam": 1})
    _record_provider_use(settings, response["used_provider_ids"])
    assert settings.provider_last_used["Steam"] == 1
    assert settings.provider_last_used["test.search"] > 1


async def test_any_account_saves_its_own_plugin_aware_order(order_boundary):
    item = order_boundary
    owner, member = item.users
    member.is_admin = False
    item.session.commit()
    # Host routes take precedence over the plugin catch-all, as in the application.
    plugin_routes = list(item.app.router.routes)
    item.app.router.routes.clear()
    item.app.include_router(settings_routes.router)
    item.app.router.routes.extend(plugin_routes)
    order = ["official.metadata-gog.gog", "core.steam", "IGDB"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=item.app), base_url="http://test"
    ) as client:
        client.cookies.set(session_cookie_name("test"), item.tokens[member.id])
        saved = await client.put(
            "/api/settings/scan",
            json={"provider_order": order, "image_provider_order": ["core.steamgriddb"]},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["provider_order"] == order
        rejected = await client.put("/api/settings/scan", json={"provider_order": ["Bad name"]})
        assert rejected.status_code == 422
    rows = {
        row.user_id: row.provider_order for row in item.session.scalars(select(UserScanSettings))
    }
    assert rows == {member.id: order}
    assert owner.id not in rows
