"""Public, token-scoped email opt-out with an explicit confirmation step."""

from html import escape
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.session import get_db
from src.features.notification_unsubscribe import (
    UNSUBSCRIBE_PATH,
    UnsubscribeError,
    unsubscribe_email,
    unsubscribe_status,
)

router = APIRouter(tags=["notifications"])
_DB = Depends(get_db)
_STYLE = """
:root { color-scheme: light dark; font-family: system-ui, sans-serif; }
body { margin: 0; min-height: 100vh; display: grid; place-items: center;
       background: light-dark(#f4f5f7, #15191c); color: light-dark(#1d252b, #eef1f4); }
main { box-sizing: border-box; width: min(94%, 540px); padding: 32px;
       background: light-dark(#fff, #20262b); border: 1px solid #80808050; border-radius: 16px; }
h1 { font-size: 26px; margin-top: 0; } p { line-height: 1.65; }
button { border: 0; border-radius: 8px; padding: 12px 18px; background: #f1ad55;
         color: #201a12; font: inherit; font-weight: 650; cursor: pointer; }
small { display: block; margin-top: 20px; opacity: .8; line-height: 1.6; }
form + form { margin-top: 24px; } .secondary { background: transparent;
    color: inherit; border: 1px solid #80808080; } a { color: inherit; }
"""


def _page(
    title: str,
    text: str,
    *,
    token: str = "",
    event_type: str | None = None,
    stopped: bool = False,
    destination_disabled: bool = False,
    status: int = 200,
) -> HTMLResponse:
    form = ""
    if token and not destination_disabled:
        opening = (
            f'<form method="post" action="{UNSUBSCRIBE_PATH}">'
            f'<input type="hidden" name="token" value="{escape(token, quote=True)}">'
        )
        if event_type and not stopped:
            form += (
                opening + '<input type="hidden" name="scope" value="type">'
                '<button name="confirm" value="unsubscribe">Unsubscribe from this type</button>'
                "</form>"
            )
        form += (
            opening + '<input type="hidden" name="scope" value="all">'
            "<p>Alternatively, stop every notification type to this email destination, "
            "including security and recovery messages.</p>"
            '<button class="secondary" name="confirm" value="unsubscribe">'
            "Stop all email notifications to this destination</button></form>"
        )
    return HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head><body><main>"
        f"<h1>{escape(title)}</h1><p>{escape(text)}</p>{form}"
        "<small>Your inbox and other destinations stay active. "
        '<a href="/settings?area=account&amp;section=notifications">Review other subscriptions '
        "or re-enable notifications in Settings</a> after signing in.</small>"
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


@router.get(UNSUBSCRIBE_PATH, response_class=HTMLResponse)
async def confirmation(
    token: Annotated[str, Query(min_length=1, max_length=2048)], db: AsyncSession = _DB
) -> HTMLResponse:
    try:
        state = await unsubscribe_status(db, token)
    except UnsubscribeError as exc:
        return _page("Link unavailable", str(exc), status=400)
    if state.destination_disabled:
        return _page("Email notifications stopped", "This email destination is already turned off.")
    if state.event_type:
        return _page(
            "Subscription stopped" if state.stopped else "Unsubscribe from this notification type?",
            f"Notification type: {state.event_type}. "
            + (
                "This type is already turned off for this email destination."
                if state.stopped
                else "Confirm to stop this type only for this email destination. Other types remain "
                "enabled. Opening this page has not changed your preferences."
            ),
            token=token,
            event_type=state.event_type,
            stopped=state.stopped,
        )
    return _page(
        "Manage email notifications",
        "This older link is not scoped to a particular notification type. "
        "Opening this page has not changed your preferences.",
        token=token,
    )


@router.post(UNSUBSCRIBE_PATH, response_class=HTMLResponse)
async def confirm_unsubscribe(
    request: Request,
    token: Annotated[str, Form(min_length=1, max_length=2048)],
    confirm: Annotated[str, Form(max_length=32)] = "",
    scope: Annotated[Literal["type", "all"] | None, Form()] = None,
    db: AsyncSession = _DB,
) -> HTMLResponse:
    if confirm != "unsubscribe" or request.headers.get("sec-fetch-site") == "cross-site":
        return _page(
            "Confirmation required", "Use the confirmation button in your email link.", status=400
        )
    try:
        event_type = await unsubscribe_email(db, token, scope)
    except UnsubscribeError as exc:
        await db.rollback()
        return _page("Link unavailable", str(exc), status=400)
    if event_type:
        return _page(
            "Subscription stopped",
            f"{event_type} is now turned off for this email destination. "
            "Other notification types remain enabled.",
            token=token,
            event_type=event_type,
            stopped=True,
        )
    return _page("Email notifications stopped", "This email destination is now turned off.")
