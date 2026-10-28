"""Narrow public adapters for an explicitly installed password-reset contribution."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.metrics import AUTH_ATTEMPTS
from src.core.session_manager import request_ip
from src.database.session import get_db
from src.features.auth_throttle import AuthRateLimited, caller_network
from src.features.password_recovery import complete_password_reset, request_password_reset
from src.features.plugin_auth_flows import active_reset_flow

router = APIRouter(prefix="/api/auth/password-reset", tags=["auth"])


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identifier: str = Field(min_length=1, max_length=320)


class ResetCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    token: str = Field(min_length=32, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    return {"enabled": await active_reset_flow(db) is not None}


@router.post("/request")
async def request_reset(
    payload: ResetRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    decision = await request_password_reset(
        db, payload.identifier, caller_network(request_ip(request))
    )
    if not decision.allowed:
        AUTH_ATTEMPTS.labels("recovery_request", "limited").inc()
        raise AuthRateLimited(decision.retry_after)
    AUTH_ATTEMPTS.labels("recovery_request", "accepted").inc()
    return {
        "message": "If the account has an eligible recovery destination, a reset link will arrive."
    }


@router.post("/complete")
async def complete_reset(
    payload: ResetCompletion, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    try:
        success = await complete_password_reset(db, payload.id, payload.token, payload.password)
    except ValueError as exc:
        AUTH_ATTEMPTS.labels("recovery_complete", "policy_error").inc()
        raise HTTPException(422, str(exc)) from exc
    if not success:
        AUTH_ATTEMPTS.labels("recovery_complete", "invalid").inc()
        raise HTTPException(400, "This reset link is invalid or expired. Request a new link.")
    AUTH_ATTEMPTS.labels("recovery_complete", "success").inc()
    return {"message": "Password changed. Sign in with your new password."}
