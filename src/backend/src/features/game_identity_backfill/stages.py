"""The backfill stages. Each is idempotent and resumable: it only adds what is
missing, never rewrites or removes what exists, and commits in bounded chunks."""

from __future__ import annotations

# SQLAlchemy's `func` is a dynamic namespace pylint cannot see through.
# pylint: disable=not-callable
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, TypeVar
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.database.models.achievement import Achievement
from src.database.models.game import Game
from src.database.models.game_duplicate_dismissal import GameDuplicateDismissal
from src.database.models.game_identity import (
    DecisionActor,
    DecisionOutcome,
    DecisionSubject,
    GameIdentity,
    GameIdentityDecision,
    GameRelationship,
    GameSourceConnection,
    GameSourceObservation,
    GameSourceObservationLink,
    GameUmbrella,
    IdentityKind,
    RecordOrigin,
)
from src.database.models.game_identity_tracking import (
    FieldValueOrigin,
    FieldValueState,
    GameFieldValue,
    LockOrigin,
)
from src.database.session import SessionLocal
from src.features.game_identity import counters, decisions, graph, manual, names, review
from src.features.game_identity_backfill import mapping
from src.features.game_identity_backfill.mapping import GameRow
from src.features.game_identity_backfill.progress import Progress
from src.features.game_ingestion.contract import ObservationBatchV1, SourceObservationV1
from src.features.game_ingestion.service import find_connection, ingest_batch

T = TypeVar("T")
ACTOR = decisions.Actor(DecisionActor.MIGRATION.value, "legacy-backfill")
_COLUMNS = (
    "id title sort_title source external_id playnite_guid platform release_date status "
    "playtime_seconds last_played_at favorite stale_since parent_game_id relationship_type "
    "created_at deleted_at"
).split()


