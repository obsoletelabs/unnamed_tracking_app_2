"""Manual achievements and completion alongside imported data (#97).

Manual facts are the user's own. Provider achievements stay in `achievements`
(replaced by every sync) and manual ones in `game_manual_achievements`, which no
sync touches. A user-set completion is a `completion_status` field value with
origin `user` plus a `status` entry in the game's `locked_fields`; sync paths that
would move the status skip a locked one.

Aggregation rules (see wiki/docs/development/game-identity-manual.md):

- a provider achievement is identified by provider and external ID, never by title,
  so two provider achievements with the same name stay two, while the same Steam
  achievement reported on two merged rows (a duplicated source observation) counts once;
- a manual achievement is its own entry unless the user gave several the same
  `match_key`, which is the explicit statement that they are one achievement;
- manual and provider achievements never merge with each other.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.achievement import Achievement
from src.database.models.game import Game, GameStatus
from src.database.models.game_identity import GameIdentity
from src.database.models.game_identity_tracking import (
    FieldValueOrigin,
    FieldValueState,
    GameFieldValue,
    GameManualAchievement,
    LockOrigin,
    ManualFactOrigin,
)
from src.features.game_identity import graph, policy
from src.helpers.public_http import parse_http_url

COMPLETION_FIELD = "completion_status"
STATUS_LOCK = "status"
_EDITABLE = (
    "title",
    "description",
    "icon_url",
    "category",
    "position",
    "match_key",
    "achieved",
    "achieved_at",
)


def _invalid(message: str) -> graph.IdentityInvalid:
    return graph.IdentityInvalid(message)


def _clean(value: str | None, *, limit: int) -> str | None:
    text = (value or "").strip()
    return text[:limit] if text else None


def validate_icon(url: str | None) -> str | None:
    """An icon is a public http(s) address; it is fetched, checked and resized on first use."""
    text = _clean(url, limit=2000)
    if text is None:
        return None
    try:
        parse_http_url(text, allowed_ports={80, 443})
    except ValueError as exc:
        raise _invalid("The icon must be an http(s) image address without credentials.") from exc
    return text


async def _live_game(db: AsyncSession, user_id: UUID, game_id: UUID, *, lock: bool = False) -> Game:
    statement = select(Game).where(Game.id == game_id, Game.user_id == user_id)
    game = await db.scalar(statement.with_for_update() if lock else statement)
    if game is None or game.deleted_at is not None:
        raise LookupError("Game not found")
    return game


async def _umbrella_of(db: AsyncSession, user_id: UUID, game_id: UUID) -> UUID:
    umbrella = await db.scalar(
        select(GameIdentity.umbrella_id).where(
            GameIdentity.game_id == game_id, GameIdentity.user_id == user_id
        )
    )
    if umbrella is None:
        raise graph.IdentityConflict("This game is not placed in an umbrella yet.")
    return umbrella


async def _owned_achievement(
    db: AsyncSession, user_id: UUID, achievement_id: UUID
) -> GameManualAchievement:
    row = await db.scalar(
        select(GameManualAchievement)
        .where(
            GameManualAchievement.id == achievement_id,
            GameManualAchievement.user_id == user_id,
            GameManualAchievement.archived_at.is_(None),
        )
        .with_for_update()
    )
    if row is None:
        raise LookupError("Achievement not found")
    return row


def _normalise(values: dict[str, Any]) -> dict[str, Any]:
    """Validated, trimmed column values for the keys present in `values`."""
    out: dict[str, Any] = {}
    if "title" in values:
        title = _clean(values["title"], limit=500)
        if title is None:
            raise _invalid("An achievement needs a title.")
        out["title"] = title
    for key, limit in (("description", 20000), ("category", 100), ("match_key", 200)):
        if key in values:
            out[key] = _clean(values[key], limit=limit)
    if "icon_url" in values:
        out["icon_url"] = validate_icon(values["icon_url"])
    if "position" in values:
        out["position"] = max(0, int(values["position"] or 0))
    if "achieved" in values:
        out["achieved"] = bool(values["achieved"])
    if "achieved_at" in values:
        stamp = values["achieved_at"]
        if stamp is not None and (stamp < 0 or stamp > 253_402_300_799):
            raise _invalid("The earned time is out of range.")
        out["achieved_at"] = stamp
    return out


def _settle_earned(row: GameManualAchievement, stamp: int) -> None:
    """Earned needs no date; a date needs earned. Earning without one records now."""
    if not row.achieved:
        row.achieved_at = None
    elif row.achieved_at is None:
        row.achieved_at = stamp


async def create_achievement(
    db: AsyncSession,
    user_id: UUID,
    game_id: UUID,
    values: dict[str, Any],
    *,
    umbrella: bool = False,
) -> GameManualAchievement:
    """Add a manual achievement to one version, or to the whole umbrella of that version."""
    await graph.lock_user_identities(db, user_id)
    await _live_game(db, user_id, game_id)
    data = _normalise(values)
    if "title" not in data:
        raise _invalid("An achievement needs a title.")
    stamp = graph.now_seconds()
    row = GameManualAchievement(
        user_id=user_id,
        game_id=None if umbrella else game_id,
        umbrella_id=await _umbrella_of(db, user_id, game_id) if umbrella else None,
        origin=ManualFactOrigin.USER.value,
        created_at=stamp,
        updated_at=stamp,
        **data,
    )
    _settle_earned(row, stamp)
    db.add(row)
    await db.flush()
    return row


async def update_achievement(
    db: AsyncSession, user_id: UUID, achievement_id: UUID, changes: dict[str, Any]
) -> GameManualAchievement:
    """Edit only the fields sent; leaving `achieved` untouched keeps its earned time."""
    row = await _owned_achievement(db, user_id, achievement_id)
    data = _normalise({k: v for k, v in changes.items() if k in _EDITABLE})
    for key, value in data.items():
        setattr(row, key, value)
    stamp = graph.now_seconds()
    _settle_earned(row, stamp)
    row.updated_at = stamp
    await db.flush()
    return row


async def set_earned(
    db: AsyncSession,
    user_id: UUID,
    achievement_id: UUID,
    achieved: bool,
    achieved_at: int | None = None,
) -> GameManualAchievement:
    """Mark earned (now unless a time is given) or unmark it, which forgets the time."""
    return await update_achievement(
        db,
        user_id,
        achievement_id,
        {"achieved": achieved, "achieved_at": achieved_at if achieved else None},
    )


async def remove_achievement(db: AsyncSession, user_id: UUID, achievement_id: UUID) -> None:
    """Removing archives: the row stays for history and never reappears in views."""
    row = await _owned_achievement(db, user_id, achievement_id)
    row.archived_at = row.updated_at = graph.now_seconds()
    await db.flush()


def achievement_out(row: GameManualAchievement) -> dict[str, Any]:
    return {
        "id": row.id,
        "scope": "umbrella" if row.umbrella_id else "version",
        "game_id": row.game_id,
        "umbrella_id": row.umbrella_id,
        "title": row.title,
        "description": row.description,
        "icon_url": f"/api/game-identity/manual-achievements/{row.id}/icon"
        if row.icon_url
        else None,
        "category": row.category,
        "position": row.position,
        "achieved": row.achieved,
        "achieved_at": row.achieved_at,
        "match_key": row.match_key,
        "origin": row.origin,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@dataclass
class _Entry:
    key: tuple[str, ...]
    source: str
    title: str
    unlocked: bool = False
    versions: dict[UUID, bool] = field(default_factory=dict)
    manual_ids: list[UUID] = field(default_factory=list)


def _entry_key_manual(row: GameManualAchievement) -> tuple[str, ...]:
    if row.match_key:
        return ("match", " ".join(row.match_key.casefold().split()))
    return ("manual", str(row.id))


def _group_entries(
    provider: Sequence[Achievement], manual: Sequence[GameManualAchievement]
) -> dict[tuple[str, ...], _Entry]:
    entries: dict[tuple[str, ...], _Entry] = {}
    for row in provider:
        key: tuple[str, ...] = ("provider", row.provider.casefold(), row.external_id)
        entry = entries.setdefault(key, _Entry(key, "provider", row.name))
        entry.unlocked = entry.unlocked or bool(row.unlocked)
        entry.versions[row.game_id] = entry.versions.get(row.game_id, False) or bool(row.unlocked)
    for item in manual:
        key = _entry_key_manual(item)
        entry = entries.setdefault(key, _Entry(key, "manual", item.title))
        entry.unlocked = entry.unlocked or item.achieved
        entry.manual_ids.append(item.id)
        if item.game_id is not None:
            entry.versions[item.game_id] = entry.versions.get(item.game_id, False) or item.achieved
    return entries


def _version_rows(
    games: Sequence[Game], provider: Sequence[Achievement], manual: Sequence[GameManualAchievement]
) -> list[dict[str, Any]]:
    versions = []
    for game in games:
        p_rows = [r for r in provider if r.game_id == game.id]
        m_rows = [r for r in manual if r.game_id == game.id]
        p_done = sum(1 for r in p_rows if r.unlocked)
        m_done = sum(1 for r in m_rows if r.achieved)
        versions.append(
            {
                "game_id": game.id,
                "title": game.title,
                "platform": game.platform,
                "provider": {"total": len(p_rows), "unlocked": p_done},
                "manual": {"total": len(m_rows), "unlocked": m_done},
                "total": len(p_rows) + len(m_rows),
                "unlocked": p_done + m_done,
            }
        )
    return versions


async def achievements_view(db: AsyncSession, user_id: UUID, game_id: UUID) -> dict[str, Any]:
    """Umbrella totals and a per-version breakdown with no double counting."""
    await _live_game(db, user_id, game_id)
    group = (await graph.merge_graph(db, user_id)).group(game_id)
    games = list(
        await db.scalars(
            select(Game)
            .where(Game.id.in_(group), Game.user_id == user_id, Game.deleted_at.is_(None))
            .order_by(Game.sort_title, Game.id)
        )
    )
    ids = {g.id for g in games}
    umbrellas = set(
        await db.scalars(
            select(GameIdentity.umbrella_id).where(
                GameIdentity.user_id == user_id, GameIdentity.game_id.in_(ids)
            )
        )
    )
    provider = (
        await db.scalars(
            select(Achievement).where(Achievement.game_id.in_(ids)).order_by(Achievement.name)
        )
    ).all()
    scope: ColumnElement[bool] = GameManualAchievement.game_id.in_(ids)
    if umbrellas:
        scope = or_(scope, GameManualAchievement.umbrella_id.in_(umbrellas))
    manual = (
        await db.scalars(
            select(GameManualAchievement)
            .where(
                GameManualAchievement.user_id == user_id,
                GameManualAchievement.archived_at.is_(None),
                scope,
            )
            .order_by(
                GameManualAchievement.position,
                GameManualAchievement.created_at,
                GameManualAchievement.id,
            )
        )
    ).all()

    entries = _group_entries(provider, manual)
    versions = _version_rows(games, provider, manual)
    total = len(entries)
    unlocked = sum(1 for e in entries.values() if e.unlocked)
    return {
        "game_id": game_id,
        "umbrella": {
            "total": total,
            "unlocked": unlocked,
            "percent": round(100 * unlocked / total) if total else 0,
            "manual": sum(1 for e in entries.values() if e.source == "manual"),
            "provider": sum(1 for e in entries.values() if e.source == "provider"),
            "summary": f"{unlocked} of {total} earned across {len(games)} tracked "
            f"{'version' if len(games) == 1 else 'versions'}"
            if total
            else None,
        },
        "versions": versions,
        "manual": [achievement_out(r) for r in manual],
    }


# ---- completion --------------------------------------------------------------


async def _current_value(db: AsyncSession, user_id: UUID, game_id: UUID) -> GameFieldValue | None:
    return await db.scalar(
        select(GameFieldValue).where(
            GameFieldValue.user_id == user_id,
            GameFieldValue.game_id == game_id,
            GameFieldValue.field == COMPLETION_FIELD,
            GameFieldValue.state == FieldValueState.CURRENT.value,
        )
    )


def is_status_locked(game: Game) -> bool:
    """True when the user set this game's status by hand; sync must leave it alone."""
    return STATUS_LOCK in (game.locked_fields or [])


