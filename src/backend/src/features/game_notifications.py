"""Owned-game release discovery and typed integration facts for sales/price hits."""

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.preferences import load_preferences
from src.database.models.game import Game, GameStatus
from src.features.notification_controller import NotificationDraft, NotificationEvent, emit


class GamePriceObservation(BaseModel):
    """Explicit live quote; historical purchase amounts are never used here."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9._-]+$")
    observation_id: str = Field(min_length=1, max_length=200)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    amount: Decimal = Field(ge=0, max_digits=14, decimal_places=4)
    regular_amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=4)
    previous_amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=4)
    threshold: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=4)
    threshold_id: str | None = Field(default=None, min_length=1, max_length=128)
    market: str = Field(min_length=1, max_length=64)
    platform: str = Field(min_length=1, max_length=64)

    @field_validator("amount", "regular_amount", "previous_amount", "threshold", mode="before")
    @classmethod
    def exact_amount(cls, value: object) -> object:
        if isinstance(value, (float, bool)):
            raise ValueError("Quote amounts require decimal strings, never floats")
        return value

    @model_validator(mode="after")
    def coherent_price(self) -> "GamePriceObservation":
        if self.regular_amount is not None and self.amount >= self.regular_amount:
            raise ValueError("A sale must be below its regular price")
        return self


async def interpret_game_event(
    db: AsyncSession, event: NotificationEvent
) -> NotificationDraft | None:
    """Enrich only the recipient's game; private threshold/follow facts stay internal."""
    if event.entity_type != "game":
        raise ValueError("Game events require a game entity")
    game = await db.scalar(
        select(Game).where(
            Game.id == event.entity_id, Game.user_id == event.user_id, Game.deleted_at.is_(None)
        )
    )
    if game is None:
        raise ValueError("Game is not owned by this recipient")
    kind: Literal["game_released", "game_sale", "game_price_hit"] = "game_released"
    source = "host"
    dedupe_key = event.dedupe_key
    body = "Released"
    public_body = body
    if event.type == "game.released":
        if event.data:
            raise ValueError("Release facts are resolved from owned game metadata")
        if game.release_date is None:
            return None
        release_at = int(
            datetime.combine(
                game.release_date, datetime.min.time(), tzinfo=timezone.utc
            ).timestamp()
        )
        if event.occurred_at != release_at or release_at > int(
            datetime.now(timezone.utc).timestamp()
        ):
            raise ValueError("Release event does not match the known release date")
        dedupe_key = f"game-release:{game.id}:{game.release_date}"
    else:
        quote = GamePriceObservation.model_validate(event.data)
        source = quote.source
        fingerprint = sha256(
            f"{source}:{quote.observation_id}:{quote.threshold_id or ''}:{quote.market}:"
            f"{quote.platform}:{quote.currency}".encode()
        ).hexdigest()
        dedupe_key = f"{event.type}:{game.id}:{fingerprint}"
        public_body = (
            f"Available for {quote.currency} {quote.amount} ({quote.market}, {quote.platform})"
        )
        if event.type == "game.sale.started":
            if quote.regular_amount is None:
                raise ValueError("Sale event requires a regular price")
            kind = "game_sale"
            body = f"On sale: {quote.currency} {quote.amount} (was {quote.regular_amount})"
        elif event.type == "game.price.threshold_hit":
            if (
                quote.threshold is None
                or not quote.threshold_id
                or quote.previous_amount is None
                or not quote.amount <= quote.threshold < quote.previous_amount
            ):
                raise ValueError(
                    "Price hit requires a downward crossing of an identified threshold"
                )
            kind = "game_price_hit"
            body = f"Price reached your {quote.currency} {quote.threshold} target: {quote.amount}"
        else:
            raise ValueError("Unsupported game event")
    return NotificationDraft(
        kind=kind,
        user_id=event.user_id,
        entity_type="game",
        entity_id=game.id,
        occurred_at=event.occurred_at,
        dedupe_key=dedupe_key,
        title=game.title,
        body=body,
        source=source,
        public_title=game.title,
        public_body=public_body,
        group_key=f"{event.type}:{game.id}",
        fingerprint=sha256(quote.model_dump_json().encode()).hexdigest()
        if event.type != "game.released"
        else None,
    )


async def generate_game_releases(db: AsyncSession, user_id: UUID, *, now: int) -> int:
    """Discover known releases in the same seven-day window as existing media."""
    prefs = await load_preferences(db, user_id)
    if not prefs["notify_game_released"]:
        return 0
    first_day = datetime.fromtimestamp(now - 7 * 86400, timezone.utc).date()
    today = datetime.fromtimestamp(now, timezone.utc).date()
    games = await db.scalars(
        select(Game).where(
            Game.user_id == user_id,
            Game.deleted_at.is_(None),
            Game.status.in_((GameStatus.WISHLIST, GameStatus.BACKLOG, GameStatus.ON_HOLD)),
            Game.release_date.between(first_day, today),
        )
    )
    created = 0
    for game in games:
        if game.release_date is None:
            continue
        when = int(
            datetime.combine(
                game.release_date, datetime.min.time(), tzinfo=timezone.utc
            ).timestamp()
        )
        created += int(
            await emit(
                db,
                NotificationEvent(
                    type="game.released",
                    user_id=user_id,
                    entity_type="game",
                    entity_id=game.id,
                    occurred_at=when,
                    dedupe_key=f"game-release:{game.id}:{game.release_date}",
                    data={},
                ),
            )
            is not None
        )
    return created
