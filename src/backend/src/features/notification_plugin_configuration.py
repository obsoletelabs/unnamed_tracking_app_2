"""Explicit plugin-owned configuration; core stores routing authority, never secret values."""

import json
import math
import time
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.database.models.user import User
from src.features.notification_destinations import retire_provider_destinations
from src.features.notification_enrollment import (
    EnrollmentError,
    enforce_destination_limit,
    invalidate_endpoint,
)
from src.features.notification_providers.plugin import PluginNotificationProvider
from src.plugin_api.notification_contracts import NotificationProviderDefinition
from src.plugin_api.runtime_client import (
    PluginRuntimeClient,
    PluginRuntimeRequestError,
    PluginRuntimeUnavailable,
)
from src.plugin_api.ui_contracts import UiField, UiFieldType


@dataclass(frozen=True)
class _ConfigurationAuthority:
    provider_id: str
    installation_id: UUID
    definition: dict
    delivery_action: str
    server_revision: int


def _validate_bounds(field: UiField, value) -> None:
    bounds = field.validation
    if bounds is None:
        return
    if isinstance(value, str):
        too_short = bounds.min_length is not None and len(value) < bounds.min_length
        too_long = bounds.max_length is not None and len(value) > bounds.max_length
        if too_short or too_long:
            raise EnrollmentError("Provider configuration text does not meet its bounds")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        too_small = bounds.minimum is not None and value < bounds.minimum
        too_large = bounds.maximum is not None and value > bounds.maximum
        if too_small or too_large:
            raise EnrollmentError("Provider configuration number does not meet its bounds")


def validate_values(fields: tuple[UiField, ...], values: dict, *, partial: bool) -> dict:
    """Apply the existing field types/options plus bounded scalar configuration constraints."""
    definitions = {field.id: field for field in fields}
    if set(values) - definitions.keys() or len(json.dumps(values).encode()) > 32768:
        raise EnrollmentError("Provider configuration contains unknown or oversized fields")
    result = {}
    for field in fields:
        if field.id not in values:
            if not partial and field.required and field.default is None:
                raise EnrollmentError("Complete the required provider configuration fields")
            if not partial and field.default is not None:
                result[field.id] = field.default
            continue
        value = values[field.id]
        if field.type == UiFieldType.MULTISELECT and isinstance(value, list):
            value = tuple(value)
        try:
            field.validate_value(value)
        except ValueError as exc:
            raise EnrollmentError(
                "Provider configuration does not match its declared fields"
            ) from exc
        if isinstance(value, str) and (len(value) > 4096 or (field.required and not value.strip())):
            raise EnrollmentError("Provider configuration text is empty or exceeds its bounds")
        if isinstance(value, tuple) and len(value) > 64:
            raise EnrollmentError("Provider configuration choices exceed their bounds")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (abs(value) > 2**53 - 1 or not math.isfinite(value))
        ):
            raise EnrollmentError("Provider configuration requires finite numbers")
        _validate_bounds(field, value)
        result[field.id] = value
    return result


