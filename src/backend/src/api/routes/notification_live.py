"""Public, token-scoped live story page: the PUBLIC projection only, no scripts, no identifiers."""

from html import escape
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.session import get_db
from src.features.notification_story_pages import (
    LIVE_PATH,
    StoryNotFound,
    public_view,
    resolve_live_link,
)

router = APIRouter(tags=["notifications"])
_DB = Depends(get_db)
_Token = Annotated[str, Path(min_length=1, max_length=512)]
_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex, nofollow",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
}
_STYLE = """
:root { color-scheme: light dark; font-family: system-ui, sans-serif; }
body { margin: 0; min-height: 100vh; display: grid; place-items: start center; padding: 24px 12px;
       box-sizing: border-box; background: light-dark(#f4f5f7, #15191c);
       color: light-dark(#1d252b, #eef1f4); }
main { box-sizing: border-box; width: min(100%, 640px); padding: 28px;
       background: light-dark(#fff, #20262b); border: 1px solid #80808050; border-radius: 16px; }
h1 { font-size: clamp(1.4rem, 5vw, 1.8rem); margin: 0 0 12px; overflow-wrap: anywhere; }
p, dd { line-height: 1.6; overflow-wrap: anywhere; } dl { margin: 20px 0 0; }
dt { font-weight: 650; margin-top: 12px; } dd { margin: 4px 0 0; }
a { color: light-dark(#0b57d0, #9ec1ff); } .stale { font-weight: 650; }
"""


def _page(
    title: str, text: str, *, status: int = 200, details: str = "", footer: str = ""
) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head><body>"
        f'<main aria-labelledby="story-title"><h1 id="story-title">{escape(title)}</h1>'
        f"<p>{escape(text)}</p>{details}{footer}</main></body></html>",
        status_code=status,
        headers=_HEADERS,
    )


@router.get(LIVE_PATH + "/{token}", response_class=HTMLResponse)
async def live_page(token: _Token, db: AsyncSession = _DB) -> HTMLResponse:
    try:
        view = public_view(await resolve_live_link(db, token))
    except StoryNotFound:
        return _page(
            "Link unavailable",
            "This link is invalid, has expired or was revoked.",
            status=404,
        )
    await db.commit()
    stale = (
        '<p class="stale" role="status">This update is no longer current.</p>'
        if view["stale"]
        else ""
    )
    details = (
        f"{stale}<dl><dt>Status</dt><dd>{escape(view['status'])}</dd>"
        f'<dt>Last updated</dt><dd><time datetime="{escape(view["updated_iso"])}">'
        f"{escape(view['updated_iso'].replace('T', ' ').replace('Z', ' UTC'))}</time></dd></dl>"
    )
    footer = (
        f'<p><a href="/notifications/live/{escape(token, quote=True)}">'
        "Sign in to see all details</a></p>"
    )
    return _page(view["title"], view["text"], details=details, footer=footer)


@router.get(LIVE_PATH + "/{token}/state")
async def live_state(token: _Token, db: AsyncSession = _DB) -> Response:
    """The same projection as JSON, for status checks; failures are uniform."""
    try:
        view = public_view(await resolve_live_link(db, token))
    except StoryNotFound:
        return JSONResponse({"detail": "Link unavailable"}, status_code=404, headers=_HEADERS)
    await db.commit()
    return JSONResponse(view, headers=_HEADERS)
