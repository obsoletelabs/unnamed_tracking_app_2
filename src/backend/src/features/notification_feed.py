"""Bounded replay of a current user's current installation lifecycle metadata."""

import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.crypto import decrypt_secret, encrypt_secret
from src.database.models.notification_audit import NotificationLifecycleAudit
from src.features.notification_audit import lifecycle_stream
from src.plugin_api.notification_contracts import NotificationLifecycleQuery


def _cursor(user_id: UUID, installation_id: UUID, sequence: int) -> str:
    return encrypt_secret(
        json.dumps(["notification-lifecycle-v1", str(user_id), str(installation_id), sequence])
    )


def _sequence(cursor: str, user_id: UUID, installation_id: UUID) -> int:
    try:
        values = json.loads(decrypt_secret(cursor))
        if (
            not isinstance(values, list)
            or len(values) != 4
            or values[:3] != ["notification-lifecycle-v1", str(user_id), str(installation_id)]
        ):
            raise ValueError("Invalid notification lifecycle cursor")
        sequence = values[3]
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise ValueError("Invalid notification lifecycle cursor")
        return sequence
    except (RuntimeError, ValueError, TypeError) as exc:
        raise ValueError("Invalid notification lifecycle cursor") from exc


async def poll_lifecycle(
    db: AsyncSession, plugin_id: str, installation_id: UUID, user_id: UUID, payload: dict
) -> dict:
    query = NotificationLifecycleQuery.model_validate(payload)
    # Hold a brief shared lock until the gateway transaction ends, so pruning
    # cannot race the retention floor/page. No external calls occur here.
    stream = await lifecycle_stream(db, write=False)
    after = (
        _sequence(query.cursor, user_id, installation_id)
        if query.cursor
        else stream.expired_through
    )
    if after > stream.published_through:
        raise ValueError("Invalid notification lifecycle cursor")
    if after < stream.expired_through:
        return {
            "events": [],
            "cursor": _cursor(user_id, installation_id, stream.expired_through),
            "has_more": False,
            "resync_required": True,
        }
    rows = list(
        await db.scalars(
            select(NotificationLifecycleAudit)
            .where(
                NotificationLifecycleAudit.user_id == user_id,
                NotificationLifecycleAudit.plugin_id == plugin_id,
                NotificationLifecycleAudit.installation_id == installation_id,
                NotificationLifecycleAudit.sequence > after,
                NotificationLifecycleAudit.sequence <= stream.published_through,
            )
            .order_by(NotificationLifecycleAudit.sequence)
            .limit(query.limit + 1)
        )
    )
    has_more = len(rows) > query.limit
    rows = rows[: query.limit]
    through = rows[-1].sequence if has_more else stream.published_through
    return {
        "events": [
            {
                "id": str(row.id),
                "notification_id": str(row.notification_id),
                "delivery_id": str(row.delivery_id) if row.delivery_id else None,
                "status": row.status,
                "occurred_at": datetime.fromtimestamp(row.occurred_at, timezone.utc).isoformat(),
            }
            for row in rows
        ],
        "cursor": _cursor(user_id, installation_id, through),
        "has_more": has_more,
        "resync_required": False,
    }
