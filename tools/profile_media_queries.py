"""Profile the media-library queries against a realistic data shape (#260).

Seeds one heavy account plus many light ones into a *scratch, migrated* PostgreSQL database (never run it against
real data), runs representative library requests, and reports statement counts, timings and the plan of every
SELECT, flagging sequential scans of large tables. Nothing is changed; indexes are added only for plans this shows
to be a problem.

    cd src/backend
    TEST_DATABASE_URL=... python ../../tools/profile_media_queries.py --titles 20000 --noise-users 200

The database must already be migrated (`alembic upgrade head`). It is filled with rows owned by throwaway users and
the rows are deleted again when the run ends.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import random
import sys
import time
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import delete, event, insert, text

sys.path.insert(0, os.getcwd())  # run from src/backend

from src.api.routes.media_common import LibraryFilters, library_page, library_progress, title_search  # noqa: E402
from src.database.models.anime import Anime, AnimeSeason, AnimeStatus  # noqa: E402
from src.database.models.movies import Movie, MovieStatus  # noqa: E402
from src.database.models.tv_show import TVSeason, TVShow, TVShowStatus  # noqa: E402
from src.database.models.user import User  # noqa: E402
from src.database.session import SessionLocal, engine  # noqa: E402

SUPPORTS_RELEVANCE = "search_columns" in inspect.signature(LibraryFilters.from_query).parameters
WORDS = "dark light night day city star blood iron golden silent last first lost hidden final broken wild river".split()
GENRES = ["Action", "Drama", "Comedy", "Sci-Fi", "Horror", "Romance", "Thriller", "Fantasy", "Animation", "Mystery"]


def title(rng: random.Random) -> str:
    return " ".join(rng.choice(WORDS).title() for _ in range(rng.randint(2, 4)))


def rows(model, status_enum, user_id: UUID, count: int, rng: random.Random, *, kind: str):
    statuses = list(status_enum)
    out = []
    for _ in range(count):
        name = title(rng)
        row = {
            "id": uuid4(),
            "user_id": user_id,
            "title": name,
            "sort_title": name.lower(),
            "status": rng.choice(statuses),
            "rating_overall": Decimal(rng.randint(10, 100)) / 10 if rng.random() < 0.6 else None,
            "genres": rng.sample(GENRES, rng.randint(1, 3)),
            "created_at": rng.randint(1_500_000_000, 1_790_000_000),
        }
        out.append(row)
    return out


async def seed(titles: int, noise_users: int, seed_value: int) -> tuple[UUID, list[UUID]]:
    rng = random.Random(seed_value)
    heavy = uuid4()
    others = [uuid4() for _ in range(noise_users)]
    async with SessionLocal() as db:
        for user in [heavy, *others]:
            db.add(User(id=user, username=f"profile_{user.hex[:12]}", email=f"{user.hex[:12]}@profile.test", password_hash="x"))
        await db.commit()
        per_noise = max(10, titles // 100)
        for model, status_enum, season_model, kind in (
            (Movie, MovieStatus, None, "movie"),
            (TVShow, TVShowStatus, TVSeason, "tv"),
            (Anime, AnimeStatus, AnimeSeason, "anime"),
        ):
            batch = rows(model, status_enum, heavy, titles, rng, kind=kind)
            for user in others:
                batch += rows(model, status_enum, user, per_noise, rng, kind=kind)
            for i in range(0, len(batch), 5000):
                await db.execute(insert(model), batch[i : i + 5000])
            if season_model is not None:
                seasons = [
                    {"id": uuid4(), "show_id": r["id"], "season_number": 1, "episode_count": rng.randint(6, 24),
                     "episodes_watched": rng.randint(0, 6)}
                    for r in batch[:titles]
                ]
                for i in range(0, len(seasons), 5000):
                    await db.execute(insert(season_model), seasons[i : i + 5000])
        await db.commit()
        await db.execute(text("ANALYZE"))
        await db.commit()
    return heavy, others


async def cleanup(users: list[UUID]) -> None:
    async with SessionLocal() as db:
        for model in (Movie, TVShow, Anime):
            await db.execute(delete(model).where(model.user_id.in_(users)))
        await db.execute(delete(User).where(User.id.in_(users)))
        await db.commit()


class Recorder:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple | dict | None]] = []
        event.listen(engine.sync_engine, "before_cursor_execute", self._record)

    def _record(self, _conn, _cursor, statement, parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            self.statements.append((statement, parameters))

    def clear(self) -> None:
        self.statements.clear()


def scans(plan: dict, found: list[str]) -> None:
    node = plan.get("Node Type", "")
    if node == "Seq Scan" and plan.get("Plan Rows", 0) > 2000:
        found.append(f"Seq Scan on {plan.get('Relation Name')} (est {plan['Plan Rows']} rows, actual {plan.get('Actual Rows')})")
    for child in plan.get("Plans", []):
        scans(child, found)


async def explain(statement: str, parameters) -> dict:
    async with engine.connect() as conn:
        raw = await conn.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters)
        return raw.scalar()[0]


def request_matrix(model, status_enum, season_model, kind: str):
    year = model.release_date if kind == "movie" else model.first_air_date
    progress = library_progress(model, season_model) if season_model else library_progress(model)
    base = dict(year_column=year)
    return [
        ("list, title order", dict(sort="title"), None, {}),
        ("list, by rank", dict(sort="rank"), None, {}),
        ("list, by progress", dict(sort="progress"), None, dict(progress_column=progress)),
        ("search 'dark'", dict(sort="title"), "dark", {}),
        ("search 'dark', best match", dict(sort="relevance"), "dark", {}),
        ("genre filter", dict(sort="title", genre=["Drama"]), None, {}),
        ("status tab", dict(sort="added", status_bucket="completed"), None, {}),
    ], base


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--titles", type=int, default=5000, help="titles of each kind for the heavy account")
    parser.add_argument("--noise-users", type=int, default=100)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--json", action="store_true", help="print machine-readable results")
    args = parser.parse_args()

    heavy, others = await seed(args.titles, args.noise_users, args.seed)
    recorder = Recorder()
    report = []
    try:
        from src.api.schemas.pagination import LibraryQuery  # noqa: PLC0415

        for model, status_enum, season_model, kind in (
            (Movie, MovieStatus, None, "movie"),
            (TVShow, TVShowStatus, TVSeason, "tv"),
            (Anime, AnimeStatus, AnimeSeason, "anime"),
        ):
            matrix, base = request_matrix(model, status_enum, season_model, kind)
            for label, query_args, search, extra in matrix:
                if query_args.get("sort") == "relevance" and not SUPPORTS_RELEVANCE:
                    continue  # the best-match sort is not in this version
                query = LibraryQuery[status_enum](search=search, limit=100, **query_args)
                clause = title_search([model.title], search)
                options = {"search_columns": (model.title,)} if SUPPORTS_RELEVANCE else {}
                filters = LibraryFilters.from_query(
                    query, status_enum, search_clause=clause, **options, **base, **extra
                )
                async with SessionLocal() as db:
                    recorder.clear()
                    started = time.perf_counter()
                    page = await library_page(db, model, heavy, filters=filters, skip=0, limit=100)
                    elapsed = (time.perf_counter() - started) * 1000
                    statements = list(recorder.statements)
                problems: list[str] = []
                slowest = 0.0
                for statement, parameters in statements:
                    plan = await explain(statement, parameters)
                    slowest = max(slowest, plan.get("Execution Time", 0))
                    scans(plan["Plan"], problems)
                report.append(
                    {
                        "kind": kind,
                        "request": label,
                        "statements": len(statements),
                        "total_ms": round(elapsed, 1),
                        "slowest_statement_ms": round(slowest, 1),
                        "rows": page.total,
                        "large_scans": sorted(set(problems)),
                    }
                )
    finally:
        await cleanup([heavy, *others])
    if args.json:
        print(json.dumps(report, indent=1))
        return
    print(f"{args.titles} titles per kind for one account, {args.noise_users} other accounts\n")
    print(f"{'kind':6} {'request':28} {'stmts':>5} {'total ms':>9} {'slowest ms':>10}  large scans")
    for r in report:
        print(f"{r['kind']:6} {r['request']:28} {r['statements']:>5} {r['total_ms']:>9} {r['slowest_statement_ms']:>10}  "
              f"{'; '.join(r['large_scans']) or '-'}")


if __name__ == "__main__":
    asyncio.run(main())
