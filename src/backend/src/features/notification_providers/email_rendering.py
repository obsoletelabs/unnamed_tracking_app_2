"""Host-owned email presentation; notification text is never executable markup."""

import re
from html import escape
from urllib.parse import urlsplit

from src.features.notification_html import sanitize_html

from .base import NotificationMessage


def _safe_link(value: str | None) -> str | None:
    if not value or any(ord(char) < 32 for char in value):
        return None
    try:
        url = urlsplit(value)
    except ValueError:
        return None
    return value if url.scheme in {"https", "http"} and url.hostname and not url.username else None


def is_rich(message: NotificationMessage) -> bool:
    """Canonical story mail or a digest: composed from host-sanitised content, not a bearer link."""
    return bool(message.items) or message.html is not None


def _live_button(message: NotificationMessage, accent: str) -> str:
    link = _safe_link(message.link)
    if not link:
        return ""
    return (
        '<p style="margin:24px 0 8px"><a style="display:inline-block;background:'
        f"{accent};color:#fff;text-decoration:none;padding:12px 20px;border-radius:10px;"
        f'font-weight:600" href="{escape(link, quote=True)}" rel="noopener noreferrer nofollow">'
        "View the latest status</a></p>"
        '<p style="margin:0 0 8px;color:#536584;font-size:12px">The link opens the current state '
        "after you sign in. This email itself does not change.</p>"
    )


def _rich_body(message: NotificationMessage, accent: str) -> str:
    """Sanitised story HTML, or one section per digest item; every part has a text equivalent."""
    if message.items:
        sections = []
        for item in message.items:
            url = _safe_link(item.url)
            link = (
                f'<p style="margin:0"><a style="color:{accent}" href="{escape(url, quote=True)}"'
                ' rel="noopener noreferrer nofollow">View the latest status</a></p>'
                if url
                else ""
            )
            sections.append(
                '<div style="border-top:1px solid #e1e7f0;padding:16px 0">'
                f'<h2 style="margin:0 0 8px;font-size:18px">{escape(item.title)}</h2>'
                '<p style="margin:0 0 10px;line-height:1.6;overflow-wrap:anywhere">'
                f"{escape(item.body).replace(chr(10), '<br>')}</p>{link}</div>"
            )
        everything = _safe_link(message.link)
        tail = (
            f'<p style="margin:16px 0 0"><a style="color:{accent}" '
            f'href="{escape(everything, quote=True)}" rel="noopener noreferrer nofollow">'
            "All notifications</a></p>"
            if everything
            else ""
        )
        heading = f'<h1 style="margin:0 0 16px;font-size:26px">{escape(message.title)}</h1>'
        return heading + "".join(sections) + tail
    images = "".join(
        f'<p style="margin:0 0 16px"><img src="{escape(url, quote=True)}" '
        f'alt="{escape(message.title)}" style="max-width:100%;height:auto;border-radius:8px"></p>'
        for url in message.images
        if _safe_link(url)
    )
    return images + sanitize_html(message.html or "")


def render_plain_email(message: NotificationMessage, unsubscribe_url: str | None = None) -> str:
    """Text part carrying the same title, content, links and opt-out as the HTML part."""
    if not is_rich(message):
        return message.body
    lines = [message.title, ""]
    if message.items:
        for number, item in enumerate(message.items, 1):
            lines += [f"{number}. {item.title}", item.body]
            if _safe_link(item.url):
                lines.append(f"Latest status: {item.url}")
            lines.append("")
    else:
        lines += [message.body, ""]
        lines += [f"{action.label}: {action.url}" for action in message.actions]
    if _safe_link(message.link) and not message.items:
        lines.append(f"Latest status: {message.link}")
    if message.link and message.items:
        lines.append(f"All notifications: {message.link}")
    if _safe_link(unsubscribe_url):
        # The one-click opt-out stays in the List-Unsubscribe header and the HTML footer: a
        # scanner or forwarded plain-text copy must not carry the bearer link.
        lines += ["", "To stop these notifications, use your mail app's Unsubscribe option."]
    return "\n".join(lines).strip() + "\n"


def render_email(message: NotificationMessage, unsubscribe_url: str | None = None) -> str:
    """Render all mail purposes consistently without changing routing or disclosure."""
    action = _safe_link(message.action_url)
    presentation = message.email_presentation
    accent = presentation.accent_color if presentation else "#3458d4"
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        accent = "#3458d4"
    heading = presentation.heading if presentation else message.title
    footer_text = presentation.footer if presentation else "Sent by your tracking app."
    simple = presentation is not None and presentation.layout == "simple"
    action_label = {
        "auth.password_reset": "Reset password",
        "auth.user_invite": "Accept invitation",
    }.get(message.event_type, "Open notification")
    if message.kind == "destination_verification":
        action_label = "Verify email address"
    if presentation:
        action_label = presentation.action_label
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
    rich = is_rich(message)
    if rich:
        paragraphs = _rich_body(message, accent) + (
            _live_button(message, accent) if not message.items else ""
        )
    button = (
        f'<p style="margin:28px 0"><a style="display:inline-block;background:{accent};'
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
        f'<body style="margin:0;background:{"#fff" if simple else "#eef2f8"};'
        'color:#17213b;font-family:Arial,sans-serif">'
        '<table role="presentation" style="width:100%;border-collapse:collapse"><tr>'
        '<td style="padding:28px 12px"><table role="presentation" style="width:100%;'
        "max-width:600px;margin:0 auto;border-collapse:collapse;background:#fff;"
        f'border:{"0" if simple else "1px solid #dbe2ef"};border-radius:16px"><tr><td style="padding:32px">'
        '<p style="margin:0 0 16px;color:#536584;font-size:12px;font-weight:700;'
        f'letter-spacing:1px;text-transform:uppercase">{escape(category)}</p>'
        + (
            ""
            if rich
            else f'<h1 style="margin:0 0 24px;font-size:26px;line-height:1.3">{escape(heading)}</h1>'
        )
        + f"{paragraphs}{button}"
        '<div style="border-top:1px solid #e1e7f0;margin-top:28px;padding-top:20px;'
        'color:#536584;font-size:12px;line-height:1.6">'
        f'<p style="margin:0">{escape(footer_text)}</p>{footer}'
        "</div></td></tr></table></td></tr></table></body></html>"
    )
