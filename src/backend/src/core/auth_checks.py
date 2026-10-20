"""One failed-credential budget shared by browser, API, delegated and management credentials."""

from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import HTTPException, Request

from src.core.client_address import request_ip
from src.features.auth_throttle import release_auth_attempts, reserve_credential_attempt

_T = TypeVar("_T")


async def check_credential(request: Request, verify: Callable[[], Awaitable[_T]]) -> _T:
    receipt = await reserve_credential_attempt(request_ip(request))
    try:
        result = await verify()
    except HTTPException as error:
        if error.status_code != 401:
            await release_auth_attempts((receipt,))
        raise
    except BaseException:
        await release_auth_attempts((receipt,))
        raise
    await release_auth_attempts((receipt,))
    return result
