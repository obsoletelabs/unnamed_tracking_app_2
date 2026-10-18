"""Minimized own-user projections and durable, explicitly self-reported achievements."""

import time
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.delegation import AchievementReportWrite
from src.database.models.achievement import Achievement
from src.database.models.achievement_override import AchievementOverride
from src.database.models.game import Game, GameStatus
from src.features.game_identity import graph
from src.plugin_api.game_library import game_details, owned_game


async def delegated_game(db: AsyncSession, user_id: UUID, game_id: UUID) -> Game:
    try:
        return await owned_game(db, user_id, game_id)
    except LookupError as exc:
        raise HTTPException(404, "Game not found.") from exc


def library_game(game: Game) -> dict:
    """Current library membership is ownership; provider purchase offers are separate."""
    item = game_details(game)
    item.pop("achievement_total")
    item.pop("achievement_unlocked")
    item["provider_ids"] = game.provider_ids
    item["external_id"] = game.external_id
    item["play_compatibility"] = game.play_compatibility
    item["owned"] = game.status != GameStatus.WISHLIST
    item["ownership_provenance"] = "instance_library"
    item["retrieved_at"] = int(time.time())
    item["provider_checked_at"] = None
    item["offers"] = []
    item["offer_status"] = "unavailable"
    item["assets"] = {
        kind: f"/api/delegation/v1/games/{game.id}/artwork/{kind}"
        for kind in ("key_art", "banner", "logo", "icon")
    }
    return jsonable_encoder(item)


async def _identities(db: AsyncSession, user_id: UUID, games: list[Game]) -> dict[UUID, dict]:
    """Which logical game each record belongs to (#274).

    Copies of one game (Steam and Epic Hades, Minecraft Java and for Windows) share a
    `group_id` and the main copy's title for presentation. Grouping is not proof of
    access or play compatibility: consumers still check each copy's claims.
    A record that is not grouped is its own group."""
    merged = await graph.merge_graph(db, user_id, readonly=True)
    roots = {game.id: merged.root(game.id) for game in games}
    titles = {game.id: game.title for game in games}
    missing = set(roots.values()) - titles.keys()
    if missing:
        titles.update(
            (row.id, row.title)
            for row in await db.execute(
                select(Game.id, Game.title).where(Game.user_id == user_id, Game.id.in_(missing))
            )
        )
    return {
        game.id: {
            "group_id": str(roots[game.id]),
            "is_main": roots[game.id] == game.id,
            "main_title": titles.get(roots[game.id], game.title),
        }
        for game in games
    }


async def library_page(db: AsyncSession, user_id: UUID, offset: int, limit: int) -> dict:
    rows = list(
        await db.scalars(
            select(Game)
            .where(Game.user_id == user_id, Game.deleted_at.is_(None))
            .order_by(Game.sort_title, Game.id)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    identities = await _identities(db, user_id, rows[:limit])
    return {
        "games": [{**library_game(row), "identity": identities[row.id]} for row in rows[:limit]],
        "next_offset": offset + min(limit, len(rows)),
        "complete": len(rows) <= limit,
        "retrieved_at": int(time.time()),
    }


def achievement_report(row: AchievementOverride) -> dict:
    return {
        "id": str(row.id),
        "provider": row.provider,
        "external_id": row.external_id,
        "name": row.name,
        "description": row.description,
        "unlocked": row.unlocked,
        "unlocked_at": row.unlocked_at,
        "updated_at": row.updated_at,
        "provenance": "manual" if row.provider == "manual" else "self_reported_override",
        "verified": False,
    }


async def achievement_page(
    db: AsyncSession, user_id: UUID, game_id: UUID, offset: int, limit: int
) -> dict:
    await delegated_game(db, user_id, game_id)
    provider_rows = list(
        await db.scalars(
            select(Achievement)
            .where(Achievement.game_id == game_id)
            .order_by(Achievement.provider, Achievement.external_id, Achievement.id)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    reports = list(
        await db.scalars(
            select(AchievementOverride)
            .where(AchievementOverride.game_id == game_id)
            .order_by(AchievementOverride.provider, AchievementOverride.external_id)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    report_keys = {(row.provider, row.external_id) for row in provider_rows[:limit]}
    matching_reports = (
        list(
            await db.scalars(
                select(AchievementOverride).where(
                    AchievementOverride.game_id == game_id,
                    AchievementOverride.provider.in_({key[0] for key in report_keys}),
                    AchievementOverride.external_id.in_({key[1] for key in report_keys}),
                )
            )
        )
        if report_keys
        else []
    )
    indexed = {(row.provider, row.external_id): row for row in matching_reports}
    return {
        "achievements": [
            {
                "id": str(row.id),
                "provider": row.provider,
                "external_id": row.external_id,
                "name": row.name,
                "description": row.description,
                "unlocked": row.unlocked,
                "unlocked_at": row.unlocked_at,
                "provenance": "provider_synced",
                "provider_checked_at": None,
                "local_report": achievement_report(indexed[(row.provider, row.external_id)])
                if (row.provider, row.external_id) in indexed
                else None,
            }
            for row in provider_rows[:limit]
        ],
        "local_reports": [achievement_report(row) for row in reports[:limit]],
        "next_offset": offset + min(limit, max(len(provider_rows), len(reports))),
        "complete": len(provider_rows) <= limit and len(reports) <= limit,
    }


async def write_achievement_report(
    db: AsyncSession, user_id: UUID, game_id: UUID, payload: AchievementReportWrite
) -> dict:
    game = await delegated_game(db, user_id, game_id)
    # Serialize reports against one owned parent, including first creation.
    await db.scalar(select(Game.id).where(Game.id == game.id).with_for_update())
    external_id = payload.external_id or str(uuid4())
    imported = None
    if payload.provider != "manual":
        imported = await db.scalar(
            select(Achievement).where(
                Achievement.game_id == game_id,
                Achievement.provider == payload.provider,
                Achievement.external_id == external_id,
            )
        )
        if imported is None:
            raise HTTPException(404, "Provider achievement not found in this game.")
        if payload.name is not None or payload.description is not None:
            raise HTTPException(422, "A local report cannot rewrite provider achievement metadata.")
    elif payload.name is None:
        raise HTTPException(422, "Manual achievements require a name.")
    row = await db.scalar(
        select(AchievementOverride)
        .where(
            AchievementOverride.game_id == game_id,
            AchievementOverride.provider == payload.provider,
            AchievementOverride.external_id == external_id,
        )
        .with_for_update()
    )
    if row is None:
        row = AchievementOverride(
            game_id=game_id,
            provider=payload.provider,
            external_id=external_id,
            name=imported.name[:500] if imported else str(payload.name),
        )
        db.add(row)
    row.name = imported.name[:500] if imported else str(payload.name)
    row.description = (imported.description or "")[:10000] if imported else payload.description
    row.unlocked = payload.unlocked
    row.unlocked_at = (payload.unlocked_at or int(time.time())) if payload.unlocked else None
    row.updated_at = int(time.time())
    await db.flush()
    return achievement_report(row)


async def delete_achievement_report(
    db: AsyncSession, user_id: UUID, game_id: UUID, report_id: UUID
) -> None:
    await delegated_game(db, user_id, game_id)
    row = await db.scalar(
        select(AchievementOverride).where(
            AchievementOverride.id == report_id,
            AchievementOverride.game_id == game_id,
        )
    )
    if row is None:
        raise HTTPException(404, "Local achievement report not found.")
    await db.delete(row)
