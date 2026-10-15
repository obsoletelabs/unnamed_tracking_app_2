"""Keep storage resources alive until an operation finishes, even on cancellation."""

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

_Result = TypeVar("_Result")


async def finish_storage_operation(operation: Awaitable[_Result]) -> _Result:
    """Defer parent cancellation until its worker has released filesystem/session state."""
    task = asyncio.ensure_future(operation)
    cancelled = False
    try:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        return task.result()
    finally:
        if cancelled:
            raise asyncio.CancelledError
