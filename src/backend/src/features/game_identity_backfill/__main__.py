"""Operator command: `python -m src.features.game_identity_backfill`.

Safe to run while the application is up, to repeat, and to run again after a failure
or an interruption: it resumes. Prints a JSON report (counts and error codes only)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from uuid import UUID

from src.database.session import SessionLocal, engine
from src.features.game_identity_backfill import runner


async def _main(args: argparse.Namespace) -> int:
    if args.dry_run:
        async with SessionLocal() as db:
            users = [args.user] if args.user else None
            if users is None:
                from sqlalchemy import select  # pylint: disable=import-outside-toplevel

                from src.database.models.user import User  # pylint: disable=import-outside-toplevel

                users = list(await db.scalars(select(User.id)))
            print(json.dumps({str(user): await runner.plan(db, user) for user in users}, indent=2))
        return 0
    if args.user:
        reports = {
            str(args.user): await runner.run_user(
                args.user, batch_size=args.batch_size, reset=args.reset
            )
        }
    else:
        reports = await runner.run_all(batch_size=args.batch_size, reset=args.reset)
    print(json.dumps(reports, indent=2, default=str))
    return int(any(report.get("status") != "completed" for report in reports.values()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", type=UUID, help="Only this user (default: every user)")
    parser.add_argument("--batch-size", type=int, default=runner.DEFAULT_BATCH)
    parser.add_argument(
        "--reset", action="store_true", help="Restart a failed run from its first stage"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print coverage numbers, write nothing"
    )
    args = parser.parse_args()

    async def go() -> int:
        try:
            return await _main(args)
        finally:
            await engine.dispose()

    return asyncio.run(go())


if __name__ == "__main__":
    sys.exit(main())
