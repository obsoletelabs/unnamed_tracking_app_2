"""Run, resume and report the backfill for one user or for everyone.

A run is a sequence of stages. Each stage commits bounded chunks together with its
counts, so an interrupted run (crash, restart, failed chunk) resumes at the stage it
stopped in and repeats nothing that was committed. A finished run started again
simply finds nothing left to add. One user's run holds a session advisory lock, so a
second runner for the same user is refused instead of racing."""

from __future__ import annotations

# SQLAlchemy's `func` is a dynamic namespace pylint cannot see through.
# pylint: disable=not-callable
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.achievement import Achievement
from src.database.models.achievement_override import AchievementOverride
from src.database.models.game import Game, GameLink
from src.database.models.game_duplicate_dismissal import GameDuplicateDismissal
from src.database.models.game_field_change import GameFieldChange
from src.database.models.game_identity import (
    GameIdentity,
    GameIdentityDecision,
    GameSourceObservation,
    GameSourceObservationLink,
)
from src.database.models.game_identity_backfill import BackfillStatus, GameIdentityBackfill
from src.database.models.game_note_detail import GameNoteDetail
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.game_identity_backfill import stages
from src.features.game_identity_backfill.progress import Progress

VERSION = 1
DEFAULT_BATCH = 500
STALE_AFTER = 24 * 3600
Stage = Callable[[Progress, int], Awaitable[None]]
STAGES: tuple[tuple[str, Stage], ...] = (
    ("observations", stages.observations),
    ("merges", stages.merges),
    ("placement", stages.placement),
    ("relationships", stages.relationships),
    ("decisions", stages.dismissals),
    ("review", stages.review_queue),
    ("fields", stages.fields),
    ("counters", stages.counter_groups),
    ("matching", stages.matching),
)
# Tables the backfill must never shrink; counted before and after.
PROTECTED: dict[str, tuple[Any, Any]] = {
    "games": (Game, Game.user_id),
    "achievements": (Achievement, None),
    "achievement_overrides": (AchievementOverride, None),
    "game_links": (GameLink, None),
    "game_notes": (GameNoteDetail, None),
    "field_changes": (GameFieldChange, None),
    "duplicate_dismissals": (GameDuplicateDismissal, GameDuplicateDismissal.user_id),
}


class BackfillBusy(RuntimeError):
    """Another runner holds this user's backfill."""


def _lock_key(user_id: UUID) -> str:
    return f"gid-backfill:{user_id}"


async def snapshot(db: AsyncSession, user_id: UUID) -> dict[str, int]:
    """Counts the backfill must preserve (never decrease) plus identity coverage."""
    found: dict[str, int] = {}
    owned = select(Game.id).where(Game.user_id == user_id)
    for name, (model, owner) in PROTECTED.items():
        statement = select(func.count()).select_from(model)
        if owner is not None:
            statement = statement.where(owner == user_id)
        else:
            statement = statement.where(model.game_id.in_(owned))
        found[name] = int(await db.scalar(statement) or 0)
    games = select(Game).where(Game.user_id == user_id).subquery()
    found["games_with_parent"] = int(
        await db.scalar(select(func.count()).where(games.c.parent_game_id.is_not(None))) or 0
    )
    found["games_with_playnite_guid"] = int(
        await db.scalar(select(func.count()).where(games.c.playnite_guid.is_not(None))) or 0
    )
    found["games_with_collections"] = int(
        await db.scalar(select(func.count()).where(func.cardinality(games.c.collections) > 0)) or 0
    )
    found["games_with_notes"] = int(
        await db.scalar(select(func.count()).where(games.c.notes.is_not(None))) or 0
    )
    found["playtime_seconds"] = int(
        await db.scalar(select(func.coalesce(func.sum(games.c.playtime_seconds), 0))) or 0
    )
    found["identities"] = int(
        await db.scalar(
            select(func.count()).select_from(GameIdentity).where(GameIdentity.user_id == user_id)
        )
        or 0
    )
    found["observations"] = int(
        await db.scalar(
            select(func.count())
            .select_from(GameSourceObservation)
            .where(GameSourceObservation.user_id == user_id)
        )
        or 0
    )
    found["decisions"] = int(
        await db.scalar(
            select(func.count())
            .select_from(GameIdentityDecision)
            .where(GameIdentityDecision.user_id == user_id)
        )
        or 0
    )
    found["games_without_identity"] = int(
        await db.scalar(
            select(func.count())
            .select_from(Game)
            .where(
                Game.user_id == user_id,
                ~select(GameIdentity.game_id).where(GameIdentity.game_id == Game.id).exists(),
            )
        )
        or 0
    )
    found["games_without_source_link"] = int(
        await db.scalar(
            select(func.count())
            .select_from(Game)
            .where(
                Game.user_id == user_id,
                ~select(GameSourceObservationLink.id)
                .where(
                    GameSourceObservationLink.game_id == Game.id,
                    GameSourceObservationLink.unlinked_at.is_(None),
                )
                .exists(),
            )
        )
        or 0
    )
    return found


