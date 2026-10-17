"""Registered plugin event sources use the existing core, grants and lifecycle boundary."""

import hashlib
import json
import time
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import count as sql_count

from src.database.models.notification_receipt import NotificationReceipt
from src.database.models.plugin_notification_type import PluginNotificationTypeRegistration
from src.database.models.user import User
from src.features.notification_contract import source_receipt_key
from src.features.notification_controller import (
    NotificationDraft,
    NotificationInterpretation,
    emit_interpreted,
)
from src.features.notification_policy import Trust
from src.features.notification_source_policy import retire_sources
from src.plugin_api.grants import has_capability_grant
from src.plugin_api.notification_contracts import (
    NotificationEventEmission,
    NotificationTypeRegistration,
)
from src.plugin_api.notification_plugin_contracts import CanonicalNotificationTypeRegistration


async def _sensitive_grant(
    db: AsyncSession,
    plugin_id: str,
    installation_id: UUID,
    user_id: UUID,
    definition: NotificationTypeRegistration,
) -> None:
    if definition.required_trust == "SECURE" and not await has_capability_grant(
        db,
        plugin_id=plugin_id,
        installation_id=installation_id,
        user_id=user_id,
        capability="notifications.sensitive",
    ):
        raise PermissionError("Sensitive notification sources require an explicit high-risk grant")


RESERVED_NAMESPACES = (
    "auth.",
    "security.",
    "destination.",
    "system.",
    "media.",
    "game.",
    "host.",
    "core.",
)


def source_namespace_allowed(plugin_id: str, event_type: str) -> bool:
    return event_type.startswith(f"{plugin_id}.") and not event_type.startswith(RESERVED_NAMESPACES)


async def store_source_registration(
    db: AsyncSession,
    plugin_id: str,
    installation_id: UUID,
    event_type: str,
    definition: dict[str, Any],
) -> None:
    """Create or refresh this installation's registration; changes retire unsent source work."""
    # Serialize first creation and source-count limits with an existing scope lock.
    lock_key = int.from_bytes(
        hashlib.sha256(f"notification-types:{plugin_id}".encode()).digest()[:8], "big", signed=True
    )
    await db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    row = await db.scalar(
        select(PluginNotificationTypeRegistration)
        .where(PluginNotificationTypeRegistration.event_type == event_type)
        .with_for_update()
    )
    if row is not None and row.plugin_id != plugin_id:
        raise PermissionError("Notification type belongs to another plugin")
    if row is None:
        count = await db.scalar(
            select(sql_count())
            .select_from(PluginNotificationTypeRegistration)
            .where(PluginNotificationTypeRegistration.plugin_id == plugin_id)
        )
        if count and count >= 32:
            raise ValueError("Plugins may register at most 32 notification types")
        row = PluginNotificationTypeRegistration(
            plugin_id=plugin_id,
            installation_id=installation_id,
            event_type=event_type,
            definition=definition,
        )
        db.add(row)
    else:
        if row.installation_id != installation_id or row.definition != definition:
            await retire_sources(db, plugin_id, event_type=event_type)
        # The current installation's reviewed source grant authorizes new facts;
        # no destination activation, proof or consent is transferred here.
        row.installation_id = installation_id
        row.definition = definition
        row.revoked_at = None
        row.registered_at = int(time.time())


