"""Plugin API 1.1.14: canonical type registration, event publication and version negotiation.

The plugin supplies facts, story identity and presentation hints. The host supplies the recipient
(the authenticated account), the source and installation, trust, retention, scheduling, aggregation
and dispatch, and rechecks the installation's grants at registration, publication, dispatch and
action invocation. Nothing here accepts a destination, credential or executable content.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.features.notification_canonical_registry import active_canonical_registration
from src.features.notification_contract import source_receipt_key
from src.features.notification_sources import (
    accept_source_event,
    check_acceptance_quota,
    source_draft,
    source_interpretation,
    source_namespace_allowed,
    store_source_registration,
)
from src.features.notification_stories import StoryReference
from src.plugin_api.grants import has_capability_grant
from src.plugin_api.notification_plugin_contracts import (
    NEGOTIATED_FEATURES,
    NOTIFICATION_PLUGIN_API_VERSION,
    CanonicalNotificationTypeRegistration,
    ContractNegotiationRequest,
    ContractNegotiationResult,
    check_event_against_registration,
)
from src.plugin_api.notification_story_contracts import (
    NOTIFICATION_CONTRACT_VERSION,
    CanonicalNotificationEvent,
    PluginActionTarget,
    negotiate_notification_contract,
    notification_contract_reason,
)

CANONICAL = "notifications.canonical"
ACTIONS = "notifications.actions"
URGENCY = "notifications.urgency"
HTML_TEMPLATES = "notifications.templates.html"
SENSITIVE = "notifications.sensitive"


def invalid(exc: ValidationError, what: str) -> ValueError:
    """A bounded, input-free explanation of the first validation failure."""
    first = exc.errors(include_url=False, include_input=False, include_context=False)[0]
    location = ".".join(str(part) for part in first["loc"])[:120]
    message = str(first["msg"]).removeprefix("Value error, ")[:200]
    return ValueError(f"{what} is invalid ({location}): {message}")


async def _require(
    db: AsyncSession, plugin_id: str, installation_id: UUID, user_id: UUID, *capabilities: str
) -> None:
    for capability in capabilities:
        if not await has_capability_grant(
            db,
            plugin_id=plugin_id,
            installation_id=installation_id,
            user_id=user_id,
            capability=capability,
        ):
            raise PermissionError(f"This operation requires the {capability} grant")


def registration_grants(registration: CanonicalNotificationTypeRegistration) -> tuple[str, ...]:
    """Grants a registration needs at registration, publication and dispatch."""
    needed = [CANONICAL]
    if registration.required_trust == "SECURE":
        needed.append(SENSITIVE)
    if registration.max_urgency == "critical":
        needed.append(URGENCY)
    if registration.actions:
        needed.append(ACTIONS)
    if registration.needs_html_grant:
        needed.append(HTML_TEMPLATES)
    return tuple(needed)


async def register_canonical_source(
    db: AsyncSession, plugin_id: str, installation_id: UUID, user_id: UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    try:
        registration = CanonicalNotificationTypeRegistration.model_validate(payload)
    except ValidationError as exc:
        raise invalid(exc, "Notification type registration") from exc
    if not source_namespace_allowed(plugin_id, registration.event_type):
        raise ValueError("Notification types must use this plugin's namespace")
    await _require(db, plugin_id, installation_id, user_id, *registration_grants(registration))
    await store_source_registration(
        db,
        plugin_id,
        installation_id,
        registration.event_type,
        registration.model_dump(mode="json"),
    )
    await db.commit()
    return {
        "registered": True,
        "event_type": registration.event_type,
        "contract_version": registration.contract_version,
    }


async def publish_event(
    db: AsyncSession, plugin_id: str, installation_id: UUID, user_id: UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """Accept one canonical event for the authenticated account from one installed source."""
    try:
        event = CanonicalNotificationEvent.model_validate(payload)
    except ValidationError as exc:
        raise invalid(exc, "Notification event") from exc
    now = int(time.time())
    if not now - 30 * 86400 <= event.occurred_at <= now + 300:
        raise ValueError("Notification occurrence is outside the accepted interval")
    registration = await active_canonical_registration(
        db, plugin_id, installation_id, event.event_type, lock=True
    )
    if registration is None:
        raise PermissionError("Canonical notification source is not active for this installation")
    needed = [CANONICAL]
    if registration.required_trust == "SECURE":
        needed.append(SENSITIVE)
    if event.urgency == "critical":
        needed.append(URGENCY)
    if any(isinstance(item.target, PluginActionTarget) for item in event.content.actions):
        needed.append(ACTIONS)
    await _require(db, plugin_id, installation_id, user_id, *needed)
    check_event_against_registration(registration, event)
    story = event.story
    if story is None:  # pragma: no cover - rejected by check_event_against_registration
        raise ValueError("Canonical plugin events belong to a story")
    dedupe_key = source_receipt_key(plugin_id, event.event_type, event.event_id)
    await check_acceptance_quota(db, user_id, plugin_id, dedupe_key, now)
    group_key = (
        f"{event.event_type}:{event.grouping_key}"
        if event.grouping_key and registration.grouping.merge == "digest"
        else None
    )
    draft = source_draft(
        user_id=user_id,
        plugin_id=plugin_id,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        dedupe_key=dedupe_key,
        title=event.content.title,
        body=event.content.summary[:10000],
        group_key=group_key,
        source_installation_id=installation_id,
        story=StoryReference(key=story.key, phase=story.phase, lifecycle=registration.lifecycle),
        requested_urgency=event.urgency,
        content=event.content,
    )
    return await accept_source_event(
        db, draft, source_interpretation(registration, event.event_type, installation_id)
    )


def negotiate(payload: dict[str, Any]) -> dict[str, Any]:
    """Tell a plugin which notification contract version to use; never silently downgrade."""
    try:
        request = ContractNegotiationRequest.model_validate(payload)
    except ValidationError as exc:
        raise invalid(exc, "Contract negotiation request") from exc
    negotiated = negotiate_notification_contract(request.contract_version)
    # Producer documents newer than the host are rejected, so they must send what was negotiated.
    reason = notification_contract_reason(request.contract_version)
    return ContractNegotiationResult(
        host_contract_version=NOTIFICATION_CONTRACT_VERSION,
        plugin_api_version=NOTIFICATION_PLUGIN_API_VERSION,
        negotiated=negotiated,
        reason=reason if negotiated is None else None,
        supported_major=int(NOTIFICATION_CONTRACT_VERSION.split(".", 1)[0]),
        features=NEGOTIATED_FEATURES,
    ).model_dump(mode="json")
