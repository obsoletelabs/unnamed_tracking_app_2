"""Owner-only routing metadata; endpoint addresses and configuration remain host-owned."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.preferences import load_preferences
from src.database.models.notification import Notification
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.notification_receipt import NotificationReceipt
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.database.models.plugin_notification_type import PluginNotificationTypeRegistration
from src.features.notification_browser import bound_subscription, browser_context
from src.features.notification_controller import EVENT_TYPES
from src.features.notification_destinations import resolve_destinations
from src.features.notification_enrollment import endpoint_address
from src.features.notification_policy import (
    INBOX_PROVIDER,
    MEDIA_KINDS,
    PUSH_PROVIDER,
    Trust,
    effective_trust,
    select_projection,
)
from src.features.notification_urls import notification_url
from src.features.smtp_configuration import SMTP_PROVIDER, SmtpConfiguration, smtp_configuration
from src.plugin_api.grants import has_capability_grant
from src.plugin_api.runtime_client import PluginRuntimeUnavailable

_TYPE_LABELS = {
    "episode_aired": ("New episodes", "An episode of a title you follow has aired."),
    "season_started": ("New seasons", "A new season has started airing."),
    "sequel_announced": ("Sequel announcements", "A sequel to a completed title was announced."),
    "movie_released": ("Movie releases", "A followed movie has been released."),
    "game_released": ("Game releases", "Known releases for wishlist, backlog and on-hold games."),
    "game_sale": ("Game sales", "Sale observations from connected integrations."),
    "game_price_hit": ("Price targets", "A live game price crosses your chosen target."),
    "session_anomaly": ("Security alerts", "Unusual sign-in locations; secure destinations only."),
    "plugin_update": ("Plugin updates", "Updates to installed plugins."),
    "plugin": ("Plugin notifications", "Notices from permitted plugin sources."),
}


def _eligible_types(
    destination: NotificationDestination, types: list[dict[str, Any]], *, secure_transport: bool
) -> list[str]:
    # Test disclosure eligibility independently of the user's on/off choices.
    # The real route is still rechecked at creation and immediately before I/O.
    candidate = NotificationDestination(
        id=destination.id,
        user_id=destination.user_id,
        provider_id=destination.provider_id,
        kind=destination.kind,
        channel_context=destination.channel_context,
        privacy=destination.privacy,
        revision=destination.revision,
        verified_revision=destination.verified_revision,
        verification_method=destination.verification_method,
        verification_revoked_at=destination.verification_revoked_at,
        installation_id=destination.installation_id,
        recovery_allowed=destination.recovery_allowed,
        media_consent_revision=destination.media_consent_revision,
        media_consent_at=destination.media_consent_at,
        active=True,
        enabled=True,
    )
    eligible = []
    for item in types:
        if not item["available"] or (item["required_trust"] == "SECURE" and not secure_transport):
            continue
        kind, event_type = item["kind"], item["event_type"]
        notice = Notification(
            user_id=destination.user_id,
            kind=kind,
            event_type=event_type,
            required_trust=int(Trust[item["required_trust"]]),
            purpose=item["purpose"],
            deleted_at=None,
            public_title="Release announcement" if kind in MEDIA_KINDS else None,
            public_body="Released" if kind in MEDIA_KINDS else None,
        )
        if select_projection(notice, candidate, {}):
            eligible.append(event_type)
    return eligible


async def _type_catalog(db: AsyncSession, user_id: UUID) -> list[dict[str, Any]]:
    # Price handlers are integration hooks, not an installed live price source.
    # Retain inactive source controls for recipients who already have history.
    observed_types = set(
        await db.scalars(
            select(NotificationReceipt.event_type)
            .where(NotificationReceipt.user_id == user_id)
            .distinct()
        )
    )
    preferences = await load_preferences(db, user_id)
    remembered_types = set(preferences["notification_types"]) | set(
        preferences["notification_routes"]
    )
    types = [
        {
            "event_type": event_type,
            "preference_key": f"notify_{kind}",
            "label": _TYPE_LABELS[kind][0],
            "description": _TYPE_LABELS[kind][1],
            "required_trust": "SECURE" if kind == "session_anomaly" else "PRIVATE",
            "purpose": "security" if kind == "session_anomaly" else "standard",
            "kind": kind,
            "available": True,
            "visible": kind not in {"game_sale", "game_price_hit"} or event_type in observed_types,
        }
        for kind, event_type in EVENT_TYPES.items()
    ]
    for row in await db.scalars(select(PluginNotificationTypeRegistration)):
        available = row.revoked_at is None and await has_capability_grant(
            db,
            plugin_id=row.plugin_id,
            installation_id=row.installation_id,
            user_id=user_id,
            capability="notifications.emit",
        )
        if available and row.definition["required_trust"] == "SECURE":
            available = await has_capability_grant(
                db,
                plugin_id=row.plugin_id,
                installation_id=row.installation_id,
                user_id=user_id,
                capability="notifications.sensitive",
            )
        if available or row.event_type in observed_types or row.event_type in remembered_types:
            types.append(
                {
                    "event_type": row.event_type,
                    # Namespaced types have independent preferences. The legacy
                    # generic switch remains limited to notifications.send.
                    "preference_key": "",
                    "label": row.definition["label"],
                    "description": row.definition["description"]
                    + ("" if available else " (Source inactive; preferences retained.)"),
                    "required_trust": row.definition["required_trust"],
                    "purpose": row.definition["purpose"],
                    "kind": "plugin",
                    "available": available,
                    "visible": True,
                }
            )
    return types


async def routing_settings(db: AsyncSession, user_id: UUID) -> dict[str, Any]:
    """Include inactive owner choices without exposing endpoint credentials."""
    await resolve_destinations(db, user_id)
    destinations = list(
        await db.scalars(
            select(NotificationDestination)
            .where(NotificationDestination.user_id == user_id)
            .order_by(NotificationDestination.created_at, NotificationDestination.id)
        )
    )
    registrations = {
        row.provider_id: row
        for row in await db.scalars(select(PluginNotificationProviderRegistration))
    }
    settings = {
        row.provider_id: row.enabled
        for row in await db.scalars(
            select(NotificationProviderSetting).where(
                NotificationProviderSetting.user_id == user_id
            )
        )
    }
    smtp = await smtp_configuration(db)
    try:
        browser = await browser_context(db)
    except PluginRuntimeUnavailable:
        browser = None
    provider_ids = (
        {SMTP_PROVIDER, PUSH_PROVIDER}
        | {row.provider_id for row in destinations}
        | {row.provider_id for row in registrations.values() if row.revoked_at is None}
    )
    providers = {
        provider_id: _provider_settings(
            provider_id, registrations.get(provider_id), settings.get(provider_id, False), smtp
        )
        for provider_id in sorted(provider_ids)
    }
    providers[PUSH_PROVIDER].update(
        name="Browser / PWA push",
        available=browser is not None,
        configuration_scope="user",
        destination_kind="browser_push",
        secure_transport=False,
    )
    types = await _type_catalog(db, user_id)
    result = []
    for destination in destinations:
        provider = providers[destination.provider_id]
        registration = registrations.get(destination.provider_id)
        installation_matches = destination.installation_id is None or bool(
            registration and destination.installation_id == registration.installation_id
        )
        if destination.provider_id == PUSH_PROVIDER:
            installation_matches = bool(
                browser
                and await bound_subscription(db, destination, browser, require_enabled=False)
            )
        result.append(
            {
                "id": str(destination.id),
                "provider_id": destination.provider_id,
                "provider_name": provider["name"],
                "kind": destination.kind,
                "context": destination.channel_context,
                "trust": effective_trust(destination).name,
                "active": destination.active,
                "enabled": destination.enabled,
                "available": provider["available"] and installation_matches,
                "reactivation_available": bool(
                    destination.kind == "discord_webhook"
                    and destination.encrypted_configuration
                    and provider["available"]
                    and installation_matches
                ),
                "provider_enabled": provider["enabled"],
                "critical_supported": provider["critical_supported"],
                "critical_description": provider["critical_description"],
                "eligible_types": _eligible_types(
                    destination, types, secure_transport=provider["secure_transport"]
                ),
                "shared_configuration": destination.kind == "legacy_webhook",
                "label": destination.display_name,
                "notification_url": destination.notification_url,
                "masked_address": _masked_address(destination),
                "recovery_allowed": destination.recovery_allowed,
                "revision": destination.revision,
                "media_disclosure_confirmed": destination.media_consent_revision
                == destination.revision
                and destination.media_consent_at is not None,
                "verification_available": destination.provider_id == SMTP_PROVIDER
                and provider["available"]
                and provider["secure_transport"],
            }
        )
    await db.commit()
    return {
        "default_url": await notification_url(db, user_id),
        "providers": list(providers.values()),
        "destinations": result,
        "types": [item for item in types if item["visible"]],
    }


def _masked_address(destination: NotificationDestination) -> str | None:
    if destination.provider_id != SMTP_PROVIDER or not destination.encrypted_configuration:
        return None
    address = endpoint_address(destination)
    local, domain = address.rsplit("@", 1)
    return f"{local[:1]}***@{domain}"


def _provider_settings(
    provider_id: str,
    registration: PluginNotificationProviderRegistration | None,
    enabled: bool,
    smtp: SmtpConfiguration,
) -> dict[str, Any]:
    inbox = provider_id == INBOX_PROVIDER
    email = provider_id == SMTP_PROVIDER
    return {
        "id": provider_id,
        "name": "In-app inbox"
        if inbox
        else ("Email (SMTP)" if email else (registration.name if registration else provider_id)),
        "enabled": True if inbox else enabled,
        "available": inbox
        or (smtp.configured if email else bool(registration and registration.revoked_at is None)),
        "configuration_scope": "internal"
        if inbox
        else (
            "user"
            if registration
            and registration.transport in {"discord_webhook", "discord_bot_dm"}
            else "server"
        ),
        "destination_kind": (
            registration.transport
            if registration and registration.transport in {"discord_webhook", "discord_bot_dm"}
            else None
        ),
        "critical_supported": email,
        "critical_description": (
            "Adds high-priority email headers; your mail client decides how to alert you."
        )
        if email
        else None,
        "transport_warning": (
            "SMTP does not use TLS. Email contents and credentials travel "
            "without transport encryption."
        )
        if email and smtp.configured and smtp.tls_mode == "none"
        else None,
        "secure_transport": (
            smtp.allows_sensitive
            if email
            else not (registration and registration.transport == "discord_bot_dm")
        ),
    }
