"""Provider-ID lookups used to avoid linking one store record to two games."""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game


async def find_provider_id_conflicts(
    session: AsyncSession,
    user_id: UUID,
    excluded_game_id: UUID | None,
    provider_ids: dict[str, str],
) -> list[tuple[str, str, UUID]]:
    """Return identities already attached to another active game for this user."""
    if not provider_ids:
        return []

    matching_identity = or_(
        *(
            Game.provider_ids[namespace].as_string() == identity
            for namespace, identity in provider_ids.items()
        )
    )
    conditions = [
        Game.user_id == user_id,
        Game.deleted_at.is_(None),
        matching_identity,
    ]
    if excluded_game_id is not None:
        conditions.append(Game.id != excluded_game_id)
    rows = await session.execute(
        select(Game.id, Game.provider_ids).where(
            *conditions,
        )
    )
    conflicts = []
    for linked_game_id, existing_ids in rows:
        for namespace, identity in provider_ids.items():
            if existing_ids.get(namespace) == identity:
                conflicts.append((namespace, identity, linked_game_id))
    return conflicts


async def unclaimed_provider_ids(
    session: AsyncSession,
    user_id: UUID,
    game_id: UUID | None,
    existing: dict[str, str],
    incoming: dict[str, str],
) -> dict[str, str]:
    """The offered ids this game may take: new namespaces another game does not already hold."""
    new = {ns: identity for ns, identity in incoming.items() if ns not in existing}
    conflicts = await find_provider_id_conflicts(session, user_id, game_id, new)
    taken = {namespace for namespace, _identity, _id in conflicts}
    return {ns: identity for ns, identity in incoming.items() if ns not in taken}


def merge_provider_ids(
    existing: dict[str, str], incoming: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Add provider identities while retaining saved IDs for every namespace."""
    conflicts = sorted(
        namespace
        for namespace, identity in incoming.items()
        if namespace in existing and existing[namespace] != identity
    )
    return {**incoming, **existing}, conflicts
