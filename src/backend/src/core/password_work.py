"""Bound password CPU/memory work without blocking the event loop or queuing excess jobs."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
from typing import Callable, ParamSpec, TypeVar

from src.core.config import settings

_P = ParamSpec("_P")
_R = TypeVar("_R")
_slots = BoundedSemaphore(settings.AUTH_PASSWORD_WORKERS)
_executor = ThreadPoolExecutor(
    max_workers=settings.AUTH_PASSWORD_WORKERS, thread_name_prefix="auth-password"
)


class PasswordWorkBusy(RuntimeError):
    """A retry is preferable to holding an unbounded queue of password work."""


async def run_password_job(job: Callable[_P, _R], *args: _P.args, **kwargs: _P.kwargs) -> _R:
    slots = _slots
    # A semaphore context manager blocks; admission here must return a bounded retry.
    if not slots.acquire(blocking=False):  # pylint: disable=consider-using-with
        raise PasswordWorkBusy
    try:
        future = _executor.submit(job, *args, **kwargs)
    except RuntimeError:
        slots.release()
        raise PasswordWorkBusy from None
    # Cancellation of an HTTP task cannot free a slot while its thread still runs.
    # A queued future cancelled before execution releases its slot through the same callback.
    future.add_done_callback(lambda _future: slots.release())
    return await asyncio.wrap_future(future)
