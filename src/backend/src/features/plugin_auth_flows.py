"""Live, explicitly granted installable auth flows; no anonymous plugin execution."""

import time
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.plugin_auth_flow import AuthRecoveryToken, PluginAuthFlowRegistration
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.user import User
from src.features.notification_audit import change_deliveries
from src.plugin_api.auth_contracts import PasswordResetFlowDefinition, UserInviteFlowDefinition
from src.plugin_api.grants import installation_is_executable
from src.plugin_api.runtime_client import PluginRuntimeClient, PluginRuntimeUnavailable

RESET_CAPABILITY = "auth.password_reset"


async def active_reset_flow(db: AsyncSession) -> PluginAuthFlowRegistration | None:
    return await active_auth_flow(db, "password_reset")


async def active_auth_flow(db: AsyncSession, mode: str) -> PluginAuthFlowRegistration | None:
    row = await db.scalar(
        select(PluginAuthFlowRegistration)
        .where(
            PluginAuthFlowRegistration.mode == mode,
            PluginAuthFlowRegistration.revoked_at.is_(None),
        )
        .execution_options(populate_existing=True)
    )
    if row is None or not await _global_grant(db, row.plugin_id, row.installation_id, mode):
        return None
    try:
        installed = await PluginRuntimeClient().plugins()
    except PluginRuntimeUnavailable:
        return None
    matches = [item for item in installed if item.get("plugin_id") == row.plugin_id]
    if (
        len(matches) != 1
        or matches[0].get("installation_id") != str(row.installation_id)
        or not installation_is_executable(matches[0])
    ):
        return None
    return row


async def _global_grant(db, plugin_id, installation_id, mode="password_reset") -> bool:
    # Public recovery serves accounts that have not granted this plugin personally.
    return (
        await db.scalar(
            select(PluginPermissionGrant.id).where(
                PluginPermissionGrant.plugin_id == plugin_id,
                PluginPermissionGrant.installation_id == installation_id,
                PluginPermissionGrant.capability
                == (RESET_CAPABILITY if mode == "password_reset" else "auth.user_invites"),
                PluginPermissionGrant.capability_version == 1,
                PluginPermissionGrant.user_id.is_(None),
                PluginPermissionGrant.device_id.is_(None),
                PluginPermissionGrant.revoked_at.is_(None),
            )
        )
        is not None
    )


async def register_reset_flow(
    db: AsyncSession,
    plugin_id: str,
    installation_id: UUID,
    user_id: UUID,
    payload: dict,
    *,
    restore: bool = False,
) -> dict:
    definition = PasswordResetFlowDefinition.model_validate(payload)
    return await register_auth_flow(
        db, plugin_id, installation_id, user_id, definition, restore=restore
    )


async def register_invite_flow(db, plugin_id, installation_id, user_id, payload, *, restore=False):
    definition = UserInviteFlowDefinition.model_validate(payload)
    return await register_auth_flow(
        db, plugin_id, installation_id, user_id, definition, restore=restore
    )


async def invite_flow_status(db, plugin_id, installation_id, user_id, payload):
    if payload:
        raise ValueError("Invitation status accepts no parameters")
    admin = await db.get(User, user_id)
    if admin is None or not admin.is_active or not admin.is_admin:
        raise PermissionError("Invitation configuration requires an administrator")
    if not await _global_grant(db, plugin_id, installation_id, "user_invite"):
        raise PermissionError("Invitation configuration requires an explicit global grant")
    flow = await active_auth_flow(db, "user_invite")
    registered = bool(
        flow and flow.plugin_id == plugin_id and flow.installation_id == installation_id
    )
    return {
        "registered": registered,
        "token_lifetime_seconds": flow.definition["token_lifetime_seconds"]
        if registered
        else 86400,
    }


async def register_auth_flow(db, plugin_id, installation_id, user_id, definition, *, restore=False):
    admin = await db.scalar(
        select(User.id).where(User.id == user_id, User.is_active.is_(True), User.is_admin.is_(True))
    )
    if admin is None or not await _global_grant(db, plugin_id, installation_id, definition.mode):
        raise PermissionError("Auth flows require an administrator and explicit global grant")
    # Lock first creation as well as replacement, across host workers.
    await db.execute(select(func.pg_advisory_xact_lock(731856417203)))
    # A singleton row also serializes replacement of the public entry point.
    await db.execute(
        select(PluginAuthFlowRegistration)
        .where(PluginAuthFlowRegistration.mode == definition.mode)
        .with_for_update()
    )
    row = await db.get(PluginAuthFlowRegistration, definition.mode)
    values = definition.model_dump(mode="json")
    restorable = row is not None and all(
        (
            row.revoked_at is None,
            row.plugin_id == plugin_id,
            row.installation_id == installation_id,
            row.definition == values,
        )
    )
    if restore and not restorable:
        return {"registered": False, **values}
    if row is not None and row.revoked_at is None and row.plugin_id != plugin_id:
        raise PermissionError("Another active authentication flow is already configured")
    if row is None:
        row = PluginAuthFlowRegistration(
            mode=definition.mode,
            plugin_id=plugin_id,
            installation_id=installation_id,
            definition=values,
        )
        db.add(row)
    elif row.installation_id != installation_id or row.definition != values or row.revoked_at:
        await _retire_credentials(db, row.plugin_id, definition.mode)
        row.plugin_id = plugin_id
        row.installation_id = installation_id
        row.definition = values
        row.revision += 1
        row.revoked_at = None
        row.registered_at = int(time.time())
    # Startup can register while its verified worker is becoming ready. Public
    # request/redemption/handoff remain gated by active_reset_flow's live checks.
    await db.flush()
    await db.commit()
    return {"registered": True, **values}


