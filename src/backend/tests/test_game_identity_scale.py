"""Query cost of the identity views and matcher does not grow with the library (#103).

A library of 40 games and one four times larger must cost the same number of SQL statements
for every read of one logical game (no per-row work, no whole-library ORM loads hidden behind
a constant query count is checked separately in the profile report). Absolute ceilings catch
a regression to per-game or per-pair queries.
"""

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import event, insert

from src.database.models.game import Game
from src.database.session import SessionLocal, engine
from src.features.game_identity import counters, decisions, overview, review
from src.features.game_identity_backfill import runner
from src.features.game_ingestion import legacy
from src.features.game_ingestion.contract import ObservationBatchV1, SourceObservationV1
from src.features.game_ingestion.service import ingest_batch
from tests.test_game_ingestion import users  # noqa: F401

HOUR = 3600


class Statements:
    """Counts SQL statements issued inside a `with` block."""

    def __init__(self):
        self.count = 0
        self.games_loaded = 0  # full `Game` rows materialised as ORM objects

    def _hit(self, *_args):
        self.count += 1

    def _loaded(self, *_args):
        self.games_loaded += 1

    def __enter__(self):
        event.listen(engine.sync_engine, "before_cursor_execute", self._hit)
        event.listen(Game, "load", self._loaded)
        return self

    def __exit__(self, *_exc):
        event.remove(engine.sync_engine, "before_cursor_execute", self._hit)
        event.remove(Game, "load", self._loaded)


