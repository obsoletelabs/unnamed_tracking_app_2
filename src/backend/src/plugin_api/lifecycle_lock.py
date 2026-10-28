"""Serialize host package transactions and startup recovery within an event loop."""

import asyncio
import time
from functools import wraps
from typing import Awaitable, Callable, ParamSpec, TypeVar
from weakref import WeakKeyDictionary

from src.core.metrics import PLUGIN_LIFECYCLE_OPERATIONS, PLUGIN_LIFECYCLE_QUEUE_WAIT

_locks: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = WeakKeyDictionary()
_P = ParamSpec("_P")
_R = TypeVar("_R")


def serialized_lifecycle(function: Callable[_P, Awaitable[_R]]) -> Callable[_P, Awaitable[_R]]:
    """Recovery cannot mistake an in-flight installation for an abandoned one."""

    @wraps(function)
    async def serialized(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        loop = asyncio.get_running_loop()
        lock = _locks.setdefault(loop, asyncio.Lock())
        started = time.perf_counter()
        async with lock:
            PLUGIN_LIFECYCLE_QUEUE_WAIT.observe(time.perf_counter() - started)
            if function.__qualname__ != "PluginInstaller.install":
                return await function(*args, **kwargs)
            operation = "update" if kwargs.get("update_plugin_id") is not None else "install"
            try:
                result = await function(*args, **kwargs)
            except Exception:
                PLUGIN_LIFECYCLE_OPERATIONS.labels(operation, "failed").inc()
                raise
            outcome = result.get("status", "completed") if isinstance(result, dict) else "completed"
            if outcome not in {"completed", "awaiting_permissions", "rolled_back", "denied"}:
                outcome = "completed"
            PLUGIN_LIFECYCLE_OPERATIONS.labels(operation, outcome).inc()
            return result

    return serialized
