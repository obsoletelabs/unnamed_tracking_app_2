"""Shared location and stable version of a user's optional profile picture."""

from pathlib import Path
from stat import S_ISREG
from uuid import UUID

from starlette.concurrency import run_in_threadpool

_USER_DATA_ROOT = Path("/data/user")


def profile_picture_path(user_id: UUID) -> Path:
    """Keep the existing on-disk profile location."""
    return _USER_DATA_ROOT / str(user_id) / "profile.png"


async def profile_picture_version(user_id: UUID) -> str | None:
    """Optional artwork must not prevent authentication if storage is unavailable."""
    try:
        info = await run_in_threadpool(profile_picture_path(user_id).stat)
    except OSError:
        return None
    if not S_ISREG(info.st_mode):
        return None
    # Atomic replacements can share size/mtime on coarse filesystem clocks.
    # Include file identity and change time; keep all values opaque to JavaScript.
    return f"{info.st_mtime_ns}-{info.st_ctime_ns}-{info.st_ino}-{info.st_size}"
