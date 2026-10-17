"""Progress, counts and anomaly reporting of one user's backfill.

Counts and anomaly codes are written in the same transaction as the work they
describe, so a rolled-back chunk leaves neither rows nor counts behind and a
resumed run never double counts. Only counts, stage names, error codes and game
IDs are stored: never titles, notes or other library content."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game_identity_backfill import GameIdentityBackfill
from src.database.session import SessionLocal

CURSOR = "_cursor"
MAX_SAMPLE_IDS = 20
MAX_ANOMALIES = 100


class Progress:
    """Collects the effect of the current chunk and persists it with the chunk."""

    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        self._counts: Counter[str] = Counter()
        self._anomalies: dict[tuple[str, str], list[Any]] = {}
        self._cursor: str | None | bool = False  # False: unchanged

    def add(self, name: str, amount: int = 1) -> None:
        if amount:
            self._counts[name] += amount

    def anomaly(self, stage: str, code: str, game_id: UUID | None = None) -> None:
        entry = self._anomalies.setdefault((stage, code), [0, []])
        entry[0] += 1
        if game_id is not None and len(entry[1]) < MAX_SAMPLE_IDS:
            entry[1].append(str(game_id))

    def set_cursor(self, value: str | None) -> None:
        self._cursor = value

    def discard(self) -> None:
        self._counts.clear()
        self._anomalies.clear()
        self._cursor = False

    async def flush(self, db: AsyncSession) -> None:
        row = await db.scalar(
            select(GameIdentityBackfill)
            .where(GameIdentityBackfill.user_id == self.user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise LookupError("The backfill run record is missing")
        counts = dict(row.counts or {})
        for name, amount in self._counts.items():
            counts[name] = int(counts.get(name, 0)) + amount
        if self._cursor is not False:
            if self._cursor is None:
                counts.pop(CURSOR, None)
            else:
                counts[CURSOR] = self._cursor
        row.counts = counts
        errors = {(e["stage"], e["code"]): e for e in (row.errors or [])}
        for (stage, code), (count, ids) in self._anomalies.items():
            known = errors.setdefault(
                (stage, code), {"stage": stage, "code": code, "count": 0, "game_ids": []}
            )
            known["count"] += count
            known["game_ids"] = [*known["game_ids"], *ids][:MAX_SAMPLE_IDS]
        row.errors = list(errors.values())[:MAX_ANOMALIES]
        row.updated_at = int(time.time())
        self.discard()

    async def transaction(self, work: Callable[[AsyncSession], Awaitable[None]]) -> None:
        """Run one bounded chunk atomically with its counts; a failure rolls both back."""
        async with SessionLocal() as db:
            try:
                await work(db)
                await self.flush(db)
                await db.commit()
            except BaseException:
                await db.rollback()
                self.discard()
                raise

    @staticmethod
    async def cursor(user_id: UUID) -> str | None:
        async with SessionLocal() as db:
            row = await db.get(GameIdentityBackfill, user_id)
            value = (row.counts or {}).get(CURSOR) if row else None
            return str(value) if value is not None else None
