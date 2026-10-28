"""Bounded, read-only artwork previews from an owned selected metadata candidate."""

import asyncio
import io

from PIL import Image

from src.features.metadata.core import CoreMetadataProvider
from src.features.metadata.handler import SearchSession
from src.features.metadata.providers import PluginMetadataProvider
from src.helpers.remote_images import MAX_BYTES, download_image


def _preview(url: str) -> bytes:
    data = download_image(url, max_bytes=MAX_BYTES, cache_kind="artwork_preview")
    with Image.open(io.BytesIO(data)) as source:
        source.thumbnail((800, 800))
        image = source.convert("RGBA")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


async def preview_artwork(session: SearchSession, candidate_id: str, url: str) -> bytes:
    """Never fetch caller-invented URLs or write artwork into a library record."""
    candidate = session.candidates.get(candidate_id)
    if session.cancelled or session.selected != candidate_id or candidate is None:
        raise LookupError("Select this metadata result again before previewing artwork.")
    assets = [asset for asset in candidate.get("assets", []) if asset["url"] == url]
    if not assets:
        raise LookupError("This artwork is no longer offered by the selected result.")
    # A revoked provider/opt-out cannot keep supplying previews from a stale session.
    sources = set().union(
        *(session.asset_providers.get((asset["kind"], url), set()) for asset in assets)
    )
    authorized = False
    for provider in session.providers:
        if provider.id in sources and isinstance(
            provider, (CoreMetadataProvider, PluginMetadataProvider)
        ):
            authorized = authorized or await provider.authorized("media")
    if not authorized:
        raise PermissionError("This artwork provider is no longer available.")
    return await asyncio.to_thread(_preview, url)