# Counts that must never go down while the backfill runs.
_NEVER_DECREASE = (
    *PROTECTED,
    "games_with_parent",
    "games_with_playnite_guid",
    "games_with_collections",
    "games_with_notes",
    "observations",
    "decisions",
)


def judge(before: dict[str, int], after: dict[str, int]) -> tuple[str, list[dict[str, Any]]]:
    """Status and findings from the before/after counts (the relationship invariants)."""
    findings: list[dict[str, Any]] = []
    for name in _NEVER_DECREASE:
        if after.get(name, 0) < before.get(name, 0):
            findings.append({"stage": "verify", "code": f"decreased:{name}", "count": 1})
    if findings:
        return BackfillStatus.FAILED.value, findings
    for name in ("games_without_identity", "games_without_source_link"):
        if after.get(name, 0):
            findings.append({"stage": "verify", "code": name, "count": after[name]})
    return (BackfillStatus.INCOMPLETE if findings else BackfillStatus.COMPLETED).value, findings


async def _record(user_id: UUID, **values: Any) -> None:
    async with SessionLocal() as db:
        row = await db.get(GameIdentityBackfill, user_id)
        if row is None:
            raise LookupError("The backfill run record is missing")
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_at = int(time.time())
        await db.commit()


async def _start(user_id: UUID, *, reset: bool) -> GameIdentityBackfill:
    async with SessionLocal() as db:
        row = await db.get(GameIdentityBackfill, user_id, with_for_update=True)
        now = int(time.time())
        if row is None:
            row = GameIdentityBackfill(user_id=user_id, created_at=now)
            db.add(row)
        fresh = (
            reset
            or row.status in (BackfillStatus.COMPLETED.value, BackfillStatus.INCOMPLETE.value)
            or row.version != VERSION
            or row.status == BackfillStatus.PENDING.value
        )
        if fresh:
            row.version = VERSION
            row.completed_stages = []
            row.counts = {}
            row.errors = []
            row.after_counts = {}
            row.before_counts = await snapshot(db, user_id)
            row.started_at = now
        row.status = BackfillStatus.RUNNING.value
        row.finished_at = None
        row.attempts += 1
        row.updated_at = now
        await db.commit()
        await db.refresh(row)
        return row


def view(row: GameIdentityBackfill | None) -> dict[str, Any]:
    if row is None:
        return {"status": "not_started", "stages": [name for name, _ in STAGES]}
    counts = {k: v for k, v in (row.counts or {}).items() if not k.startswith("_")}
    return {
        "status": row.status,
        "version": row.version,
        "stage": row.stage,
        "stages": [name for name, _ in STAGES],
        "completed_stages": list(row.completed_stages or []),
        "counts": counts,
        "errors": list(row.errors or []),
        "before": dict(row.before_counts or {}),
        "after": dict(row.after_counts or {}),
        "attempts": row.attempts,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
    }


async def status(db: AsyncSession, user_id: UUID) -> dict[str, Any]:
    return view(await db.get(GameIdentityBackfill, user_id))


async def run_user(
    user_id: UUID, *, batch_size: int = DEFAULT_BATCH, reset: bool = False
) -> dict[str, Any]:
    """Run (or resume) the backfill for one user and return its report.

    Never raises for a stage failure: the failure is recorded (error class only, no
    library content), the committed chunks stay, and calling again resumes."""
    async with SessionLocal() as lock_db:
        key = await lock_db.scalar(select(func.hashtextextended(_lock_key(user_id), 102)))
        if not await lock_db.scalar(select(func.pg_try_advisory_lock(key))):
            raise BackfillBusy("A backfill for this user is already running")
        try:
            return await _run_locked(user_id, batch_size, reset)
        finally:
            await lock_db.execute(select(func.pg_advisory_unlock(key)))


