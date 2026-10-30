"""Administrator view of how the Prometheus scrape endpoint is configured."""

import os

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from src.core.auth import get_current_admin
from src.core.config import settings
from src.core.metrics import metrics_enabled
from src.database.models.user import User
from src.features.metrics_viewer import snapshot

router = APIRouter(prefix="/api/settings/metrics", tags=["settings"])


@router.get("")
async def read_metrics_settings(_admin: User = Depends(get_current_admin)) -> JSONResponse:
    """Whether scraping is on and, only for a token the application generated, its value.

    A token supplied through `METRICS_TOKEN` is never echoed back: the operator already has it
    and this response must not become a second place it can leak from."""
    generated = settings.METRICS_ENABLED and settings.METRICS_TOKEN_GENERATED
    return JSONResponse(
        {
            "enabled": metrics_enabled(),
            "token_source": (
                None
                if not settings.METRICS_ENABLED
                else "generated"
                if generated
                else "environment"
            ),
            "token": settings.METRICS_TOKEN if generated else None,
            "path": "/metrics",
            "listen_port": os.getenv("METRICS_LISTEN_PORT", "").strip() or None,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/snapshot")
async def read_metrics_snapshot(_admin: User = Depends(get_current_admin)) -> JSONResponse:
    """A bounded registry view with no scrape token, dependency probes or raw queries."""
    return JSONResponse(await snapshot(), headers={"Cache-Control": "no-store"})
