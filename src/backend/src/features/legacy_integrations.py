"""Opt-in read-only adapter for older instances with unrestricted API keys."""

import asyncio
import time
from uuid import UUID

from fastapi import HTTPException

from src.core.crypto import decrypt_secret
from src.database.models.public_app import PublicAppSource
from src.plugin_api.outbound import legacy_instance_request


async def read(source: PublicAppSource, path: str) -> dict:
    if source.credential is None:
        raise HTTPException(401, "Reconnect the legacy instance first.")
    try:
        return await asyncio.to_thread(
            legacy_instance_request,
            source.origin,
            path,
            credential=decrypt_secret(source.credential),
        )
    except (OSError, ValueError, TimeoutError) as exc:
        raise HTTPException(503, "Cannot read this legacy instance.") from exc


def library_game(value: dict) -> dict:
    """Discard private paths, notes, account secrets and purchase history at the boundary."""
    return {
        "id": str(UUID(value["id"])),
        "title": str(value["title"])[:500],
        "platform": value.get("platform"),
        "tags": value.get("tags", []),
        "features": value.get("features", []),
        "provider_ids": value.get("provider_ids", {}),
        "owned": value.get("status") != "WISHLIST",
        "ownership_provenance": "legacy_instance_library",
        "retrieved_at": int(time.time()),
        "provider_checked_at": None,
        "offers": [],
        "offer_status": "unavailable",
        "assets": {},
    }


async def check(source: PublicAppSource, *, library: bool = False) -> None:
    """Revalidate the account and key without treating its legacy scopes as authorization."""
    result = await read(source, "/api/auth/me")
    source.last_checked_at = int(time.time())
    if result["status"] in {401, 403}:
        source.library, source.state, source.credential = [], "revoked", None
        return
    if result["status"] != 200:
        source.state = "offline"
        return
    try:
        user_id = str(UUID(result["data"]["id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(503, "The legacy instance returned no valid account identity.") from exc
    if source.remote_user_id and user_id != source.remote_user_id:
        source.library, source.state, source.credential = [], "revoked", None
        return
    source.remote_user_id = user_id
    source.scopes = ["profile.read", "games.read"]
    if library:
        games: list[dict] = []
        for offset in range(0, 2001, 200):
            page = await read(source, f"/api/game/list?skip={offset}&limit=200")
            if page["status"] != 200 or not isinstance(page.get("data"), list):
                source.state = "offline"
                return
            if offset == 2000 and page["data"]:
                raise HTTPException(422, "This library exceeds the Activity's 2,000-game limit.")
            try:
                games.extend(library_game(item) for item in page["data"])
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(
                    503, "The legacy instance returned an invalid library."
                ) from exc
            if len(page["data"]) < 200:
                break
        source.library = games
    source.state = "connected"
