"""Hard host deadlines, including callbacks that fail to cooperate with cancellation."""

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

Result = TypeVar("Result")
_detached: set[asyncio.Task] = set()


def _finished(task: asyncio.Task) -> None:
    _detached.discard(task)
    if not task.cancelled():
        task.exception()


async def bounded(operation: Awaitable[Result], seconds: float) -> Result:
    """Return at the deadline without awaiting a cancellation-suppressing callback."""
    task = asyncio.ensure_future(operation)
    try:
        done, _ = await asyncio.wait({task}, timeout=seconds)
        if done:
            return task.result()
        raise TimeoutError
    finally:
        if not task.done():
            task.cancel()
            _detached.add(task)
            task.add_done_callback(_finished)