async def library(user, size):
    """`size` Steam games; a quarter also exist as a Playnite copy; half of those are merged."""
    rows, steam, playnite, pairs = [], [], [], []
    for index in range(size):
        game_id, title = uuid4(), f"Scale Title {index}"
        rows.append(_row(user, game_id, title, "Steam", index))
        steam.append((game_id, str(1000 + index), title))
        if index % 4 == 0:
            copy = uuid4()
            rows.append(_row(user, copy, title, "Playnite", index, guid=uuid4()))
            playnite.append((copy, str(uuid4()), title))
            pairs.append((game_id, copy))
    async with SessionLocal() as db:
        await db.execute(insert(Game), rows)
        await db.commit()
    for connection, entries in ((legacy.STEAM, steam), (legacy.PLAYNITE, playnite)):
        batch = ObservationBatchV1(
            connection=connection,
            observations=[
                SourceObservationV1(
                    external_id=ext, title=title, facts={"playtime_seconds": 3 * HOUR}
                )
                for _, ext, title in entries
            ],
        )
        async with SessionLocal() as db:
            await ingest_batch(db, user, batch, attach={ext: gid for gid, ext, _ in entries})
            await db.commit()
    merged = pairs[: len(pairs) // 2]
    for first, second in merged:
        async with SessionLocal() as db:
            await decisions.merge_identities(
                db, user, first, second, decisions.Actor(reason="seed")
            )
            await db.commit()
    return {"merged": merged, "plain": steam[-1][0], "unmerged_pairs": pairs[len(pairs) // 2 :]}


def _row(user, game_id, title, source, index, guid=None):
    return {
        "id": game_id,
        "user_id": user,
        "title": title,
        "sort_title": title.lower(),
        "folder_location": f"s{index}-{game_id.hex[:8]}",
        "platform": "PC",
        "release_date": date(2020, 1, 1),
        "playtime_seconds": (5 if guid else 3) * HOUR,  # similar, not identical: needs review
        "source": source,
        "external_id": None if guid else str(1000 + index),
        "playnite_guid": guid,
        "tags": [],
        "features": [],
        "collections": [],
        "locked_fields": [],
        "provider_ids": {},
    }


async def costs(user, shape):
    """Statements per read of one logical game and of the layout, queue and banner."""
    first = shape["merged"][0][0]
    out = {}
    reads = {
        "layout": lambda db: overview.library_layout(db, user),
        "overview": lambda db: overview.game_overview(db, user, first),
        "sources": lambda db: overview.identity_sources(db, user, first),
        "metrics": lambda db: counters.reconcile(db, user, first),
        "queue": lambda db: review.queue(db, user),
        "banner": lambda db: review.automatic_merges(db, user),
    }
    for name, read in reads.items():
        async with SessionLocal() as db:
            await read(db)  # first read may materialise counters; measure the steady state
            await db.commit()
        async with SessionLocal() as db:
            with Statements() as seen:
                await read(db)
            await db.commit()
        out[name] = seen.count
        out[f"{name}_games_loaded"] = seen.games_loaded
    return out


async def test_reads_cost_the_same_for_a_small_and_a_large_library(users):  # noqa: F811
    small, large = await users(), await users()
    small_shape = await library(small, 40)
    large_shape = await library(large, 160)
    small_cost, large_cost = await costs(small, small_shape), await costs(large, large_shape)
    assert small_cost == large_cost, (small_cost, large_cost)  # no per-row work
    # the layout also refreshes the groups' playtime counters (#277), a constant number of statements
    ceilings = {"layout": 32, "overview": 90, "sources": 70, "metrics": 60, "queue": 8, "banner": 6}
    for name, ceiling in ceilings.items():
        assert large_cost[name] <= ceiling, (name, large_cost[name])
    # reading one logical game loads that group's rows, never the library's
    for name in ("layout", "overview", "sources", "queue", "banner"):
        assert large_cost[f"{name}_games_loaded"] <= 30, (name, large_cost)


async def test_the_matcher_costs_per_new_decision_and_nothing_for_a_repeat(users):  # noqa: F811
    small, large = await users(), await users()
    await library(small, 40)
    await library(large, 160)
    first, repeat = {}, {}
    for label, user in (("small", small), ("large", large)):
        async with SessionLocal() as db:
            with Statements() as seen:
                result = await review.run_matching(db, user)
            await db.commit()
        first[label] = (seen.count, result.associated + result.suggested)
        async with SessionLocal() as db:
            with Statements() as seen:
                again = await review.run_matching(db, user)
            await db.commit()
        repeat[label] = seen.count
        assert (again.associated, again.suggested) == (0, 0)
    for label, (statements, decided) in first.items():
        assert decided > 0
        assert statements <= 20 + 14 * decided, (
            label,
            statements,
            decided,
        )  # no per-pair graph reads
    assert repeat["small"] == repeat["large"] <= 12  # an idempotent re-run is O(1) statements


@pytest.mark.parametrize("size", [40])
async def test_a_playnite_page_costs_a_constant_number_of_statements(users, size):  # noqa: F811
    user = await users()
    shape = await library(user, size)
    assert shape["unmerged_pairs"]
    for count in (2, 10):
        batch = ObservationBatchV1(
            connection=legacy.PLAYNITE,
            observations=[
                SourceObservationV1(
                    external_id=f"page-{i}", title=f"Page {i}", facts={"playtime_seconds": 60}
                )
                for i in range(count)
            ],
        )
        async with SessionLocal() as db:
            with Statements() as seen:
                await ingest_batch(db, user, batch)
            await db.commit()
        if count == 2:
            baseline = seen.count
        assert seen.count <= baseline + 6, (count, seen.count, baseline)


async def legacy_library(user, size):
    """Games only, as an installation that predates the identity model holds them."""
    rows = [_row(user, uuid4(), f"Legacy Title {i}", "Steam", i) for i in range(size)]
    async with SessionLocal() as db:
        await db.execute(insert(Game), rows)
        await db.commit()


async def test_backfill_statements_do_not_grow_per_game(users):  # noqa: F811
    small, large = await users(), await users()
    await legacy_library(small, 40)
    await legacy_library(large, 160)
    cost = {}
    for label, user in (("small", small), ("large", large)):
        for run in ("first", "repeat"):
            with Statements() as seen:
                report = await runner.run_user(user)
            assert report["status"] == "completed", report
            cost[label, run] = seen.count
    # four times the games, the same statements (every set is far below the batch size)
    assert cost["large", "first"] <= cost["small", "first"] + 10, cost
    assert cost["large", "repeat"] <= cost["small", "repeat"] + 10, cost
    assert cost["large", "first"] < 400, cost  # was ~14 statements per game before sets
