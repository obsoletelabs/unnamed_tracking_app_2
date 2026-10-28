"""Compare SQL/pool instrumentation under the same bounded PostgreSQL contention.

Run from src/backend with PYTHONPATH=. and TEST_DATABASE_URL set. Each engine has
two connections; eight concurrent tasks hold a connection briefly after each
SELECT. This is an operator benchmark, not a CI timing assertion.
"""

import asyncio
import os
import statistics
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.database_metrics import ObservedAsyncPool, install_database_metrics


async def sample(url: str, *, observed: bool) -> float:
    kwargs = {"pool_size": 2, "max_overflow": 0}
    if observed:
        kwargs["poolclass"] = ObservedAsyncPool
    engine = create_async_engine(url, **kwargs)
    if observed:
        install_database_metrics(engine.sync_engine)

    async def worker() -> None:
        for _ in range(20):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                await asyncio.sleep(0.002)

    try:
        start = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(8)))
        return time.perf_counter() - start
    finally:
        await engine.dispose()


async def main() -> None:
    url = os.environ["TEST_DATABASE_URL"]
    plain, metered = [], []
    for _ in range(3):
        plain.append(await sample(url, observed=False))
        metered.append(await sample(url, observed=True))
    baseline, instrumented = statistics.median(plain), statistics.median(metered)
    print(f"baseline median: {baseline:.4f}s")
    print(f"instrumented median: {instrumented:.4f}s")
    print(f"relative change: {(instrumented / baseline - 1) * 100:+.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
