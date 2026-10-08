"""Application entry points shared by interactive compatibility and refresh callers."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.session import SessionLocal
from src.plugin_api.metadata_contracts import (
    MediaType,
    MetadataCandidate,
    MetadataProviderRequest,
    ProviderHealth,
)

from .handler import handler
from .health import monitor
from .identity import normalized_title
from .providers import discover_providers


async def search_records(
    db: AsyncSession,
    user_id: UUID,
    query: str,
    media_type: MediaType,
    limit: int = 20,
    *,
    preferences: dict | None = None,
) -> dict[str, Any]:
    """Keep older request/response clients on the same provider discovery and search path.

    Search returns identities and whatever metadata has already arrived. It never selects
    candidates or requests artwork. New clients consume the progressive session API.
    """
    providers = [
        monitor.available(provider)
        for provider in await discover_providers(db, user_id, media_type, preferences)
    ]
    session = handler.start(
        MetadataProviderRequest(
            request_id=uuid4(),
            user_id=user_id,
            query=query,
            media_type=media_type,
            limit=limit,
            options={"steam_user_tags": (preferences or {}).get("steam_user_tags", True)},
        ),
        providers,
    )
    try:
        if session.search_task:
            await session.search_task
        return {
            "query": query,
            "providers": [
                provider.name for provider in providers if provider.id in session.provider_states
            ],
            "provider_errors": list(
                dict.fromkeys(
                    event["message"]
                    for event in session.events
                    if event["event"] == "provider_failed"
                )
            ),
            "results": list(session.candidates.values())[:limit],
        }
    finally:
        handler.cancel(session)
        handler.sessions.pop(session.id, None)


async def collect_record(
    db: AsyncSession,
    user_id: UUID,
    candidate: MetadataCandidate,
    *,
    policy: Literal["interactive", "background"] = "background",
    resource: Literal["entity", "episodes", "airing", "relations", "recommendations"] = "entity",
    season_number: int | None = None,
    include_media: bool = False,
    preferences: dict | None = None,
    episode_phase: Literal["primary", "fallback"] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """An explicit library identity uses the same focused enrichment lifecycle.

    The caller retains domain persistence, field locks and watched/progress ownership.
    Providers retain retrieval and authentication policy.
    """
    providers = [
        monitor.available(provider)
        for provider in await discover_providers(db, user_id, candidate.media_type, preferences)
    ]
    if episode_phase:
        providers = [
            provider
            for provider in providers
            if provider.declaration.episode_source == episode_phase
        ]
    request = MetadataProviderRequest(
        request_id=uuid4(),
        user_id=user_id,
        query=candidate.title,
        media_type=candidate.media_type,
        policy=policy,
        resource=resource,
        season_number=season_number,
        options={"steam_user_tags": (preferences or {}).get("steam_user_tags", True)},
    )
    return await handler.enrich(request, providers, candidate, include_media=include_media)


def library_candidate(
    title: str, media_type: MediaType, provider_ids: dict[str, str], year: int | None = None
) -> MetadataCandidate:
    """Library identifiers are supplied by the authorized caller, never by a plugin row ID."""
    return MetadataCandidate(
        title=title,
        media_type=media_type,
        provider="local",
        external_id=title,
        provider_ids=provider_ids,
        year=year,
    )


async def collect_owned_record(
    user_id: UUID,
    candidate: MetadataCandidate,
    *,
    resource: Literal["entity", "episodes", "airing", "relations", "recommendations"] = "entity",
    season_number: int | None = None,
    episode_phase: Literal["primary", "fallback"] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Background callbacks create their own session while preserving the library owner."""
    async with SessionLocal() as db:
        return await collect_record(
            db,
            user_id,
            candidate,
            resource=resource,
            season_number=season_number,
            episode_phase=episode_phase,
        )


def game_result(record: dict[str, Any]) -> dict[str, Any]:
    """Translate canonical partial fields for the existing game persistence boundary."""
    metadata = record.get("metadata", {})
    result = {
        **metadata,
        "title": metadata.get("title") or record.get("title"),
        "release_year": metadata.get("year") or record.get("year"),
        "provider": record.get("provider_name", "Metadata providers"),
        "provider_id": record.get("external_id"),
        "provider_ids": record.get("provider_ids", {}),
        "candidate_id": record.get("id"),
    }
    for kind in ("key_art", "banner", "logo", "icon"):
        urls = [asset["url"] for asset in record.get("assets", []) if asset["kind"] == kind]
        result[f"{kind}_urls"] = urls
        result[f"{kind}_url"] = urls[0] if urls else None
    return result


async def search_games(
    db: AsyncSession, user_id: UUID, query: str, limit: int = 20, *, preferences: dict | None = None
) -> dict[str, Any]:
    response = await search_records(
        db, user_id, query, MediaType.GAME, limit, preferences=preferences
    )
    return {**response, "results": [game_result(record) for record in response["results"]]}