async def set_completion(
    db: AsyncSession,
    user_id: UUID,
    game_id: UUID,
    status: GameStatus,
    *,
    game: Game | None = None,
) -> Game:
    """Record the status the user chose for one version and protect it from sync.

    Repeating the same choice changes nothing. The earlier choice stays as a
    superseded field value, which is the history of the user's decisions."""
    game = game or await _live_game(db, user_id, game_id, lock=True)
    stamp = graph.now_seconds()
    current = await _current_value(db, user_id, game_id)
    if current is None or current.value != status.value:
        if current is not None:
            current.state = FieldValueState.SUPERSEDED.value
            current.resolved_at = stamp
            await db.flush()
        db.add(
            GameFieldValue(
                user_id=user_id,
                game_id=game_id,
                field=COMPLETION_FIELD,
                value=status.value,
                state=FieldValueState.CURRENT.value,
                origin=FieldValueOrigin.USER.value,
                edited_at=stamp,
                recorded_at=stamp,
                locked=True,
                lock_origin=LockOrigin.USER.value,
            )
        )
    game.status = status
    if status == GameStatus.MASTERED and game.completion_date is None:
        game.completion_date = stamp
    game.locked_fields = sorted({*(game.locked_fields or []), STATUS_LOCK})
    await db.flush()
    return game


