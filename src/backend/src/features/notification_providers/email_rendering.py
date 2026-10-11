"""Host-owned email presentation; notification text is never executable markup."""

from html import escape
from urllib.parse import urlsplit

from .base import NotificationMessage


def _safe_link(value: str | None) -> str | None:
    if not value or any(ord(char) < 32 for char in value):
        return None
    try:
        url = urlsplit(value)
    except ValueError:
        return None
    return value if url.scheme in {"https", "http"} and url.hostname and not url.username else None


def render_email(message: NotificationMessage, unsubscribe_url: str | None = None) -> str:
    """Render all mail purposes consistently without changing routing or disclosure."""
    action = _safe_link(message.action_url)
    action_label = {
        "auth.password_reset": "Reset password",
        "auth.user_invite": "Accept invitation",
    }.get(message.event_type, "Open notification")
    if message.kind == "destination_verification":
        action_label = "Verify email address"
    category = {
        "recovery": "Account security",
        "invitation": "You are invited",
        "test": "Delivery test",
    }.get(message.purpose, "Notification")
    if message.kind == "destination_verification":
        category = "Email verification"
    # Sensitive bearer links belong in the action, not an unreadably long paragraph.
    body = message.body.replace(action, "Use the button below.") if action else message.body
    paragraphs = "".join(
        '<p style="margin:0 0 18px;line-height:1.65;overflow-wrap:anywhere">'
        + escape(paragraph).replace("\n", "<br>")
        + "</p>"
        for paragraph in body.split("\n\n")
        if paragraph.strip()
    )
    button = (
        '<p style="margin:28px 0"><a style="display:inline-block;background:#3458d4;'
        'color:#fff;text-decoration:none;padding:14px 22px;border-radius:10px;font-weight:600" '
        f'href="{escape(action, quote=True)}">{escape(action_label)}</a></p>'
        if action
        else ""
    )
    unsubscribe = _safe_link(unsubscribe_url)
    footer = (
        '<p style="margin:12px 0 0">Manage or unsubscribe from these notifications '
        f'<a style="color:#3458d4" href="{escape(unsubscribe, quote=True)}">here</a>.</p>'
        if unsubscribe
        else ""
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(message.title)}</title></head>"
        '<body style="margin:0;background:#eef2f8;color:#17213b;font-family:Arial,sans-serif">'
        '<table role="presentation" style="width:100%;border-collapse:collapse"><tr>'
        '<td style="padding:28px 12px"><table role="presentation" style="width:100%;'
        "max-width:600px;margin:0 auto;border-collapse:collapse;background:#fff;"
        'border:1px solid #dbe2ef;border-radius:16px"><tr><td style="padding:32px">'
        '<p style="margin:0 0 16px;color:#536584;font-size:12px;font-weight:700;'
        f'letter-spacing:1px;text-transform:uppercase">{escape(category)}</p>'
        f'<h1 style="margin:0 0 24px;font-size:26px;line-height:1.3">{escape(message.title)}</h1>'
        f"{paragraphs}{button}"
        '<div style="border-top:1px solid #e1e7f0;margin-top:28px;padding-top:20px;'
        'color:#536584;font-size:12px;line-height:1.6">'
        f'<p style="margin:0">Sent by your tracking app.</p>{footer}'
        "</div></td></tr></table></td></tr></table></body></html>"
    )