async def resolve_library_record(
    db: AsyncSession,
    user_id: UUID,
    candidate: MetadataCandidate,
    *,
    include_media: bool = False,
    preferences: dict | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Resolve an unlinked library row conservatively before focused enrichment."""
    errors: list[str] = []
    if not candidate.provider_ids:
        response = await search_records(
            db, user_id, candidate.title, candidate.media_type, preferences=preferences
        )
        errors = response["provider_errors"]
        matches = [
            record
            for record in response["results"]
            if normalized_title(record["title"]) == normalized_title(candidate.title)
            and (candidate.year is None or record.get("year") in {None, candidate.year})
        ]
        if len(matches) != 1:
            return {}, errors
        record = matches[0]
        candidate = MetadataCandidate(
            title=record["title"],
            external_id=record["external_id"],
            provider=record["provider"],
            media_type=record["media_type"],
            year=record.get("year"),
            provider_ids=record["provider_ids"],
            alternate_titles=record.get("alternate_titles", ()),
            platforms=record.get("platforms", ()),
        )
    record, failures = await collect_record(
        db, user_id, candidate, include_media=include_media, preferences=preferences
    )
    return record, list(dict.fromkeys([*errors, *failures]))


async def resolve_owned_record(
    user_id: UUID,
    candidate: MetadataCandidate,
    *,
    include_media: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Import and scheduler jobs resolve identities with their owner's context."""
    async with SessionLocal() as db:
        return await resolve_library_record(db, user_id, candidate, include_media=include_media)


def media_result(record: dict[str, Any], media_type: MediaType) -> dict[str, Any]:
    """Keep native media API shapes while providers exchange one canonical contract."""
    metadata = record.get("metadata", {})
    ids = record.get("provider_ids", {})
    assets = record.get("assets", [])
    result = {
        "provider": record.get("provider_name", "Metadata providers"),
        "provider_id": record.get("external_id"),
        "provider_ids": ids,
        "title": metadata.get("title") or record.get("title"),
        "release_year": metadata.get("year") or record.get("year"),
        "description": metadata.get("description"),
        "studios": metadata.get("studios", []),
        "genres": metadata.get("genres", []),
        "countries": metadata.get("countries", []),
        "languages": metadata.get("languages", []),
        "poster_url": next((asset["url"] for asset in assets if asset["kind"] == "poster"), None),
        "backdrop_url": next(
            (asset["url"] for asset in assets if asset["kind"] in {"banner", "hero"}), None
        ),
        "url": next((link["url"] for link in metadata.get("links", [])), None),
    }
    if media_type == MediaType.MOVIE:
        result.update(
            {
                name: metadata.get(name)
                for name in ("release_date", "runtime_minutes", "director", "writer")
            }
        )
    else:
        result.update(
            {
                "first_air_date": metadata.get("release_date"),
                "episode_runtime_minutes": metadata.get("episode_runtime_minutes"),
            }
        )
    if media_type == MediaType.ANIME:
        result.update({"title_" + key: value for key, value in metadata.get("titles", {}).items()})
        result.update(
            {
                "episode_count": metadata.get("episode_count"),
                "format": metadata.get("format"),
                "anilist_score": (
                    metadata["scores"]["anilist"] / 10
                    if metadata.get("scores", {}).get("anilist") is not None
                    else None
                ),
                "mal_score": (
                    metadata["scores"]["mal"] / 10
                    if metadata.get("scores", {}).get("mal") is not None
                    else None
                ),
                "mal_id": ids.get("mal"),
                "anilist_id": ids.get("anilist"),
            }
        )
    else:
        result["tmdb_score"] = (
            metadata["scores"]["tmdb"] / 10
            if metadata.get("scores", {}).get("tmdb") is not None
            else None
        )
    if media_type == MediaType.TV_SHOW:
        result.update(
            {
                "creators": metadata.get("creators", []),
                "tvmaze_id": ids.get("tvmaze"),
                "seasons": [
                    {**season, "name": season.get("title"), "poster_url": None}
                    for season in metadata.get("seasons", [])
                ],
            }
        )
    return result


async def search_media(
    db: AsyncSession, user_id: UUID, query: str, media_type: MediaType, limit: int = 20
) -> dict[str, Any]:
    response = await search_records(db, user_id, query, media_type, limit)
    return {
        **response,
        "results": [media_result(record, media_type) for record in response["results"]],
    }


async def related_metadata(
    db: AsyncSession,
    user_id: UUID,
    candidate: MetadataCandidate,
    resource: Literal["relations", "recommendations"] = "relations",
) -> dict[str, Any]:
    """Native relation views consume registered resource capabilities and safe failures."""
    providers = [
        monitor.available(provider)
        for provider in await discover_providers(db, user_id, candidate.media_type)
        if resource in provider.declaration.metadata_resources
    ]
    participating = [
        provider
        for provider in providers
        if provider.state not in {ProviderHealth.DISABLED, ProviderHealth.NOT_CONFIGURED}
    ]
    if not participating:
        return {"configured": False, "relations": [], "relation_group": None}
    request = MetadataProviderRequest(
        request_id=uuid4(),
        user_id=user_id,
        query=candidate.title,
        media_type=candidate.media_type,
        resource=resource,
        policy="background",
    )
    record, errors = await handler.enrich(request, participating, candidate)
    metadata = record.get("metadata", {})
    if errors and not metadata.get("relations"):
        raise HTTPException(502, "; ".join(errors))
    return {
        "configured": True,
        "relations": metadata.get("relations", []),
        "relation_group": metadata.get("relation_group"),
    }


def relation_result(relation: dict[str, Any]) -> dict[str, Any]:
    """Keep existing native graph rendering fields at the application boundary."""
    candidate = relation["candidate"]
    identity_id = candidate["external_id"]
    return {
        "id": int(identity_id) if identity_id.isdecimal() else identity_id,
        "title": candidate["title"],
        "year": candidate.get("year"),
        "provider_ids": candidate.get("provider_ids", {}),
        "poster_url": relation.get("poster_url"),
        "format": relation.get("format"),
        "episode_count": relation.get("episode_count"),
        "is_current": relation.get("is_current", False),
        "anchor_id": (
            int(relation["parent_external_id"])
            if str(relation.get("parent_external_id", "")).isdecimal()
            else relation.get("parent_external_id")
        ),
        "anchor_kind": "branch" if relation.get("parent_group") == "branch" else "show",
        "relation_label": relation["relation"],
    }
