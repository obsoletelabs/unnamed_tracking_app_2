"""Safe presentations of canonical notification content.

Everything here is pure: it turns validated `NotificationContent` into
- a structured, escaped *view* that clients draw with plain text nodes,
- readable plain text for providers without rich formats,
- sanitised email HTML (default layout or an authorised template),
- the PUBLIC projection of a story, built only from host-approved public fields.

The private canonical document never feeds the projection.
"""

import re
from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from typing import Any

from src.features.notification_html import sanitize_html
from src.features.notification_markdown import Node, plain_text, render_markdown
from src.plugin_api.notification_story_contracts import (
    BooleanFactValue,
    ContentFact,
    DecimalFactValue,
    IntegerFactValue,
    MarkdownBlock,
    MoneyFactValue,
    NotificationContent,
    RendererContribution,
    TextFactValue,
    TimestampFactValue,
)

SUMMARY_PREVIEW_CHARACTERS = 280
GENERIC_PUBLIC_TITLE = "Notification update"
GENERIC_PUBLIC_TEXT = "Sign in to see the details of this update."
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!<>~|])")


def humanize_phase(phase: str) -> str:
    return phase.replace("_", " ").replace("-", " ").strip().capitalize() or "Update"


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_fact(fact: ContentFact) -> dict[str, Any]:
    """Display value of one fact; `iso`/`ts` let clients localise timestamps."""
    value = fact.value
    base: dict[str, Any] = {"key": fact.key, "label": fact.label, "kind": value.kind}
    if isinstance(value, TextFactValue):
        return {**base, "text": value.value}
    if isinstance(value, IntegerFactValue):
        return {**base, "text": f"{value.value:,}"}
    if isinstance(value, DecimalFactValue):
        return {**base, "text": value.value}
    if isinstance(value, MoneyFactValue):
        amount = Decimal(value.amount)
        if int(amount.as_tuple().exponent) > -2:
            amount = amount.quantize(Decimal("0.01"))
        return {**base, "text": f"{amount:,} {value.currency}"}
    if isinstance(value, TimestampFactValue):
        return {**base, "text": _iso(value.value).replace("T", " ").replace("Z", " UTC"),
                "iso": _iso(value.value), "ts": value.value}  # fmt: skip
    assert isinstance(value, BooleanFactValue)
    return {**base, "text": "Yes" if value.value else "No"}


def preview(summary: str) -> str:
    """Deterministic preview truncation for compact surfaces such as the bell."""
    text = " ".join(summary.split())
    if len(text) <= SUMMARY_PREVIEW_CHARACTERS:
        return text
    return text[: SUMMARY_PREVIEW_CHARACTERS - 1].rstrip() + "…"


def build_view(
    content: NotificationContent,
    *,
    images: list[dict[str, Any]],
    now: int,
) -> dict[str, Any]:
    """Structured render of the full content. `images` are already policy-resolved entries."""
    actions: list[dict[str, Any]] = []
    for action in content.actions:
        target = action.target
        entry: dict[str, Any] = {"id": action.id, "label": action.label, "style": action.style}
        if target.kind == "link":
            entry |= {"kind": "link", "href": target.url}
        elif target.kind == "host_route":
            entry |= {"kind": "route", "href": target.path}
        else:
            entry |= {"kind": "action"}
        actions.append(entry)
    return {
        "title": content.title,
        "summary": render_markdown(content.summary),
        "preview": preview(content.summary),
        "blocks": [
            render_markdown(block.markdown)
            if isinstance(block, MarkdownBlock)
            else [{"t": "p", "c": [{"t": "text", "v": block.text}]}]
            for block in content.blocks
        ],
        "facts": [format_fact(fact) for fact in content.facts],
        "images": images,
        "actions": actions,
        "attribution": (
            {"label": content.attribution.label, "url": content.attribution.url}
            if content.attribution
            else None
        ),
        "expires_at": content.expires_at,
        "stale": content.expires_at is not None and content.expires_at <= now,
    }


