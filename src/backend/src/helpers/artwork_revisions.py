"""Content revisions and HTTP validators for served game artwork.

A revision names the bytes that are actually served, never a time. It is the
first 32 hex digits of the SHA-256 of those bytes, so two devices that ask for the
same artwork agree on it, and replacing the artwork (an upload, a re-download, a
refresh, or the generated cover giving way to a stored one) always changes it.

Artwork URLs stay as they were. A request without ``v`` (or with a ``v`` that is
no longer current) is *mutable*: it is cached for a short, finite time and then
revalidated with ``ETag`` / ``If-None-Match``. A request whose ``v`` equals the
stored artwork's current revision is *immutable* and may be kept for a year, because
a replacement is announced with a different revision, hence a different URL.
"""

import asyncio
import hashlib
import re
from collections import OrderedDict
from pathlib import Path
from typing import NamedTuple

from fastapi import Request
from fastapi.responses import FileResponse, Response

from src.helpers.async_storage import finish_storage_operation

REVISION_LENGTH = 32
# how long a browser may reuse artwork fetched without a (current) revision
MUTABLE_MAX_AGE_SECONDS = 60
MUTABLE_CACHE_CONTROL = f"private, max-age={MUTABLE_MAX_AGE_SECONDS}, must-revalidate"
IMMUTABLE_CACHE_CONTROL = "private, max-age=31536000, immutable"

# Keep the source and resized revisions for a full 5k-game library resident.
# Each stored image can occupy two entries (the original and its preview), so
# the former 4k cap churned while a user moved through a large library and then
# revisited earlier artwork.
_MEMO_LIMIT = 16384
_READ_CHUNK = 1024 * 1024
_ETAG_TOKEN = re.compile(r'\s*(?:W/)?"([^"]*)"\s*(?:,|$)')


class _FileSignature(NamedTuple):
    path: str
    mtime_ns: int
    ctime_ns: int
    inode: int
    size: int


_memo: OrderedDict[_FileSignature, str] = OrderedDict()


def bytes_revision(data: bytes) -> str:
    """Revision of bytes held in memory, such as a generated cover."""
    return hashlib.sha256(data).hexdigest()[:REVISION_LENGTH]


def file_revision(path: Path) -> str:
    """Revision of a file's current bytes.

    Hashing is remembered per file identity (path, inode, size and change times), so a
    file that was replaced - even by one of the same size within one clock tick - is
    hashed again, and an unchanged one costs a ``stat``.
    """
    info = path.stat()
    signature = _FileSignature(
        str(path), info.st_mtime_ns, info.st_ctime_ns, info.st_ino, info.st_size
    )
    known = _memo.get(signature)
    if known is not None:
        _memo.move_to_end(signature)
        return known
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK):
            digest.update(chunk)
    revision = digest.hexdigest()[:REVISION_LENGTH]
    _memo[signature] = revision
    while len(_memo) > _MEMO_LIMIT:
        _memo.popitem(last=False)
    return revision


async def stored_file_revision(path: Path) -> str:
    """``file_revision`` off the event loop, finished even if the request is cancelled."""
    return await finish_storage_operation(asyncio.to_thread(file_revision, path))


def etag_for(revision: str) -> str:
    """The strong validator for bytes with this revision."""
    return f'"{revision}"'


def matches_if_none_match(header: str | None, etag: str) -> bool:
    """Whether ``If-None-Match`` lists this validator (weak comparison) or ``*``."""
    if header is None:
        return False
    if header.strip() == "*":
        return True
    wanted = etag.strip('"')
    return any(token == wanted for token in _ETAG_TOKEN.findall(header))


def artwork_headers(etag: str, *, immutable: bool) -> dict[str, str]:
    """Headers shared by 200 and 304 answers for one representation."""
    return {
        "ETag": etag,
        "Cache-Control": IMMUTABLE_CACHE_CONTROL if immutable else MUTABLE_CACHE_CONTROL,
    }


def artwork_response(
    request: Request,
    *,
    revision: str,
    source_revision: str,
    version: str | None,
    media_type: str,
    path: Path | None = None,
    content: bytes | None = None,
    cache_control: str | None = None,
) -> Response:
    """Answer 304 when the client already holds these bytes, otherwise 200.

    ``revision`` identifies the served bytes (the ETag). ``source_revision`` identifies
    the stored artwork they derive from; a ``version`` equal to it makes the URL immutable.
    ``cache_control`` overrides the policy for answers that must always revalidate.
    """
    etag = etag_for(revision)
    headers = artwork_headers(etag, immutable=version is not None and version == source_revision)
    if cache_control is not None:
        headers["Cache-Control"] = cache_control
    if matches_if_none_match(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    if path is not None:
        return FileResponse(path, media_type=media_type, headers=headers)
    return Response(content=content or b"", media_type=media_type, headers=headers)
