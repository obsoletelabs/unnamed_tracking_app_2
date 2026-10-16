"""Administrator account editing: thin routes over the audited admin user feature."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_admin
from src.database.models.user import User
from src.database.session import get_db
from src.features import admin_users
from src.features.user_invitations import (
    list_unaccepted_invitations,
    resend_invitation,
    withdraw_invitation,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SensitiveConfirmation(_Body):
    # Fresh proof of the acting administrator plus the typed target username.
    admin_password: str = Field(min_length=1, max_length=1024)
    confirm_username: str = Field(min_length=1, max_length=100)


class EmailChange(SensitiveConfirmation):
    email: str = Field(min_length=3, max_length=320)


class PasswordChange(SensitiveConfirmation):
    new_password: str = Field(min_length=1, max_length=1024)


class VerificationRequest(_Body):
    destination_id: UUID | None = None


class VerificationRevoke(_Body):
    destination_id: UUID
    confirm_username: str = Field(min_length=1, max_length=100)


def _fail(exc: admin_users.AdminActionError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.post("/users/{user_id}/email")
async def change_user_email(
    user_id: UUID,
    payload: EmailChange,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await admin_users.change_email(
            db,
            admin,
            user_id,
            payload.email,
            admin_password=payload.admin_password,
            confirm_username=payload.confirm_username,
        )
    except admin_users.AdminActionError as exc:
        raise _fail(exc) from exc


@router.post("/users/{user_id}/password")
async def change_user_password(
    user_id: UUID,
    payload: PasswordChange,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await admin_users.set_password(
            db,
            admin,
            user_id,
            payload.new_password,
            admin_password=payload.admin_password,
            confirm_username=payload.confirm_username,
        )
    except admin_users.AdminActionError as exc:
        raise _fail(exc) from exc


@router.post("/users/{user_id}/password-reset-link")
async def send_user_reset_link(
    user_id: UUID,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await admin_users.send_reset_link(
            db, admin, user_id, force=False, admin_password="", confirm_username=""
        )
    except admin_users.AdminActionError as exc:
        raise _fail(exc) from exc


@router.post("/users/{user_id}/force-password-reset")
async def force_user_password_reset(
    user_id: UUID,
    payload: SensitiveConfirmation,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await admin_users.send_reset_link(
            db,
            admin,
            user_id,
            force=True,
            admin_password=payload.admin_password,
            confirm_username=payload.confirm_username,
        )
    except admin_users.AdminActionError as exc:
        raise _fail(exc) from exc


@router.get("/users/{user_id}/email-destinations")
async def user_email_destinations(
    user_id: UUID,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    del admin
    try:
        return await admin_users.list_email_destinations(db, user_id)
    except admin_users.AdminActionError as exc:
        raise _fail(exc) from exc


@router.post("/users/{user_id}/email-verification")
async def request_user_email_verification(
    user_id: UUID,
    payload: VerificationRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await admin_users.send_verification(db, admin, user_id, payload.destination_id)
    except admin_users.AdminActionError as exc:
        raise _fail(exc) from exc


@router.post("/users/{user_id}/email-verification/revoke")
async def revoke_user_email_verification(
    user_id: UUID,
    payload: VerificationRevoke,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await admin_users.revoke_verification(
            db, admin, user_id, payload.destination_id, payload.confirm_username
        )
    except admin_users.AdminActionError as exc:
        raise _fail(exc) from exc


@router.get("/user-audit/{target_id}")
async def user_audit(
    target_id: UUID,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    del admin
    return await admin_users.list_audit(db, target_id)


@router.get("/user-invitations")
async def unaccepted_invitations(
    admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    del admin
    return await list_unaccepted_invitations(db)


async def _invitation_action(db, admin, invitation_id, action, operation):
    actor = admin_users.actor_of(admin)
    try:
        result = await operation()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        await db.rollback()
        admin_users.record_audit(
            db,
            actor,
            action,
            outcome="refused",
            target_id=invitation_id,
            target_label="Invitation",
            detail=str(exc),
        )
        await db.commit()
        raise HTTPException(409, str(exc)) from exc
    admin_users.record_audit(
        db, admin, action, target_id=invitation_id, target_label="Invitation", detail="Completed"
    )
    await db.commit()
    return result


@router.post("/user-invitations/{invitation_id}/resend")
async def resend_user_invitation(
    invitation_id: UUID,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await _invitation_action(
        db,
        admin,
        invitation_id,
        "invitation.resend",
        lambda: resend_invitation(db, admin, invitation_id),
    )
    return {"status": "resent", "id": result["id"], "expires_at": result["expires_at"]}


@router.delete("/user-invitations/{invitation_id}")
async def withdraw_user_invitation(
    invitation_id: UUID,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _invitation_action(
        db,
        admin,
        invitation_id,
        "invitation.withdraw",
        lambda: withdraw_invitation(db, invitation_id),
    )
    return {"status": "withdrawn", "id": str(invitation_id)}