async def register_source(
    db: AsyncSession, plugin_id: str, installation_id: UUID, user_id: UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    if "contract_version" in payload:
        # Plugin API 1.1.14 canonical declaration; the legacy template form never carries one.
        # pylint: disable-next=import-outside-toplevel,cyclic-import
        from src.features.notification_plugin_events import register_canonical_source

        return await register_canonical_source(db, plugin_id, installation_id, user_id, payload)
    definition = NotificationTypeRegistration.model_validate(payload)
    if not source_namespace_allowed(plugin_id, definition.event_type):
        raise ValueError("Notification types must use this plugin's namespace")
    await _sensitive_grant(db, plugin_id, installation_id, user_id, definition)
    await store_source_registration(
        db, plugin_id, installation_id, definition.event_type, definition.model_dump(mode="json")
    )
    await db.commit()
    return {"registered": True, "event_type": definition.event_type}


async def check_acceptance_quota(
    db: AsyncSession, user_id: UUID, plugin_id: str, dedupe_key: str, now: int
) -> None:
    """Serialize account quotas using acceptance time; exact replay consumes no new quota."""
    owner = await db.scalar(
        select(User.id).where(User.id == user_id, User.is_active.is_(True)).with_for_update()
    )
    if owner is None:
        raise PermissionError("Notification recipient is inactive")
    existing = await db.scalar(
        select(NotificationReceipt.id).where(
            NotificationReceipt.user_id == user_id, NotificationReceipt.dedupe_key == dedupe_key
        )
    )
    if existing is None:
        count = await db.scalar(
            select(sql_count())
            .select_from(NotificationReceipt)
            .where(
                NotificationReceipt.user_id == user_id,
                NotificationReceipt.source == plugin_id,
                NotificationReceipt.accepted_at > now - 3600,
            )
        )
        if count and count >= 100:
            raise ValueError("Notification source quota exceeded")


async def emit_source(
    db: AsyncSession, plugin_id: str, installation_id: UUID, user_id: UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    event = NotificationEventEmission.model_validate(payload)
    now = int(time.time())
    if not now - 30 * 86400 <= event.occurred_at <= now + 300:
        raise ValueError("Notification occurrence is outside the accepted interval")
    row = await db.scalar(
        select(PluginNotificationTypeRegistration)
        .where(
            PluginNotificationTypeRegistration.event_type == event.event_type,
            PluginNotificationTypeRegistration.plugin_id == plugin_id,
            PluginNotificationTypeRegistration.installation_id == installation_id,
            PluginNotificationTypeRegistration.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if row is None:
        raise PermissionError("Notification source is not active for this installation")
    if "contract_version" in row.definition:
        raise ValueError("Canonical notification types are published with notifications.publish")
    definition = NotificationTypeRegistration.model_validate(row.definition)
    await _sensitive_grant(db, plugin_id, installation_id, user_id, definition)
    expected = {"string": str, "integer": int, "boolean": bool}
    if set(event.data) != set(definition.parameters) or any(
        not isinstance(event.data[key], expected[kind])
        or (kind == "integer" and isinstance(event.data[key], bool))
        for key, kind in definition.parameters.items()
    ):
        raise ValueError("Notification parameters do not match the registered type")
    dedupe_key = source_receipt_key(plugin_id, event.event_type, event.dedupe_key)
    await check_acceptance_quota(db, user_id, plugin_id, dedupe_key, now)
    facts = json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    draft = source_draft(
        user_id,
        plugin_id,
        event.event_type,
        event.occurred_at,
        dedupe_key,
        definition.title_template.format_map(event.data),
        definition.body_template.format_map(event.data),
        group_key=f"{event.event_type}:{event.group_key}" if event.group_key else None,
        fingerprint=hashlib.sha256(facts.encode()).hexdigest(),
    )
    return await accept_source_event(
        db,
        draft,
        source_interpretation(definition, event.event_type, installation_id),
    )


def source_interpretation(
    definition: NotificationTypeRegistration | CanonicalNotificationTypeRegistration,
    event_type: str,
    installation_id: UUID,
) -> NotificationInterpretation:
    """Trust, purpose and severity come from the registered declaration, never the event."""
    return NotificationInterpretation(
        event_type,
        Trust[definition.required_trust],
        definition.purpose,
        definition.severity,
        installation_id,
    )


def source_draft(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    user_id: UUID,
    plugin_id: str,
    event_type: str,
    occurred_at: int,
    dedupe_key: str,
    title: str,
    body: str,
    **extra: Any,
) -> NotificationDraft:
    """The producer-neutral part of a registered source's draft; the host adds the rest."""
    return NotificationDraft(
        kind="plugin",
        user_id=user_id,
        entity_type="plugin",
        entity_id=uuid5(NAMESPACE_URL, f"notification-source:{event_type}"),
        occurred_at=occurred_at,
        dedupe_key=dedupe_key,
        title=title,
        body=body,
        source=plugin_id,
        **extra,
    )


async def accept_source_event(
    db: AsyncSession, draft: NotificationDraft, interpretation: NotificationInterpretation
) -> dict[str, Any]:
    """Host acceptance of one registered source event, shared by both producer contracts."""
    notice_id = await emit_interpreted(db, draft, interpretation)
    await db.commit()
    return {
        "created": notice_id is not None,
        "notification_id": str(notice_id) if notice_id else None,
    }


async def unregister_source(
    db: AsyncSession, plugin_id: str, installation_id: UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """Stop this installation's type without erasing its history or personal choices."""
    if set(payload) != {"event_type"} or not isinstance(payload["event_type"], str):
        raise ValueError("An exact notification type is required")
    row = await db.scalar(
        select(PluginNotificationTypeRegistration).where(
            PluginNotificationTypeRegistration.plugin_id == plugin_id,
            PluginNotificationTypeRegistration.installation_id == installation_id,
            PluginNotificationTypeRegistration.event_type == payload["event_type"],
        )
    )
    if row is None:
        raise PermissionError("Notification source belongs to another installation")
    await retire_sources(db, plugin_id, event_type=row.event_type)
    await db.commit()
    return {"unregistered": True, "event_type": row.event_type}