async def _run_locked(user_id: UUID, batch_size: int, reset: bool) -> dict[str, Any]:
    row = await _start(user_id, reset=reset)
    done = list(row.completed_stages or [])
    progress = Progress(user_id)
    for name, stage in STAGES:
        if name in done:
            continue
        await _record(user_id, stage=name)
        try:
            progress.set_cursor(None)
            await _flush_only(progress)
            await stage(progress, batch_size)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            await _fail(user_id, name, exc)
            break
        done.append(name)
        await _finish_stage(progress, user_id, done)
    else:
        await _verify(user_id)
    async with SessionLocal() as db:
        return view(await db.get(GameIdentityBackfill, user_id))


async def _flush_only(progress: Progress) -> None:
    async def nothing(_db: AsyncSession) -> None:
        return None

    await progress.transaction(nothing)


async def _finish_stage(progress: Progress, user_id: UUID, done: list[str]) -> None:
    progress.set_cursor(None)

    async def work(db: AsyncSession) -> None:
        row = await db.get(GameIdentityBackfill, user_id, with_for_update=True)
        assert row is not None
        row.completed_stages = list(done)

    await progress.transaction(work)


async def _fail(user_id: UUID, stage: str, exc: BaseException) -> None:
    async with SessionLocal() as db:
        row = await db.get(GameIdentityBackfill, user_id, with_for_update=True)
        if row is None:
            return
        code = getattr(getattr(exc, "orig", None), "sqlstate", None) or type(exc).__name__
        row.status = BackfillStatus.FAILED.value
        row.errors = [
            *(row.errors or []),
            {"stage": stage, "code": f"stage_failed:{type(exc).__name__}:{code}", "count": 1},
        ][:100]
        row.updated_at = row.finished_at = int(time.time())
        await db.commit()


async def _verify(user_id: UUID) -> None:
    async with SessionLocal() as db:
        row = await db.get(GameIdentityBackfill, user_id, with_for_update=True)
        assert row is not None
        after = await snapshot(db, user_id)
        outcome, findings = judge(dict(row.before_counts or {}), after)
        row.after_counts = after
        row.status = outcome
        row.stage = "verify"
        row.errors = [*(row.errors or []), *findings][:100]
        row.updated_at = row.finished_at = int(time.time())
        await db.commit()


async def run_all(
    *, batch_size: int = DEFAULT_BATCH, reset: bool = False
) -> dict[str, dict[str, Any]]:
    """Every user in turn (the operator command). A busy or failed user does not stop the rest."""
    async with SessionLocal() as db:
        users = list(await db.scalars(select(User.id).order_by(User.id)))
    reports: dict[str, dict[str, Any]] = {}
    for user_id in users:
        try:
            reports[str(user_id)] = await run_user(user_id, batch_size=batch_size, reset=reset)
        except BackfillBusy:
            reports[str(user_id)] = {"status": "busy"}
    return reports


async def plan(db: AsyncSession, user_id: UUID) -> dict[str, int]:
    """What a run would still do (the dry run): read-only coverage numbers."""
    current = await snapshot(db, user_id)
    dup = await db.scalar(
        text(
            "SELECT count(*) FROM (SELECT 1 FROM games WHERE user_id = :u AND external_id IS NOT NULL "
            "GROUP BY lower(coalesce(source, '')), external_id HAVING count(*) > 1) d"
        ),
        {"u": user_id},
    )
    return {
        "games": current["games"],
        "games_without_identity": current["games_without_identity"],
        "games_without_source_link": current["games_without_source_link"],
        "owned_copy_links": int(
            await db.scalar(
                select(func.count())
                .select_from(Game)
                .where(Game.user_id == user_id, Game.relationship_type == "owned_copy")
            )
            or 0
        ),
        "other_relationships": int(
            await db.scalar(
                select(func.count())
                .select_from(Game)
                .where(
                    Game.user_id == user_id,
                    Game.parent_game_id.is_not(None),
                    Game.relationship_type != "owned_copy",
                )
            )
            or 0
        ),
        "duplicate_dismissals": current["duplicate_dismissals"],
        "shared_source_record_groups": int(dup or 0),
    }