def render_plain(
    content: NotificationContent, live_url: str | None = None, *, facts: bool = True
) -> str:
    """Understandable without opening the app: summary, blocks, facts and the live link."""
    parts = [content.title, "", plain_text(render_markdown(content.summary))]
    for block in content.blocks:
        text = (
            plain_text(render_markdown(block.markdown))
            if isinstance(block, MarkdownBlock)
            else block.text
        )
        parts += ["", text]
    if content.facts and facts:
        parts.append("")
        parts += [f"{fact['label']}: {fact['text']}" for fact in map(format_fact, content.facts)]
    if live_url:
        parts += ["", f"Latest status: {live_url}"]
    return "\n".join(parts).strip()


# --- inline HTML for email -------------------------------------------------------------------


def _html_inline(nodes: list[Node]) -> str:
    out = []
    for node in nodes:
        kind = node["t"]
        if kind == "text":
            out.append(escape(node["v"], quote=False))
        elif kind == "code":
            out.append(f"<code>{escape(node['v'], quote=False)}</code>")
        elif kind == "br":
            out.append("<br>")
        elif kind in {"strong", "em"}:
            out.append(f"<{kind}>{_html_inline(node['c'])}</{kind}>")
        elif kind == "link":
            out.append(
                f'<a href="{escape(node["href"], quote=True)}" rel="noopener noreferrer nofollow">'
                f"{_html_inline(node['c'])}</a>"
            )
    return "".join(out)


def nodes_to_html(nodes: list[Node]) -> str:
    """Serialise a safe node tree; every string is escaped and every URL was validated."""
    out = []
    for node in nodes:
        kind = node["t"]
        if kind == "p":
            out.append(f"<p>{_html_inline(node['c'])}</p>")
        elif kind == "h":
            out.append(f"<h{node['level']}>{_html_inline(node['c'])}</h{node['level']}>")
        elif kind in {"ul", "ol"}:
            items = "".join(f"<li>{_html_inline(item['c'])}</li>" for item in node["c"])
            out.append(f"<{kind}>{items}</{kind}>")
        elif kind == "quote":
            out.append(f"<blockquote>{nodes_to_html(node['c'])}</blockquote>")
        elif kind == "pre":
            out.append(f"<pre>{escape(node['v'], quote=False)}</pre>")
        elif kind == "hr":
            out.append("<hr>")
        else:
            out.append(_html_inline([node]))
    return "".join(out)


def _facts_html(content: NotificationContent) -> str:
    rows = "".join(
        f'<tr><th scope="row" style="text-align:left">{escape(fact["label"])}</th>'
        f"<td>{escape(fact['text'])}</td></tr>"
        for fact in map(format_fact, content.facts)
    )
    return f"<table>{rows}</table>" if rows else ""


def _actions_html(content: NotificationContent, base_url: str) -> str:
    links = []
    for action in content.actions:
        target = action.target
        if target.kind == "link":
            href = target.url
        elif target.kind == "host_route":
            href = f"{base_url}{target.path}" if base_url else ""
        else:
            continue  # plugin actions run only inside the signed-in app
        if href:
            links.append(
                f'<a href="{escape(href, quote=True)}" rel="noopener noreferrer nofollow">'
                f"{escape(action.label)}</a>"
            )
    return "<p>" + " | ".join(links) + "</p>" if links else ""


def _blocks_html(content: NotificationContent) -> str:
    return "".join(
        nodes_to_html(render_markdown(block.markdown))
        if isinstance(block, MarkdownBlock)
        else f"<p>{escape(block.text, quote=False)}</p>"
        for block in content.blocks
    )


def _escape_markdown(value: str) -> str:
    return _MARKDOWN_SPECIAL.sub(r"\\\1", value)