async def unregister_reset_flow(db, plugin_id, installation_id, user_id, payload) -> dict:
    return await unregister_auth_flow(
        db, plugin_id, installation_id, user_id, payload, mode="password_reset"
    )


async def unregister_invite_flow(db, plugin_id, installation_id, user_id, payload) -> dict:
    return await unregister_auth_flow(
        db, plugin_id, installation_id, user_id, payload, mode="user_invite"
    )


async def unregister_auth_flow(db, plugin_id, installation_id, user_id, payload, *, mode) -> dict:
    if payload:
        raise ValueError("Auth flow withdrawal accepts no parameters")
    admin = await db.get(User, user_id)
    if admin is None or not admin.is_active or not admin.is_admin:
        raise PermissionError("Auth flow configuration requires an administrator")
    await db.execute(select(func.pg_advisory_xact_lock(731856417203)))
    row = await db.scalar(
        select(PluginAuthFlowRegistration)
        .where(
            PluginAuthFlowRegistration.mode == mode,
            PluginAuthFlowRegistration.plugin_id == plugin_id,
            PluginAuthFlowRegistration.installation_id == installation_id,
        )
        .with_for_update()
    )
    if row is None:
        raise PermissionError("This installation does not own the authentication flow")
    await retire_auth_flows(db, plugin_id, installation_id, mode=mode)
    await db.commit()
    return {"registered": False}


async def retire_auth_flows(
    db: AsyncSession,
    plugin_id: str,
    installation_id: UUID | None = None,
    *,
    mode: str | None = None,
) -> None:
    """Withdrawal consumes credentials permanently, even if a grant is restored later."""
    await db.execute(select(func.pg_advisory_xact_lock(731856417203)))
    registrations = update(PluginAuthFlowRegistration).where(
        PluginAuthFlowRegistration.plugin_id == plugin_id
    )
    if installation_id is not None:
        registrations = registrations.where(
            PluginAuthFlowRegistration.installation_id == installation_id
        )
    if mode is not None:
        registrations = registrations.where(PluginAuthFlowRegistration.mode == mode)
    if mode == "user_invite":
        await db.execute(registrations.values(revoked_at=int(time.time())))
        await _retire_credentials(db, plugin_id, mode, installation_id)
        return
    tokens = select(AuthRecoveryToken.notification_id).where(
        AuthRecoveryToken.plugin_id == plugin_id
    )
    credentials = update(AuthRecoveryToken).where(
        AuthRecoveryToken.plugin_id == plugin_id,
        AuthRecoveryToken.used_at.is_(None),
    )
    if installation_id is not None:
        registrations = registrations.where(
            PluginAuthFlowRegistration.installation_id == installation_id
        )
        tokens = tokens.where(AuthRecoveryToken.installation_id == installation_id)
        credentials = credentials.where(AuthRecoveryToken.installation_id == installation_id)
    now = int(time.time())
    await db.execute(registrations.values(revoked_at=now))
    await db.execute(credentials.values(used_at=now, encrypted_token=None))
    await change_deliveries(
        db,
        update(NotificationDelivery)
        .where(
            NotificationDelivery.notification_id.in_(tokens),
            NotificationDelivery.status.in_(("pending", "processing", "retry_wait")),
        )
        .values(
            status="suppressed", last_error="source_revoked", claim_token=None, lease_until=None
        ),
    )
    if mode is None:
        await _retire_credentials(db, plugin_id, "user_invite", installation_id)


async def _retire_credentials(db, plugin_id, mode, installation_id=None):
    if mode == "password_reset":
        await db.execute(
            update(AuthRecoveryToken)
            .where(AuthRecoveryToken.plugin_id == plugin_id, AuthRecoveryToken.used_at.is_(None))
            .values(used_at=int(time.time()), encrypted_token=None)
        )
    else:
        # Feature-local import keeps the delivery/auth-policy dependency graph acyclic.
        # pylint: disable-next=import-outside-toplevel
        from src.features.user_invitations import retire_invitations

        await retire_invitations(db, plugin_id, installation_id)