def guard_status_update(
    game: Game, updates: dict[str, Any], *, automation: bool
) -> GameStatus | None:
    """Apply the manual-status rules to an update before it is written.

    A source's (API key) report cannot replace a status the user set by hand, so the
    status is dropped from it. A person's own change of status is returned: it becomes
    a manual completion once the update has been applied."""
    if automation:
        if is_status_locked(game):
            updates.pop("status", None)
            updates.pop("completion_date", None)
        return None
    chosen = updates.get("status")
    return chosen if chosen is not None and chosen != game.status else None


async def record_user_status(db: AsyncSession, game: Game, status: GameStatus | None) -> None:
    if status is not None:
        await set_completion(db, game.user_id, game.id, status, game=game)


async def release_completion(db: AsyncSession, user_id: UUID, game_id: UUID) -> Game:
    """Hand the status back to sync: the value stays, but it may change again."""
    game = await _live_game(db, user_id, game_id, lock=True)
    current = await _current_value(db, user_id, game_id)
    if current is not None:
        current.state = FieldValueState.SUPERSEDED.value
        current.resolved_at = graph.now_seconds()
    game.locked_fields = sorted(set(game.locked_fields or []) - {STATUS_LOCK})
    await db.flush()
    return game


async def completion_history(
    db: AsyncSession, user_id: UUID, game_id: UUID
) -> list[dict[str, Any]]:
    await _live_game(db, user_id, game_id)
    rows = await db.scalars(
        select(GameFieldValue)
        .where(
            GameFieldValue.user_id == user_id,
            GameFieldValue.game_id == game_id,
            GameFieldValue.field == COMPLETION_FIELD,
        )
        .order_by(
            (GameFieldValue.state == FieldValueState.CURRENT.value).desc(),
            GameFieldValue.recorded_at.desc(),
            GameFieldValue.id,
        )
    )
    return [
        {
            "status": r.value,
            "state": r.state,
            "origin": r.origin,
            "set_at": r.edited_at,
            "ended_at": r.resolved_at,
        }
        for r in rows
    ]