def _template_values(
    content: NotificationContent, live_url: str | None, base_url: str, html: bool
) -> dict[str, str]:
    esc = (lambda v: escape(v, quote=False)) if html else _escape_markdown
    facts = {fact.key: format_fact(fact)["text"] for fact in content.facts}
    values = {
        "title": esc(content.title),
        "summary": nodes_to_html(render_markdown(content.summary))
        if html
        else _escape_markdown(content.summary),
        "blocks": _blocks_html(content)
        if html
        else "\n\n".join(
            block.markdown if isinstance(block, MarkdownBlock) else _escape_markdown(block.text)
            for block in content.blocks
        ),
        "facts": _facts_html(content)
        if html
        else "\n".join(
            f"- {_escape_markdown(f['label'])}: {_escape_markdown(f['text'])}"
            for f in map(format_fact, content.facts)
        ),  # fmt: skip
        "actions": _actions_html(content, base_url) if html else "",
        "attribution": esc(content.attribution.label) if content.attribution else "",
        "occurred_at": "",
        "expires_at": esc(_iso(content.expires_at)) if content.expires_at else "",
        "live_link": (escape(live_url, quote=True) if html else live_url) if live_url else "",
    }
    values.update({f"fact_{key}": esc(text) for key, text in facts.items()})
    return values


def apply_template(
    contribution: RendererContribution,
    content: NotificationContent,
    *,
    live_url: str | None = None,
    base_url: str = "",
) -> str:
    """Substitute escaped canonical values into an authorised template, then sanitise it.

    Unknown placeholders render empty; literal braces that are not placeholders stay as text.
    Markdown templates return Markdown source for `render_markdown`; HTML templates return
    sanitised markup. The template can never add data the content does not contain.
    """
    if contribution.template is None:
        raise ValueError("Contribution has no template")
    html = contribution.kind == "html_template"
    values = _template_values(content, live_url, base_url, html)
    result = _PLACEHOLDER.sub(lambda m: values.get(m.group(1), ""), contribution.template)
    return sanitize_html(result) if html else result


def render_email_html(
    content: NotificationContent,
    *,
    live_url: str | None = None,
    base_url: str = "",
    contribution: RendererContribution | None = None,
) -> str:
    """Sanitised email body; images are not embedded (they would be tracking beacons)."""
    if contribution is not None and contribution.kind == "html_template":
        return apply_template(contribution, content, live_url=live_url, base_url=base_url)
    if contribution is not None and contribution.kind == "markdown_template":
        body = nodes_to_html(
            render_markdown(apply_template(contribution, content, live_url=live_url))
        )
    else:
        body = (
            f"<h1>{escape(content.title, quote=False)}</h1>"
            + nodes_to_html(render_markdown(content.summary))
            + _blocks_html(content)
            + _facts_html(content)
            + _actions_html(content, base_url)
        )
    if live_url and "live_link" not in (contribution.template or "" if contribution else ""):
        body += (
            f'<p><a href="{escape(live_url, quote=True)}" rel="noopener noreferrer nofollow">'
            "View the latest status</a></p>"
        )
    return sanitize_html(body)


# --- PUBLIC projection -----------------------------------------------------------------------


def public_projection(
    *,
    public_title: str | None,
    public_body: str | None,
    phase: str,
    revision: int,
    updated_at: int,
    expires_at: int | None,
    now: int,
) -> dict[str, Any]:
    """What a bearer of a live link may see: host-approved public fields and the phase only.

    It is built from `Notification.public_title/public_body` (the existing release projection),
    never from the canonical document. Facts, blocks, images, actions, identifiers, recipients,
    sources and installations are not part of it.
    """
    return {
        "title": public_title or GENERIC_PUBLIC_TITLE,
        "text": public_body or GENERIC_PUBLIC_TEXT,
        "status": humanize_phase(phase),
        "revision": revision,
        "updated_at": updated_at,
        "updated_iso": _iso(updated_at),
        "expires_at": expires_at,
        "stale": expires_at is not None and expires_at <= now,
    }
