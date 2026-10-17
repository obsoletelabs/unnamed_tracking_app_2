#!/usr/bin/env python3
"""Profile the game identity model against a realistic large library (#103).

Seeds a throw-away library through the same services the application uses
(ingestion, merge decisions, achievements), then measures the hot read, write and
sync paths: wall time, number of SQL statements per request, and concurrency.

    cd src/backend && export PYTHONPATH=$PWD
    DATABASE_URL=postgresql+psycopg://... alembic upgrade head
    python ../../tools/profile_game_identity.py --games 20000 --out profile-20000.json

Run it only against a scratch database: it creates users named ``profile_*`` and
deletes them (cascading) when it finishes, unless ``--keep`` is passed.
``--explain`` prints EXPLAIN (ANALYZE, BUFFERS) for the queries behind the hot paths.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from datetime import date
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, event, insert, text

from src.api.routes.library_sync import _replace_achievements
from src.core.auth import get_current_user
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import SessionLocal, engine
from src.features.game_identity import decisions
from src.features.game_identity_backfill import runner
from src.features.game_ingestion import legacy
from src.features.game_ingestion.contract import ObservationBatchV1, SourceObservationV1
from src.features.game_ingestion.service import ingest_batch
from src.main import app

HOUR = 3600
ADJECTIVES = (
    "Crimson", "Silent", "Hollow", "Neon", "Ancient", "Broken", "Golden", "Lost", "Final",
    "Wandering", "Frozen", "Burning", "Hidden", "Eternal", "Rusty", "Velvet", "Iron", "Pale",
)  # fmt: skip
NOUNS = (
    "Kingdom", "Odyssey", "Horizon", "Frontier", "Legacy", "Citadel", "Harbor", "Protocol",
    "Voyage", "Reckoning", "Gambit", "Archive", "Meridian", "Dominion", "Echo", "Sanctuary",
)  # fmt: skip
STATUSES = ("BACKLOG", "PLAYING", "PLAYED", "BEATEN", "MASTERED", "WISHLIST")


class Counter:
    """Counts SQL statements issued while a block runs."""

    def __init__(self) -> None:
        self.statements = 0
        self.slowest = 0.0
        self._started: list[float] = []
        event.listen(engine.sync_engine, "before_cursor_execute", self._before)
        event.listen(engine.sync_engine, "after_cursor_execute", self._after)

    def _before(self, *_args) -> None:
        self.statements += 1
        self._started.append(time.perf_counter())

    def _after(self, *_args) -> None:
        if self._started:
            self.slowest = max(self.slowest, time.perf_counter() - self._started.pop())

    def reset(self) -> None:
        self.statements = 0
        self.slowest = 0.0


def title_for(index: int) -> str:
    adjective = ADJECTIVES[index % len(ADJECTIVES)]
    noun = NOUNS[(index // len(ADJECTIVES)) % len(NOUNS)]
    return f"{adjective} {noun} {index // (len(ADJECTIVES) * len(NOUNS)) + 1}"


async def make_user(label: str) -> UUID:
    user_id = uuid4()
    async with SessionLocal() as db:
        await db.execute(
            insert(User).values(
                id=user_id,
                username=f"profile_{label}_{user_id.hex[:8]}",
                email=f"{user_id}@profile.test",
                password_hash="x",
            )
        )
        await db.commit()
    return user_id


async def seed(user_id: UUID, games: int, merge_fraction: float, seed_value: int) -> dict:
    """Build the library: Steam and Playnite copies, some shared, some merged, some tracked."""
    rng = random.Random(seed_value)
    clock = time.perf_counter()
    rows, steam, playnite, pairs = [], [], [], []
    shared_titles = 0

    def add(title, source, ext, guid, hours, evidence):
        game_id = uuid4()
        rows.append(
            {
                "id": game_id,
                "user_id": user_id,
                "title": title,
                "sort_title": title.lower(),
                "folder_location": f"g{len(rows)}-{game_id.hex[:8]}",
                "source": source,
                "external_id": ext,
                "playnite_guid": guid,
                "status": rng.choice(STATUSES),
                "playtime_seconds": hours * HOUR,
                "platform": "PC",
                "release_date": date(2020, 1, 1) if evidence else None,
                "last_played_at": 1_700_000_000 if evidence else None,
                "tags": [],
                "features": [],
                "collections": [],
                "locked_fields": [],
                "provider_ids": {},
                "favorite": rng.random() < 0.05,
            }
        )
        (steam if ext else playnite).append((game_id, ext or str(guid), title, hours))
        return game_id

    index = 0
    while len(rows) < games:
        title = title_for(index)
        hours = rng.choice([1, 3, 12, 40, 150])
        if rng.random() < 0.25:  # the same title owned on Steam and in Playnite: two records
            shared_titles += 1
            flavour = rng.random()  # identical evidence / similar / title only
            other = hours if flavour < 0.4 else (hours + 5 if flavour < 0.7 else 0)
            first = add(title, "Steam", str(100000 + index), None, hours, flavour < 0.4)
            second = add(title, "Playnite", None, uuid4(), other, flavour < 0.4)
            pairs.append((first, second))
        elif rng.random() < 0.5:
            add(title, "Steam", str(100000 + index), None, hours, False)
        else:
            add(title, "Playnite", None, uuid4(), hours, False)
        index += 1
    async with SessionLocal() as db:
        for start in range(0, len(rows), 2000):
            await db.execute(insert(Game), rows[start : start + 2000])
        await db.commit()
    timings = {"games_insert_s": round(time.perf_counter() - clock, 2)}

    clock = time.perf_counter()
    for connection, entries in ((legacy.STEAM, steam), (legacy.PLAYNITE, playnite)):
        for start in range(0, len(entries), 500):
            chunk = entries[start : start + 500]
            batch = ObservationBatchV1(
                connection=connection,
                observations=[
                    SourceObservationV1(
                        external_id=ext,
                        title=title,
                        facts={"playtime_seconds": hours * HOUR, "last_played_at": 1_700_000_000},
                    )
                    for _, ext, title, hours in chunk
                ],
            )
            async with SessionLocal() as db:
                await ingest_batch(
                    db, user_id, batch, attach={ext: gid for gid, ext, _, _ in chunk}
                )
                await db.commit()
    timings["observations_s"] = round(time.perf_counter() - clock, 2)

    clock = time.perf_counter()
    merged = 0
    # a quarter of the duplicate pairs were already merged by the user
    for first, second in pairs[: int(len(pairs) * merge_fraction)]:
        async with SessionLocal() as db:
            await decisions.merge_identities(
                db, user_id, first, second, decisions.Actor(reason="profile seed")
            )
            await db.commit()
        merged += 1
    timings["merges_s"] = round(time.perf_counter() - clock, 2)

    clock = time.perf_counter()
    achievement_games = [r["id"] for r in rows if rng.random() < 0.2]
    for start in range(0, len(achievement_games), 200):
        async with SessionLocal() as db:
            for gid in achievement_games[start : start + 200]:
                await _replace_achievements(
                    db,
                    gid,
                    "Steam",
                    [
                        {"external_id": str(n), "name": f"Ach {n}", "unlocked": n % 3 == 0}
                        for n in range(10)
                    ],
                )
            await db.commit()
    timings["achievements_s"] = round(time.perf_counter() - clock, 2)
    return {
        "games": games,
        "steam_observations": len(steam),
        "playnite_observations": len(playnite),
        "duplicate_pairs": shared_titles,
        "merged_pairs": merged,
        "games_with_achievements": len(achievement_games),
        "timings": timings,
        "sample": {
            "merged": pairs[0] if merged else None,
            "plain": rows[-1]["id"],
            "playnite_ids": [e[1] for e in playnite],
        },
    }


async def timed(client, counter, method, path, *, repeat=3, **kwargs) -> dict:
    samples, queries, status, size = [], [], 0, 0
    for _ in range(repeat):
        counter.reset()
        started = time.perf_counter()
        response = await client.request(method, path, **kwargs)
        samples.append(time.perf_counter() - started)
        queries.append(counter.statements)
        status, size = response.status_code, len(response.content)
    return {
        "path": f"{method} {path.split('?')[0]}",
        "status": status,
        "median_ms": round(statistics.median(samples) * 1000, 1),
        "max_ms": round(max(samples) * 1000, 1),
        "queries": max(queries),
        "bytes": size,
    }


async def measure(user_id: UUID, info: dict) -> list[dict]:
    holder = {"id": user_id}
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=holder["id"])
    counter = Counter()
    results = []
    merged = info["sample"]["merged"]
    plain = str(info["sample"]["plain"])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://x", timeout=600) as api:
        g = "/api/game-identity"
        first = merged[0] if merged else info["sample"]["plain"]
        plan = [
            ("GET", "/api/game/list?limit=200"),
            ("GET", "/api/game/list?limit=200&search=Crimson"),
            ("GET", "/api/game/list?limit=200&search=Crimson%20Kingdom%201"),
            ("GET", f"{g}/library"),
            ("GET", f"{g}/games/{first}/overview"),
            ("GET", f"{g}/games/{plain}/overview"),
            ("GET", f"{g}/games/{first}/sources"),
            ("GET", f"{g}/games/{first}/metrics"),
            ("GET", f"{g}/games/{plain}/metrics"),
            ("GET", f"{g}/games/{first}/tracking"),
            ("GET", f"{g}/games/{first}/achievements"),
            ("GET", f"{g}/decisions"),
            ("GET", f"{g}/names/reviews"),
            ("GET", f"{g}/review/queue"),
            ("GET", f"{g}/review/automatic"),
            ("GET", f"{g}/backfill/plan"),
            ("GET", "/api/game-duplicates"),
        ]
        for method, path in plan:
            results.append(await timed(api, counter, method, path))
        for label in ("first run (decides new pairs)", "repeat run (nothing new)"):
            run = await timed(api, counter, "POST", f"{g}/review/run", repeat=1)
            run["path"] += f" - {label}"
            results.append(run)
        results.append(await timed(api, counter, "GET", f"{g}/review/queue"))
        results.append(await timed(api, counter, "GET", f"{g}/review/automatic"))

        # Playnite: the extension's page of 500 entries and its complete inventory
        guids = info["sample"]["playnite_ids"]
        page = [
            {
                "playnite_id": gid,
                "name": "x",
                "playtime_seconds": 7200,
                "modified_at": 1_800_000_000,
            }
            for gid in guids[:500]
        ]
        body = {"contract_version": 1, "games": page}
        results.append(await timed(api, counter, "POST", "/api/playnite/observations", json=body))
        inventory = {"contract_version": 1, "playnite_ids": guids[:50000]}
        results.append(
            await timed(api, counter, "POST", "/api/playnite/inventory", json=inventory, repeat=2)
        )

        if merged:
            pair = {"game_id": str(merged[0])}
            results.append(await timed(api, counter, "POST", f"{g}/unmerge", json=pair, repeat=1))
            again = {
                "source_id": str(merged[0]),
                "target_id": str(merged[1]),
                "replace_separate": True,
            }
            results.append(await timed(api, counter, "POST", f"{g}/merge", json=again, repeat=1))

        # concurrency: many readers and writers on one library at once
        counter.reset()
        started = time.perf_counter()
        calls = [api.get(f"{g}/games/{first}/overview") for _ in range(20)]
        calls += [api.get(f"{g}/library") for _ in range(5)]
        calls += [
            api.post("/api/playnite/observations", json={"contract_version": 1, "games": page[:50]})
            for _ in range(5)
        ]
        replies = await asyncio.gather(*calls)
        results.append(
            {
                "path": "CONCURRENT 20 overview + 5 library + 5 playnite batches",
                "status": sorted({r.status_code for r in replies}),
                "median_ms": round((time.perf_counter() - started) * 1000, 1),
                "max_ms": round((time.perf_counter() - started) * 1000, 1),
                "queries": counter.statements,
                "bytes": sum(len(r.content) for r in replies),
            }
        )
    app.dependency_overrides.pop(get_current_user, None)
    return results


async def measure_backfill(user_id: UUID, games: int) -> list[dict]:
    """The migration of a library that predates the identity model: games only, no source rows."""
    rng = random.Random(7)
    rows = []
    for index in range(games):
        game_id = uuid4()
        title = title_for(index)
        playnite = rng.random() < 0.4
        rows.append(
            {
                "id": game_id,
                "user_id": user_id,
                "title": title,
                "sort_title": title.lower(),
                "folder_location": f"b{index}-{game_id.hex[:8]}",
                "source": "Playnite" if playnite else "Steam",
                "external_id": None if playnite else str(500000 + index),
                "playnite_guid": uuid4() if playnite else None,
                "status": rng.choice(STATUSES),
                "playtime_seconds": rng.choice([0, 1, 12, 150]) * HOUR,
                "tags": [],
                "features": [],
                "collections": [],
                "locked_fields": [],
                "provider_ids": {},
            }
        )
    async with SessionLocal() as db:
        for start in range(0, len(rows), 2000):
            await db.execute(insert(Game), rows[start : start + 2000])
        await db.commit()
    counter = Counter()
    out = []
    for label in ("first run (does the work)", "second run (nothing left to do)"):
        counter.reset()
        started = time.perf_counter()
        report = await runner.run_user(user_id)
        out.append(
            {
                "path": f"BACKFILL {label}",
                "status": report.get("status"),
                "median_ms": round((time.perf_counter() - started) * 1000, 1),
                "max_ms": 0,
                "queries": counter.statements,
                "bytes": 0,
                "counts": report.get("counts"),
            }
        )
    return out


async def explain(user_id: UUID, info: dict) -> dict[str, list[str]]:
    """EXPLAIN (ANALYZE, BUFFERS) for the queries behind the hot paths."""
    uid = str(user_id)
    gid = str(info["sample"]["merged"][0] if info["sample"]["merged"] else info["sample"]["plain"])
    queries = {
        "library: live games of a user ordered by title (list page)": (
            "SELECT id, title FROM games WHERE user_id = :u AND deleted_at IS NULL "
            "ORDER BY sort_title OFFSET 0 LIMIT 200"
        ),
        "library: title search": (
            "SELECT id FROM games WHERE user_id = :u AND deleted_at IS NULL "
            "AND title ILIKE '%Crimson Kingdom%' ORDER BY sort_title LIMIT 200"
        ),
        "layout: active same_game edges": (
            "SELECT id, from_game_id, to_game_id FROM game_relationships "
            "WHERE user_id = :u AND kind = 'same_game' AND ended_at IS NULL"
        ),
        "overview: observation links of one game": (
            "SELECT * FROM game_source_observation_links WHERE user_id = :u AND game_id = :g"
        ),
        "ingest: lookup of one observation by source record": (
            "SELECT * FROM game_source_observations WHERE user_id = :u "
            "AND connection_id = (SELECT id FROM game_source_connections WHERE user_id = :u LIMIT 1) "
            "AND external_id = '100001'"
        ),
        "counters: counters of one game": (
            "SELECT * FROM game_metric_counters WHERE game_id = :g AND metric = 'playtime'"
        ),
        "decisions: open automatic suggestions": (
            "SELECT id FROM game_identity_decisions WHERE user_id = :u AND subject_kind = 'identity_pair' "
            "AND outcome = 'unresolved' AND actor_kind = 'automatic' AND superseded_at IS NULL"
        ),
        "decisions: the current decision of one pair": (
            "SELECT id FROM game_identity_decisions WHERE user_id = :u "
            "AND subject_kind = 'identity_pair' AND superseded_at IS NULL "
            "AND first_game_id = :g AND second_game_id = :h AND observation_id IS NULL "
            "AND first_umbrella_id IS NULL AND second_umbrella_id IS NULL"
        ),
        "decisions: rejections between two groups": (
            "SELECT id FROM game_identity_decisions WHERE user_id = :u "
            "AND subject_kind = 'identity_pair' AND outcome = 'confirmed_separate' "
            "AND superseded_at IS NULL AND ((first_game_id IN (:g, :h) AND second_game_id IN (:g, :h)))"
        ),
        "achievements: provider rows of one game": (
            "SELECT * FROM achievements WHERE game_id = :g"
        ),
        "field values: current name provenance": (
            "SELECT * FROM game_field_values WHERE user_id = :u AND game_id = :g "
            "AND field = 'title' AND state = 'current'"
        ),
    }
    other = str(info["sample"]["plain"])
    out = {}
    async with SessionLocal() as db:
        for label, sql in queries.items():
            rows = await db.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) " + sql),
                {"u": uid, "g": gid, "h": other},
            )
            out[label] = [line[0] for line in rows.all()]
        await db.rollback()
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--games", type=int, default=5000)
    parser.add_argument("--merge-fraction", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=103)
    parser.add_argument("--out", default="")
    parser.add_argument("--explain", action="store_true")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    owner = await make_user("owner")
    bystander = await make_user("other")
    legacy_user = await make_user("legacy")
    try:
        started = time.perf_counter()
        info = await seed(owner, args.games, args.merge_fraction, args.seed)
        await seed(bystander, max(args.games // 10, 50), args.merge_fraction, args.seed + 1)
        info["seed_total_s"] = round(time.perf_counter() - started, 1)
        results = await measure(owner, info)
        results += await measure_backfill(legacy_user, args.games)
        report = {k: v for k, v in info.items() if k != "sample"} | {"requests": results}
        if args.explain:
            report["explain"] = await explain(owner, info)
        text_out = json.dumps(report, indent=2, default=str)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(text_out)
        print(text_out)
    finally:
        if not args.keep:
            async with SessionLocal() as db:
                await db.execute(delete(User).where(User.id.in_([owner, bystander, legacy_user])))
                await db.commit()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