async def _registration(db: AsyncSession, user_id: UUID, provider_id: str):
    await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    row = await db.scalar(
        select(PluginNotificationProviderRegistration)
        .where(
            PluginNotificationProviderRegistration.provider_id == provider_id,
            PluginNotificationProviderRegistration.transport == "plugin",
            PluginNotificationProviderRegistration.revoked_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or not await PluginNotificationProvider(row).is_authorized(db, user_id):
        raise EnrollmentError("This plugin provider is unavailable or not permitted", 403)
    definition = NotificationProviderDefinition.model_validate(row.definition)
    authority = _ConfigurationAuthority(
        row.provider_id,
        row.installation_id,
        definition.model_dump(mode="json"),
        row.action_id,
        row.server_revision,
    )
    return row, definition, authority


async def _owned(db: AsyncSession, user_id: UUID, destination_id: UUID):
    row = await db.scalar(
        select(NotificationDestination)
        .filter_by(id=destination_id, user_id=user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.configuration_ref != str(row.id) or row.endpoint_key != str(row.id):
        raise EnrollmentError("Plugin notification destination not found", 404)
    return row


async def _invoke(row, authority, definition, *, user_id, values, context):
    action = (
        definition.retire_action
        if context["operation"] == "retire"
        else definition.configure_action
    )
    try:
        result = await PluginRuntimeClient().notification_provider_action(
            row.plugin_id,
            action,
            values,
            user_id=str(user_id),
            installation_id=str(authority.installation_id),
            context=context,
        )
    except (PluginRuntimeRequestError, PluginRuntimeUnavailable) as exc:
        raise EnrollmentError(
            "Plugin configuration is unavailable; the destination remains inactive", 503
        ) from exc
    expected = "retired" if context["operation"] == "retire" else "configured"
    if not isinstance(result, dict) or result.get(expected) is not True:
        raise EnrollmentError("Plugin configuration failed; the destination remains inactive", 400)


async def _current_registration(db, user_id, authority):
    row, definition, current = await _registration(db, user_id, authority.provider_id)
    if current != authority:
        raise EnrollmentError(
            "Provider configuration changed; configure this destination again", 409
        )
    return row, definition


def _destination_context(endpoint, operation="configure"):
    return {
        "operation": operation,
        "scope": "destination",
        "provider_id": endpoint.provider_id,
        "destination": {
            "id": str(endpoint.id),
            "revision": endpoint.revision,
            "kind": endpoint.kind,
        },
    }


async def _prepare_endpoint(
    db, user_id, provider_id, definition, authority, *, kind, values, destination_id
):
    choice = next((item for item in definition.destinations if item.kind == kind), None)
    if choice is None:
        raise EnrollmentError("This destination kind is not supported")
    configuration = validate_values(choice.fields, values, partial=destination_id is not None)
    if destination_id is None:
        await enforce_destination_limit(
            db,
            user_id,
            provider_id,
            include_inactive=True,
            maximum=20 if definition.features.multiple_destinations else 1,
        )
        identity = uuid4()
        endpoint = NotificationDestination(
            id=identity,
            user_id=user_id,
            provider_id=provider_id,
            endpoint_key=str(identity),
            configuration_ref=str(identity),
            kind=kind,
            channel_context="external",
            privacy=1 if choice.privacy == "PRIVATE" else 0,
            installation_id=authority.installation_id,
            active=False,
            enabled=False,
        )
        db.add(endpoint)
        await db.flush()
    else:
        endpoint = await _owned(db, user_id, destination_id)
        if (
            endpoint.provider_id != provider_id
            or endpoint.kind != kind
            or endpoint.installation_id != authority.installation_id
        ):
            raise EnrollmentError("Enroll a new destination after reinstalling this provider", 409)
        await invalidate_endpoint(db, endpoint)
        endpoint.active = endpoint.enabled = False
    return endpoint, configuration


async def _ensure_provider_opt_in(db, user_id, provider_id):
    setting = await db.scalar(
        select(NotificationProviderSetting).filter_by(user_id=user_id, provider_id=provider_id)
    )
    if setting is None:
        db.add(NotificationProviderSetting(user_id=user_id, provider_id=provider_id, enabled=True))


async def configure_destination(
    db: AsyncSession,
    user_id: UUID,
    provider_id: str,
    kind: str,
    values: dict,
    *,
    label: str,
    destination_id: UUID | None = None,
    enabled: bool = True,
    share_followed_media: bool = False,
) -> UUID:
    """Commit invalidation first; a callback can activate only its unchanged authority."""
    row, definition, authority = await _registration(db, user_id, provider_id)
    endpoint, configuration = await _prepare_endpoint(
        db,
        user_id,
        provider_id,
        definition,
        authority,
        kind=kind,
        values=values,
        destination_id=destination_id,
    )
    if share_followed_media and endpoint.privacy != 0:
        raise EnrollmentError("Media disclosure consent applies only to PUBLIC destinations")
    endpoint.display_name = label.strip() or next(
        item.label for item in definition.destinations if item.kind == kind
    )
    await _ensure_provider_opt_in(db, user_id, provider_id)
    expected = (endpoint.id, endpoint.revision)
    await db.commit()
    await _invoke(
        row,
        authority,
        definition,
        user_id=user_id,
        values=configuration,
        context=_destination_context(endpoint),
    )
    await _current_registration(db, user_id, authority)
    endpoint = await _owned(db, user_id, expected[0])
    if endpoint.revision != expected[1] or endpoint.installation_id != authority.installation_id:
        raise EnrollmentError(
            "Destination configuration changed; retry the current configuration", 409
        )
    endpoint.active, endpoint.enabled = True, enabled
    if share_followed_media:
        endpoint.media_consent_revision = expected[1]
        endpoint.media_consent_at = int(time.time())
    await db.commit()
    return expected[0]


async def remove_destination(db: AsyncSession, user_id: UUID, destination_id: UUID) -> bool:
    """Owner removal works even if the provider is gone; secret cleanup is best effort."""
    await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    endpoint = await _owned(db, user_id, destination_id)
    row = await db.scalar(
        select(PluginNotificationProviderRegistration).where(
            PluginNotificationProviderRegistration.provider_id == endpoint.provider_id
        )
    )
    await invalidate_endpoint(db, endpoint)
    endpoint.active = endpoint.enabled = False
    endpoint.endpoint_key = f"retired:{endpoint.id}"
    context = _destination_context(endpoint, "retire")
    installed = endpoint.installation_id
    await db.commit()
    if (
        row is None
        or row.transport != "plugin"
        or row.revoked_at is not None
        or row.installation_id != installed
    ):
        return False
    try:
        definition = NotificationProviderDefinition.model_validate(row.definition)
        authority = _ConfigurationAuthority(
            row.provider_id,
            row.installation_id,
            definition.model_dump(mode="json"),
            row.action_id,
            row.server_revision,
        )
        if not await PluginNotificationProvider(row).is_authorized(db, user_id):
            return False
        await _invoke(row, authority, definition, user_id=user_id, values={}, context=context)
    except (EnrollmentError, PluginRuntimeUnavailable, PluginRuntimeRequestError):
        return False
    return True


async def _require_admin(db: AsyncSession, user_id: UUID) -> None:
    admin = await db.scalar(
        select(User.id).where(User.id == user_id, User.is_admin.is_(True), User.is_active.is_(True))
    )
    if admin is None:
        raise EnrollmentError("Administrator access is required", 403)


async def configure_server(db: AsyncSession, user_id: UUID, provider_id: str, values: dict) -> None:
    """Called only by the authenticated administrator route; invalidate before secret changes."""
    await _require_admin(db, user_id)
    row, definition, _authority = await _registration(db, user_id, provider_id)
    if not definition.server_fields:
        raise EnrollmentError("This provider has no server configuration")
    configuration = validate_values(definition.server_fields, values, partial=row.server_configured)
    row.server_configured = False
    row.server_revision += 1
    await retire_provider_destinations(db, provider_id)
    authority = _ConfigurationAuthority(
        row.provider_id,
        row.installation_id,
        definition.model_dump(mode="json"),
        row.action_id,
        row.server_revision,
    )
    context = {
        "operation": "configure",
        "scope": "server",
        "provider_id": provider_id,
        "revision": authority.server_revision,
    }
    await db.commit()
    await _invoke(
        row, authority, definition, user_id=user_id, values=configuration, context=context
    )
    row, _definition = await _current_registration(db, user_id, authority)
    await _require_admin(db, user_id)
    row.server_configured = True
    await db.commit()
