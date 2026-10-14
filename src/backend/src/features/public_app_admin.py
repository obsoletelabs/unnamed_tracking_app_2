"""Administrator-only, installation-scoped management of external app records."""

from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.public_app import PublicAppActor, PublicAppSession, PublicAppSource
from src.database.models.user import User


async def manage_participants(
    db: AsyncSession,
    *,
    plugin_id: str,
    installation_id: UUID,
    user_id: UUID,
    method: str,
    payload: dict[str, Any],
) -> dict:
    admin = await db.scalar(
        select(User).where(User.id == user_id, User.is_active.is_(True), User.is_admin.is_(True))
    )
    if admin is None:
        raise PermissionError("Administrator access is required.")
    actors = select(PublicAppActor).where(
        PublicAppActor.plugin_id == plugin_id,
        PublicAppActor.installation_id == installation_id,
    )
    if method.endswith(".list"):
        return await _participants_page(db, actors, payload)
    return await _forget_participants(db, actors, method, payload)


async def _participants_page(db: AsyncSession, actors, payload: dict) -> dict:
    if set(payload) - {"cursor"}:
        raise ValueError("Only a participant cursor is supported.")
    if payload.get("cursor"):
        actors = actors.where(PublicAppActor.id > UUID(str(payload["cursor"])))
    rows = list(await db.scalars(actors.order_by(PublicAppActor.id).limit(101)))
    sources = list(
        await db.scalars(
            select(PublicAppSource).where(
                PublicAppSource.actor_id.in_([row.id for row in rows[:100]])
            )
        )
    )
    return {
        "participants": [
            {
                "id": str(row.id),
                "subject": row.subject,
                "username": row.account_name,
                "display_name": row.display_name,
                "expires_at": row.expires_at,
                "sources": [
                    {
                        "id": str(source.id),
                        "origin": source.origin,
                        "state": source.state,
                        "remote_user_id": source.remote_user_id,
                    }
                    for source in sources
                    if source.actor_id == row.id
                ],
            }
            for row in rows[:100]
            if row.display_name
        ],
        "next_cursor": str(rows[99].id) if len(rows) > 100 else None,
    }


async def _forget_participants(db: AsyncSession, actors, method: str, payload: dict) -> dict:
    if set(payload) - {"actor_id", "all", "confirmed", "source_id"}:
        raise ValueError("Unknown participant management options.")
    if payload.get("confirmed") is not True:
        raise ValueError("Confirm this participant management action first.")
    all_records = payload.get("all") is True
    if all_records and (
        not method.endswith(".purge") or payload.get("actor_id") or payload.get("source_id")
    ):
        raise ValueError("Choose one participant or an explicit app-wide purge.")
    if not all_records:
        if not isinstance(payload.get("actor_id"), str):
            raise ValueError("Choose a saved participant.")
        actors = actors.where(PublicAppActor.id == UUID(payload["actor_id"]))
    ids = list(await db.scalars(actors.with_only_columns(PublicAppActor.id)))
    if not ids and not all_records and not method.endswith(".purge"):
        raise LookupError("Participant not found in this installation.")
    if method.endswith(".purge"):
        if payload.get("source_id"):
            raise ValueError("Purge applies to a participant, not one connection.")
        await db.execute(delete(PublicAppActor).where(PublicAppActor.id.in_(ids)))
    else:
        query = delete(PublicAppSource).where(PublicAppSource.actor_id.in_(ids))
        if payload.get("source_id"):
            source_id = UUID(str(payload["source_id"]))
            source = await db.scalar(
                select(PublicAppSource.id).where(
                    PublicAppSource.id == source_id, PublicAppSource.actor_id.in_(ids)
                )
            )
            if source is None:
                raise LookupError("Connection not found for this participant.")
            query = query.where(PublicAppSource.id == source_id)
        await db.execute(query)
        # Invalidates in-flight external storage operations and forces fresh sign-in.
        await db.execute(delete(PublicAppSession).where(PublicAppSession.actor_id.in_(ids)))
    await db.commit()
    return {"affected": len(ids)}
