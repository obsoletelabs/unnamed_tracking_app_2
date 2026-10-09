"""Explicit confirmation prevents mail-link scanners from consuming possession proof."""

from html import escape
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.session import get_db
from src.features.notification_verification_links import (
    VERIFICATION_PATH,
    VerificationLinkError,
    confirm_verification_link,
    verification_token,
)

router = APIRouter(tags=["notifications"])
_DB = Depends(get_db)


def _page(title: str, text: str, *, token: str = "", status: int = 200) -> HTMLResponse:
    form = (
        f'<form method="post" action="{VERIFICATION_PATH}">'
        f'<input type="hidden" name="token" value="{escape(token, quote=True)}">'
        '<button name="confirm" value="verify">Verify this email</button></form>'
        if token
        else ""
    )
    return HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>"
        ":root{color-scheme:light dark;font-family:system-ui,sans-serif}"
        "body{margin:0;min-height:100vh;display:grid;place-items:center;"
        "background:light-dark(#f4f5f7,#15191c);color:light-dark(#1d252b,#eef1f4)}"
        "main{box-sizing:border-box;width:min(94%,540px);padding:32px;"
        "background:light-dark(#fff,#20262b);border:1px solid #80808050;border-radius:16px}"
        "p{line-height:1.65}button{border:0;border-radius:8px;padding:12px 18px;"
        "background:#f1ad55;color:#201a12;font:inherit;font-weight:650;cursor:pointer}"
        "</style></head><body><main>"
        f"<h1>{escape(title)}</h1><p>{escape(text)}</p>{form}"
        '<p><a href="/settings?section=notifications">Manage account notifications</a></p>'
        "</main></body></html>",
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Robots-Tag": "noindex, nofollow",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        },
    )


@router.get(VERIFICATION_PATH, response_class=HTMLResponse)
async def confirmation(
    token: Annotated[str, Query(min_length=1, max_length=2048)], db: AsyncSession = _DB
) -> HTMLResponse:
    try:
        await verification_token(db, token)
    except VerificationLinkError as exc:
        return _page("Link unavailable", str(exc), status=400)
    return _page(
        "Verify this email?",
        "Confirm that you have access to this notification address. "
        "Opening this page has not verified it or signed you into an account.",
        token=token,
    )


@router.post(VERIFICATION_PATH, response_class=HTMLResponse)
async def complete_verification(
    request: Request,
    token: Annotated[str, Form(min_length=1, max_length=2048)],
    confirm: Annotated[str, Form(max_length=32)] = "",
    db: AsyncSession = _DB,
) -> HTMLResponse:
    if confirm != "verify" or request.headers.get("sec-fetch-site") == "cross-site":
        return _page(
            "Confirmation required", "Use the confirmation button in your email link.", status=400
        )
    try:
        await confirm_verification_link(db, token)
    except VerificationLinkError as exc:
        await db.rollback()
        return _page("Link unavailable", str(exc), status=400)
    return _page(
        "Email verified",
        "This notification address is now verified. "
        "Your account and recovery routing choices are unchanged.",
    )
