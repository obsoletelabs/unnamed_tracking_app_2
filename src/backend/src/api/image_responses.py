"""Shared HTTP fallback for images that could not be cached locally."""

from fastapi import status
from fastapi.responses import RedirectResponse


def original_image_response(url: str) -> RedirectResponse:
    """Let the browser load the original image without caching the fallback."""
    return RedirectResponse(
        url,
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Cache-Control": "no-store"},
    )