async def completion_view(db: AsyncSession, user_id: UUID, game_id: UUID) -> dict[str, Any]:
    """Per-version status and an honest umbrella summary of how many are complete."""
    await _live_game(db, user_id, game_id)
    group = (await graph.merge_graph(db, user_id)).group(game_id)
    games = list(
        await db.scalars(
            select(Game)
            .where(Game.id.in_(group), Game.user_id == user_id, Game.deleted_at.is_(None))
            .order_by(Game.sort_title, Game.id)
        )
    )
    versions = [
        {
            "game_id": g.id,
            "title": g.title,
            "platform": g.platform,
            "status": g.status.value,
            "completed": policy.is_completed(g.status.value),
            "user_set": is_status_locked(g),
            "completion_date": g.completion_date,
        }
        for g in games
    ]
    total = len(versions)
    done = sum(1 for v in versions if v["completed"])
    plural = "version" if total == 1 else "versions"
    if done == 0:
        summary = f"Not completed on any of {total} tracked {plural}"
    elif done == total:
        summary = "Completed" if total == 1 else f"Completed on all {total} tracked versions"
    else:
        summary = f"Completed on {done} of {total} tracked {plural}"
    return {
        "game_id": game_id,
        "completed": done,
        "of": total,
        "partial": 0 < done < total,
        "all_completed": total > 0 and done == total,
        "summary": summary,
        "versions": versions,
    }


async def tracking_view(db: AsyncSession, user_id: UUID, game_id: UUID) -> dict[str, Any]:
    return {
        "completion": await completion_view(db, user_id, game_id),
        "achievements": await achievements_view(db, user_id, game_id),
    }


async def export_rows(db: AsyncSession, user_id: UUID) -> dict[str, list[dict[str, Any]]]:
    """Everything manual a user has, for the library export: nothing is dropped."""
    achievements = await db.scalars(
        select(GameManualAchievement)
        .where(
            GameManualAchievement.user_id == user_id, GameManualAchievement.archived_at.is_(None)
        )
        .order_by(GameManualAchievement.created_at, GameManualAchievement.id)
    )
    completions = await db.scalars(
        select(GameFieldValue)
        .where(
            GameFieldValue.user_id == user_id,
            GameFieldValue.field == COMPLETION_FIELD,
            GameFieldValue.origin == FieldValueOrigin.USER.value,
        )
        .order_by(GameFieldValue.recorded_at, GameFieldValue.id)
    )
    return {
        "manual_achievements": [
            {
                **achievement_out(a),
                "icon_url": a.icon_url,
            }
            for a in achievements
        ],
        "manual_completion": [
            {
                "game_id": c.game_id,
                "status": c.value,
                "state": c.state,
                "set_at": c.edited_at,
                "ended_at": c.resolved_at,
            }
            for c in completions
        ],
    }
