"""Transactional, installation-scoped lifecycle metadata; no provider I/O or content."""

import time
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.database.models.notification import Notification
from src.database.models.notification_audit import (
    NotificationLifecycleAudit,
    NotificationLifecycleOutbox,
    NotificationLifecycleStream,
)
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration


async def record_notices(db: AsyncSession, notices: Iterable[Notification], status: str) -> None:
    """Capture source ownership before deletion; the caller still owns the transaction."""
    for notice in notices:
        if notice.source_installation_id is not None:
            db.add(
                NotificationLifecycleOutbox(
                    user_id=notice.user_id,
                    plugin_id=notice.source,
                    installation_id=notice.source_installation_id,
                    notification_id=notice.id,
                    status=status,
                )
            )
    await db.flush()


async def change_notices(db: AsyncSession, statement, status: str) -> int:
    notices = list(await db.scalars(statement.returning(Notification)))
    await record_notices(db, notices, status)
    return len(notices)


async def record_deliveries(db: AsyncSession, delivery_ids: list[UUID]) -> None:
    """Only the owning provider installation sees its delivery; never another destination."""
    if not delivery_ids:
        return
    rows = (
        select(
            Notification.user_id,
            PluginNotificationProviderRegistration.plugin_id,
            NotificationDestination.installation_id,
            Notification.id,
            NotificationDelivery.id,
            NotificationDelivery.status,
        )
        .join(NotificationDelivery, NotificationDelivery.notification_id == Notification.id)
        .join(
            NotificationDestination,
            NotificationDestination.id == NotificationDelivery.destination_id,
        )
        .join(
            PluginNotificationProviderRegistration,
            (PluginNotificationProviderRegistration.provider_id == NotificationDelivery.provider_id)
            & (
                PluginNotificationProviderRegistration.installation_id
                == NotificationDestination.installation_id
            ),
        )
        .where(NotificationDelivery.id.in_(delivery_ids))
    )
    for user_id, plugin_id, installation_id, notice_id, delivery_id, status in await db.execute(
        rows
    ):
        db.add(
            NotificationLifecycleOutbox(
                user_id=user_id,
                plugin_id=plugin_id,
                installation_id=installation_id,
                notification_id=notice_id,
                delivery_id=delivery_id,
                status=status,
            )
        )
    await db.flush()


async def change_deliveries(db: AsyncSession, statement) -> None:
    ids = list(await db.scalars(statement.returning(NotificationDelivery.id)))
    await record_deliveries(db, ids)


async def lifecycle_stream(db: AsyncSession, *, write: bool) -> NotificationLifecycleStream:
    await db.execute(
        pg_insert(NotificationLifecycleStream)
        .values(id=1, published_through=0, expired_through=0)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    stream = await db.scalar(
        select(NotificationLifecycleStream)
        .where(NotificationLifecycleStream.id == 1)
        .with_for_update(read=not write)
        .execution_options(populate_existing=True)
    )
    assert stream is not None
    return stream


async def publish_lifecycle(db: AsyncSession, *, limit: int = 200) -> int:
    """Publish committed records under one short lock; never lock live notice rows."""
    stream = await lifecycle_stream(db, write=True)
    pending = list(
        await db.scalars(
            select(NotificationLifecycleOutbox)
            .order_by(NotificationLifecycleOutbox.position)
            .limit(limit)
            .with_for_update()
        )
    )
    now = int(time.time())
    for item in pending:
        stream.published_through += 1
        db.add(
            NotificationLifecycleAudit(
                sequence=stream.published_through,
                id=item.id,
                user_id=item.user_id,
                plugin_id=item.plugin_id,
                installation_id=item.installation_id,
                notification_id=item.notification_id,
                delivery_id=item.delivery_id,
                status=item.status,
                occurred_at=item.occurred_at,
                published_at=now,
            )
        )
        await db.delete(item)
    await db.flush()
    # Bound cleanup too. The floor advances only past records actually expired.
    oldest = await db.execute(
        select(NotificationLifecycleAudit.sequence, NotificationLifecycleAudit.published_at)
        .order_by(NotificationLifecycleAudit.sequence)
        .limit(limit)
    )
    expired = []
    for sequence, published_at in oldest:
        if published_at >= now - settings.NOTIFICATION_AUDIT_RETENTION_DAYS * 86400:
            break
        expired.append(sequence)
    if expired:
        await db.execute(
            delete(NotificationLifecycleAudit).where(
                NotificationLifecycleAudit.sequence.in_(expired)
            )
        )
        stream.expired_through = max(stream.expired_through, *expired)
    await db.commit()
    return len(pending)
