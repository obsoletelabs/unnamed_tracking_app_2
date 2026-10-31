"""Remote launch v1: browser queues, an explicitly approved Playnite connection receives."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.delegation import browser_user, private_response
from src.api.schemas.remote_launch import LaunchCreate, LaunchDeviceWrite, LaunchResult
from src.core.auth import app_grant, get_current_user
from src.core.delegation import DelegatedActor, delegated_actor
from src.database.models.user import User
from src.database.session import get_db
from src.features import remote_launch

router = APIRouter(
    prefix="/api/playnite/launch/v1",
    tags=["remote-launch"],
    dependencies=[Depends(private_response)],
)
Db = Annotated[AsyncSession, Depends(get_db)]
Browser = Annotated[User, Depends(browser_user)]
Receiver = Annotated[DelegatedActor, Depends(delegated_actor)]


@router.get("/contract")
@app_grant("games.launch")
async def contract(_user: Annotated[User, Depends(get_current_user)]) -> dict:
    return remote_launch.contract_description()


@router.get("/devices")
async def devices(db: Db, user: Browser) -> list[dict]:
    return await remote_launch.list_devices(db, user.id)


@router.put("/device")
async def configure_device(body: LaunchDeviceWrite, db: Db, actor: Receiver) -> dict:
    return await remote_launch.configure_device(db, actor, body.enabled)


@router.post("/requests")
async def enqueue(body: LaunchCreate, db: Db, user: Browser) -> dict:
    return await remote_launch.enqueue(db, user.id, body)


@router.get("/requests/{request_id}")
async def request_status(request_id: UUID, db: Db, user: Browser) -> dict:
    return await remote_launch.read_request(db, user.id, request_id)


@router.post("/requests/claim")
async def claim(db: Db, actor: Receiver) -> dict:
    return await remote_launch.claim(db, actor)


@router.post("/requests/{request_id}/result")
async def result(request_id: UUID, body: LaunchResult, db: Db, actor: Receiver) -> dict:
    return await remote_launch.report_result(db, actor, request_id, body)
