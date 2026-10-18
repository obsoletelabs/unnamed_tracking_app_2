"""Which copies a game page shows content from (#274).

A merged game is one library entry with several copies (a Steam copy, a Switch
copy, a Playnite record...). Every save, screenshot, clip and doc stays attached
to the copy it was uploaded to, so imports and the Playnite extension keep
syncing exactly as before. The main copy's page lists content from every copy
grouped under it and labels each item with its copy, so nothing "disappears"
when copies are merged."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game
from src.database.models.game_identity import GameIdentity
from src.features.game_identity import graph

# A copy listed as its own library entry ("Listed separately": an edge shown as a
# link, or an identity presented separately) keeps its content on its own page.
_OWN_PAGE = frozenset({"link"})


@dataclass(frozen=True)
class CopyLabel:
    game_id: UUID
    title: str
    source: str | None
    platform: str | None
    is_main: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "game_id": str(self.game_id),
            "title": self.title,
            "source": self.source,
            "platform": self.platform,
            "is_main": self.is_main,
        }


async def page_copies(db: AsyncSession, user_id: UUID, game_id: UUID) -> list[CopyLabel]:
    """The copies whose content belongs on this game's page, main copy first.

    The caller has already checked that `game_id` is a live game of `user_id`."""
    merged = await graph.merge_graph(db, user_id, readonly=True)
    members = {game_id}
    if merged.root(game_id) == game_id:
        others = merged.group(game_id) - {game_id}
        separate = set(
            await db.scalars(
                select(GameIdentity.game_id).where(
                    GameIdentity.user_id == user_id,
                    GameIdentity.game_id.in_(others),
                    GameIdentity.presentation == "separate",
                )
            )
        )
        members |= {
            member
            for member in others
            if member not in separate and merged.edge[member].display not in _OWN_PAGE
        }
    rows = await db.execute(
        select(Game.id, Game.title, Game.source, Game.platform).where(
            Game.user_id == user_id, Game.id.in_(members), Game.deleted_at.is_(None)
        )
    )
    labels = [
        CopyLabel(row.id, row.title, row.source, row.platform, row.id == game_id) for row in rows
    ]
    labels.sort(key=lambda c: (not c.is_main, (c.source or "").lower(), c.title, str(c.game_id)))
    return labels
