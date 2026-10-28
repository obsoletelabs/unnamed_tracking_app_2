"""Narrow public account acceptance adapter; no anonymous plugin execution."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.metrics import AUTH_ATTEMPTS
from src.database.session import get_db
from src.features.plugin_auth_flows import active_auth_flow
from src.features.user_invitations import accept_invitation

router = APIRouter(prefix="/api/auth/user-invites", tags=["auth"])


class InviteAcceptance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    token: str = Field(min_length=32, max_length=128)
    username: str
    password: str = Field(min_length=1, max_length=1024)


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    return {"enabled": await active_auth_flow(db, "user_invite") is not None}


@router.post("/accept")
async def accept(payload: InviteAcceptance, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    try:
        success = await accept_invitation(
            db, payload.id, payload.token, payload.username, payload.password
        )
    except ValueError as exc:
        AUTH_ATTEMPTS.labels("invitation_accept", "policy_error").inc()
        raise HTTPException(422, str(exc)) from exc
    if not success:
        AUTH_ATTEMPTS.labels("invitation_accept", "invalid").inc()
        raise HTTPException(
            400, "This invitation is invalid or expired. Contact the administrator."
        )
    AUTH_ATTEMPTS.labels("invitation_accept", "success").inc()
    return {"message": "Account created. Sign in with your username and password."}