def chunks(items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def load_rows(db: AsyncSession, user_id: UUID) -> list[GameRow]:
    """Every game of the user, trashed ones included, in ID order."""
    found = await db.execute(
        select(*(getattr(Game, name) for name in _COLUMNS))
        .where(Game.user_id == user_id)
        .order_by(Game.id)
    )
    return [GameRow(**row) for row in found.mappings()]


# --- observations -----------------------------------------------------------------------------


async def _achievement_counts(
    db: AsyncSession, game_ids: Iterable[UUID]
) -> dict[UUID, dict[str, tuple[int, int]]]:
    found: dict[UUID, dict[str, tuple[int, int]]] = {}
    rows = await db.execute(
        select(
            Achievement.game_id,
            Achievement.provider,
            func.count(),
            func.count().filter(Achievement.unlocked.is_(True)),
        )
        .where(
            Achievement.game_id.in_(list(game_ids)),
            Achievement.provider.in_(list(mapping.ACHIEVEMENT_PROVIDERS)),
        )
        .group_by(Achievement.game_id, Achievement.provider)
    )
    for game_id, provider, total, unlocked in rows:
        found.setdefault(game_id, {})[mapping.ACHIEVEMENT_PROVIDERS[provider]] = (total, unlocked)
    return found


async def _existing_observations(
    db: AsyncSession,
    user_id: UUID,
    connection: mapping.SourceConnectionRef,
    external_ids: list[str],
) -> dict[str, UUID]:
    known = await find_connection(db, user_id, connection)
    if known is None:
        return {}
    rows = await db.execute(
        select(GameSourceObservation.external_id, GameSourceObservation.id).where(
            GameSourceObservation.connection_id == known.id,
            GameSourceObservation.external_id.in_(external_ids),
        )
    )
    return dict(rows.all())  # type: ignore[arg-type]


async def _observe_chunk(  # pylint: disable=too-many-locals
    db: AsyncSession,
    p: Progress,
    batch: Sequence[GameRow],
    winners: dict[tuple[str, str, str, str], UUID],
) -> None:
    user_id = p.user_id
    achievements = await _achievement_counts(db, (game.id for game in batch))
    wanted: dict[tuple[str, str, str], tuple[Any, list[tuple[UUID, SourceObservationV1]]]] = {}
    for game in batch:
        for claim in mapping.claims_for(game):
            primary = winners[mapping.connection_key(claim)] == game.id
            connection = (
                claim.connection
                if primary
                else mapping.duplicate_connection(claim.connection, game.id)
            )
            observation = mapping.build_observation(game, claim, achievements.get(game.id, {}))
            if observation is None:
                p.anomaly("observations", "observation_invalid", game.id)
                continue
            p.add("duplicate_source_records" if not primary else "source_records")
            key = (connection.source_kind, connection.source, connection.source_scope)
            wanted.setdefault(key, (connection, []))[1].append((game.id, observation))
    for connection, items in wanted.values():
        known = await _existing_observations(
            db, user_id, connection, [item.external_id for _, item in items]
        )
        fresh = [item for _, item in items if item.external_id not in known]
        if fresh:
            result = await ingest_batch(
                db, user_id, ObservationBatchV1(connection=connection, observations=fresh)
            )
            known.update({r.external_id: r.observation_id for r in result.results})
            p.add("observations_created", len(fresh))
        p.add("observations_existing", len(items) - len(fresh))
        await _link(db, p, {known[item.external_id]: game_id for game_id, item in items})


async def _link(db: AsyncSession, p: Progress, targets: dict[UUID, UUID]) -> None:
    """Link records that have no link history; a record ever linked keeps its state."""
    history = {
        row.observation_id: row
        for row in await db.execute(
            select(
                GameSourceObservationLink.observation_id,
                GameSourceObservationLink.game_id,
                GameSourceObservationLink.unlinked_at,
            ).where(GameSourceObservationLink.observation_id.in_(list(targets)))
        )
    }
    stamp = graph.now_seconds()
    rows = []
    for observation_id, game_id in targets.items():
        if observation_id in history:
            current = history[observation_id]
            if current.unlinked_at is None and current.game_id == game_id:
                p.add("links_existing")
            else:
                p.add("links_elsewhere")  # a sync or a person already placed this record
            continue
        rows.append(
            {
                "user_id": p.user_id,
                "observation_id": observation_id,
                "game_id": game_id,
                "origin": RecordOrigin.MIGRATION.value,
                "linked_at": stamp,
            }
        )
    if rows:
        await db.execute(pg_insert(GameSourceObservationLink).values(rows))
        p.add("links_created", len(rows))


async def observations(p: Progress, batch_size: int) -> None:
    async with SessionLocal() as db:
        rows = await load_rows(db, p.user_id)
    winners = mapping.collision_winners(rows)
    cursor = await p.cursor(p.user_id)
    pending = [game for game in rows if cursor is None or str(game.id) > cursor]
    for batch in chunks(pending, batch_size):

        async def work(db: AsyncSession, batch: Sequence[GameRow] = batch) -> None:
            await graph.lock_user_identities(db, p.user_id)
            await _observe_chunk(db, p, batch, winners)
            p.add("games_seen", len(batch))
            p.set_cursor(str(batch[-1].id))

        await p.transaction(work)


# --- merges, placement, relationships ---------------------------------------------------------


def _parents(rows: Sequence[GameRow], *, copies: bool) -> Iterator[tuple[GameRow, bool]]:
    known = {game.id for game in rows}
    for game in rows:
        if game.parent_game_id is None:
            continue
        is_copy = game.relationship_type == mapping.OWNED_COPY
        if is_copy != copies:
            continue
        yield game, game.parent_game_id in known


async def merges(p: Progress, batch_size: int) -> None:
    """Owned-copy groups are the user's own earlier decision: merge them (never by title)."""
    async with SessionLocal() as db:
        rows = await load_rows(db, p.user_id)
    pairs: list[tuple[UUID, UUID]] = []
    for game, parent_known in _parents(rows, copies=True):
        if parent_known and game.parent_game_id not in (None, game.id):
            assert game.parent_game_id is not None
            pairs.append((game.id, game.parent_game_id))
        else:
            p.anomaly("merges", "owned_copy_parent_invalid", game.id)
    for batch in chunks(pairs, batch_size):

        async def work(db: AsyncSession, batch: Sequence[tuple[UUID, UUID]] = batch) -> None:
            for copy_id, main_id in batch:
                try:
                    async with db.begin_nested():
                        result = await decisions.merge_identities(
                            db,
                            p.user_id,
                            copy_id,
                            main_id,
                            decisions.Actor(
                                ACTOR.kind,
                                ACTOR.detail,
                                "Owned copy group from the earlier library",
                            ),
                        )
                except graph.IdentityConflict:
                    p.anomaly("merges", "owned_copy_kept_separate", copy_id)
                except (graph.IdentityInvalid, LookupError):
                    p.anomaly("merges", "owned_copy_invalid", copy_id)
                else:
                    p.add("owned_copies_merged" if result.merged else "owned_copies_existing")

        await p.transaction(work)


async def placement(p: Progress, batch_size: int) -> None:
    """Every game, trashed ones included, gets a place in exactly one umbrella."""
    async with SessionLocal() as db:
        rows = {game.id: game for game in await load_rows(db, p.user_id)}
        merge = await graph.merge_graph(db, p.user_id)
        placed = set(
            await db.scalars(select(GameIdentity.game_id).where(GameIdentity.user_id == p.user_id))
        )
    groups: dict[UUID, list[GameRow]] = {}
    for game_id in rows:
        if game_id not in placed:
            groups.setdefault(merge.root(game_id), [])
    for root in groups:
        groups[root] = sorted(
            (rows[m] for m in merge.group(root) if m in rows), key=lambda g: g.order
        )
    for batch in chunks(sorted(groups.items(), key=lambda item: str(item[0])), batch_size):

        async def work(db: AsyncSession, batch: Sequence[Any] = batch) -> None:
            await graph.lock_user_identities(db, p.user_id)
            # one lookup for the whole batch rather than one per group
            placed_now = {
                row.game_id: row.umbrella_id
                for row in await db.execute(
                    select(GameIdentity.game_id, GameIdentity.umbrella_id).where(
                        GameIdentity.user_id == p.user_id,
                        GameIdentity.game_id.in_([m.id for _, group in batch for m in group]),
                    )
                )
            }
            identities: list[GameIdentity] = []
            for _, members in batch:
                await _place_group(db, p, members, placed_now, identities)
            await db.flush()  # the umbrellas, which the identities reference
            db.add_all(identities)
            await db.flush()

        await p.transaction(work)


async def _place_group(  # pylint: disable=too-many-locals,too-many-arguments,too-many-positional-arguments
    db: AsyncSession,
    p: Progress,
    members: list[GameRow],
    placed: dict[UUID, UUID],
    identities: list[GameIdentity],
) -> None:
    found = {m.id: placed[m.id] for m in members if m.id in placed}
    todo = [m for m in members if m.id not in found]
    if not todo:
        return
    stamp = graph.now_seconds()
    live = [m for m in members if m.deleted_at is None]
    head = (live or members)[0]
    if found:
        umbrella_id = min(found.values(), key=str)
        has_primary = bool(
            await db.scalar(
                select(GameIdentity.game_id).where(
                    GameIdentity.umbrella_id == umbrella_id, GameIdentity.is_primary.is_(True)
                )
            )
        )
    else:
        umbrella = GameUmbrella(
            id=uuid4(),  # chosen here so the umbrella and its identities go out in one flush
            user_id=p.user_id,
            title=head.title.strip() or "Untitled game",
            sort_title=head.sort_title or head.title.lower(),
            created_at=stamp,
            updated_at=stamp,
        )
        db.add(umbrella)
        umbrella_id, has_primary = umbrella.id, False
        p.add("umbrellas_created")
    ids = {m.id for m in members}
    for position, game in enumerate(members):
        if game.id in found:
            continue
        copy = game.relationship_type == mapping.OWNED_COPY and game.parent_game_id in ids
        primary = not has_primary and game.id == head.id
        has_primary = has_primary or primary
        grouped = len(members) > 1
        identities.append(
            GameIdentity(
                user_id=p.user_id,
                game_id=game.id,
                umbrella_id=umbrella_id,
                kind=(
                    IdentityKind.COPY
                    if copy
                    else IdentityKind.EDITION
                    if grouped
                    else IdentityKind.STANDARD
                ).value,
                label=(game.platform or None) if grouped else None,
                is_primary=primary,
                position=position,
                created_at=stamp,
                updated_at=stamp,
            )
        )
    p.add("identities_created", len(todo))


async def relationships(p: Progress, batch_size: int) -> None:
    """DLC, mods and expansions become typed relationships (the old columns stay)."""
    async with SessionLocal() as db:
        rows = await load_rows(db, p.user_id)
        known = {
            (e.kind, e.from_game_id, e.to_game_id)
            for e in await db.scalars(
                select(GameRelationship).where(GameRelationship.user_id == p.user_id)
            )
        }
    work_items = []
    for game, parent_known in _parents(rows, copies=False):
        kind = mapping.RELATIONSHIP_KINDS.get(game.relationship_type or "")
        if kind is None:
            p.anomaly("relationships", "relationship_type_unmapped", game.id)
        elif not parent_known:
            p.anomaly("relationships", "parent_missing", game.id)
        elif (kind, game.id, game.parent_game_id) in known:
            p.add("relationships_existing")  # created earlier, or ended by the user
        else:
            work_items.append((kind, game.id, game.parent_game_id))
    for batch in chunks(work_items, batch_size):

        async def work(db: AsyncSession, batch: Sequence[Any] = batch) -> None:
            for kind, child, parent in batch:
                try:
                    async with db.begin_nested():
                        await graph.add_relationship(
                            db, p.user_id, kind, child, parent, origin=RecordOrigin.MIGRATION.value
                        )
                except (graph.IdentityInvalid, LookupError):
                    p.anomaly("relationships", "relationship_invalid", child)
                else:
                    p.add("relationships_created")

        await p.transaction(work)


# --- decisions and review ---------------------------------------------------------------------


async def dismissals(p: Progress, batch_size: int) -> None:
    """Each 'keep both' becomes a durable `confirmed_separate` decision."""
    async with SessionLocal() as db:
        found = list(
            await db.execute(
                select(
                    GameDuplicateDismissal.first_game_id,
                    GameDuplicateDismissal.second_game_id,
                    GameDuplicateDismissal.created_at,
                ).where(GameDuplicateDismissal.user_id == p.user_id)
            )
        )
    for batch in chunks(found, batch_size):

        async def work(db: AsyncSession, batch: Sequence[Any] = batch) -> None:
            await graph.lock_user_identities(db, p.user_id)
            merge = await graph.merge_graph(db, p.user_id)
            for first, second, created in batch:
                low, high = (first, second) if first < second else (second, first)
                if merge.root(low) == merge.root(high):
                    p.add("dismissals_in_merged_group")
                    continue
                current = await db.scalar(
                    select(GameIdentityDecision.id).where(
                        GameIdentityDecision.user_id == p.user_id,
                        GameIdentityDecision.subject_kind == DecisionSubject.IDENTITY_PAIR.value,
                        GameIdentityDecision.first_game_id == low,
                        GameIdentityDecision.second_game_id == high,
                        GameIdentityDecision.superseded_at.is_(None),
                    )
                )
                if current is not None:
                    p.add("dismissals_already_decided")
                    continue
                await decisions.write_decision(
                    db,
                    p.user_id,
                    DecisionSubject.IDENTITY_PAIR.value,
                    {"first_game_id": low, "second_game_id": high},
                    DecisionOutcome.CONFIRMED_SEPARATE.value,
                    decisions.Actor(
                        ACTOR.kind, ACTOR.detail, "Kept separate in the earlier duplicate review"
                    ),
                    evidence={"migrated_from": "game_duplicate_dismissals"},
                    now=created,
                )
                p.add("dismissals_migrated")

        await p.transaction(work)


async def review_queue(p: Progress, batch_size: int) -> None:
    """Rows sharing one source record are queued for review, never merged."""
    dup_conn, main_conn = aliased(GameSourceConnection), aliased(GameSourceConnection)
    dup_obs, main_obs = aliased(GameSourceObservation), aliased(GameSourceObservation)
    dup_link, main_link = aliased(GameSourceObservationLink), aliased(GameSourceObservationLink)
    query = (
        select(main_link.game_id, dup_link.game_id, dup_conn.source)
        .select_from(dup_conn)
        .join(dup_obs, dup_obs.connection_id == dup_conn.id)
        .join(dup_link, (dup_link.observation_id == dup_obs.id) & dup_link.unlinked_at.is_(None))
        .join(
            main_conn,
            (main_conn.user_id == dup_conn.user_id)
            & (main_conn.source_kind == dup_conn.source_kind)
            & (main_conn.source == dup_conn.source)
            & (main_conn.source_scope == mapping.LEGACY_SOURCE_SCOPE),
        )
        .join(
            main_obs,
            (main_obs.connection_id == main_conn.id)
            & (main_obs.external_id == dup_obs.external_id),
        )
        .join(
            main_link, (main_link.observation_id == main_obs.id) & main_link.unlinked_at.is_(None)
        )
        .where(
            dup_conn.user_id == p.user_id,
            dup_conn.source_scope.like(f"{mapping.DUPLICATE_SCOPE_PREFIX}%"),
            main_link.game_id != dup_link.game_id,
        )
    )
    async with SessionLocal() as db:
        pairs = [tuple(row) for row in await db.execute(query)]
    for batch in chunks(sorted(pairs, key=str), batch_size):

        async def work(db: AsyncSession, batch: Sequence[Any] = batch) -> None:
            for winner, loser, source in batch:
                result = await decisions.associate_automatically(
                    db,
                    p.user_id,
                    winner,
                    loser,
                    rule="legacy-duplicate-source-id",
                    rule_version="1",
                    confidence=0.7,
                    reason="Two library rows carry the same source record ID",
                    evidence={"matched": ["source_record_id"], "source": source},
                    merge=False,
                )
                p.add(f"duplicate_source_pairs_{result.outcome}")

        await p.transaction(work)


# --- field provenance, counters, matching -----------------------------------------------------


async def fields(p: Progress, batch_size: int) -> None:  # pylint: disable=too-many-locals
    """Name provenance and the status's origin; existing locks keep their legacy origin."""
    async with SessionLocal() as db:
        rows = await load_rows(db, p.user_id)
    cursor = await p.cursor(p.user_id)
    by_id = {game.id: game for game in rows}
    pending = [game for game in rows if cursor is None or str(game.id) > cursor]
    for batch in chunks(pending, batch_size):

        async def work(db: AsyncSession, batch: Sequence[Any] = batch) -> None:
            ids = [game.id for game in batch]
            games = list(
                await db.scalars(
                    select(Game)
                    .where(Game.user_id == p.user_id, Game.id.in_(ids))
                    .order_by(Game.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            report = await names.reconcile_games(db, p.user_id, games)
            p.add("titles_tracked", report.tracked)
            have = set(
                await db.scalars(
                    select(GameFieldValue.game_id).where(
                        GameFieldValue.user_id == p.user_id,
                        GameFieldValue.game_id.in_(ids),
                        GameFieldValue.field == manual.COMPLETION_FIELD,
                        GameFieldValue.state == FieldValueState.CURRENT.value,
                    )
                )
            )
            stamp = graph.now_seconds()
            for game in games:
                if game.id in have:
                    continue
                lock = mapping.completion_lock(by_id[game.id]) and not manual.is_status_locked(game)
                db.add(
                    GameFieldValue(
                        user_id=p.user_id,
                        game_id=game.id,
                        field=manual.COMPLETION_FIELD,
                        value=game.status.value,
                        state=FieldValueState.CURRENT.value,
                        origin=FieldValueOrigin.LEGACY.value,
                        recorded_at=stamp,
                        locked=lock or manual.is_status_locked(game),
                        lock_origin=(
                            LockOrigin.LEGACY.value
                            if lock or manual.is_status_locked(game)
                            else None
                        ),
                    )
                )
                if lock:
                    game.locked_fields = sorted({*(game.locked_fields or []), manual.STATUS_LOCK})
                    p.add("statuses_protected")
                p.add("completion_tracked")
            await db.flush()
            p.set_cursor(str(batch[-1].id))

        await p.transaction(work)


async def counter_groups(p: Progress, batch_size: int) -> None:
    async with SessionLocal() as db:
        ids = [game.id for game in await load_rows(db, p.user_id)]
    cursor = await p.cursor(p.user_id)
    for batch in chunks([i for i in ids if cursor is None or str(i) > cursor], batch_size):

        async def work(db: AsyncSession, batch: Sequence[Any] = batch) -> None:
            p.add("counter_groups_synced", await counters.sync_many(db, p.user_id, batch))
            p.set_cursor(str(batch[-1]))

        await p.transaction(work)


async def matching(p: Progress, _batch_size: int) -> None:
    """Queue corroborated candidates for review. Nothing merges: the inventory is unconfirmed."""

    async def work(db: AsyncSession) -> None:
        result = await review.run_matching(db, p.user_id, inventory_complete=False)
        p.add("review_suggested", result.suggested)
        p.add("review_associated", result.associated)

    await p.transaction(work)
