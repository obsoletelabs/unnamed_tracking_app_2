"""Revocable user delegation is accepted only by explicitly scoped public v1 routes."""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth_checks import check_credential
from src.database.models.delegation import DelegatedGrant
from src.database.models.user import User
from src.database.session import get_db
from src.features.delegation import authenticate_grant

_DB = Depends(get_db)


@dataclass(frozen=True)
class DelegatedActor:
    grant: DelegatedGrant
    user: User

    def require(self, scope: str) -> None:
        if scope not in self.grant.scopes:
            raise HTTPException(
                403, "This integration has not been granted the required capability."
            )


async def delegated_actor(request: Request, db: AsyncSession = _DB) -> DelegatedActor:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "An integration credential is required.")
    grant, user = await check_credential(
        request, lambda: authenticate_grant(db, authorization[7:].strip())
    )
    return DelegatedActor(grant=grant, user=user)
