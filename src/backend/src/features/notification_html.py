"""Allowlist HTML sanitiser for authorised email templates.

The sanitiser rebuilds markup from parsed events: only allowlisted elements and attributes are
emitted, every text and attribute value is escaped, links must be public HTTPS or application
paths, and inline style is reduced to a few harmless properties with strict value syntax.
Scripts, frames, forms, SVG, event handlers, `url()` CSS and unknown elements never survive.
"""

import re
from html import escape
from html.parser import HTMLParser

from src.features.notification_safety import url_is_safe

MAX_HTML_CHARACTERS = 60_000
MAX_DEPTH = 16
_ALLOWED_TAGS = frozenset(
    {
        "a", "b", "blockquote", "br", "code", "div", "em", "h1", "h2", "h3", "h4", "hr", "i",
        "li", "ol", "p", "pre", "small", "span", "strong", "table", "tbody", "td", "tfoot",
        "th", "thead", "tr", "u", "ul",
    }
)  # fmt: skip
_VOID = frozenset({"br", "hr"})
_DROP_WITH_CONTENT = frozenset(
    {
        "script", "style", "iframe", "frame", "frameset", "object", "embed", "applet", "svg",
        "math", "template", "noscript", "form", "textarea", "select", "option", "title", "head",
        "button", "audio", "video", "canvas", "xmp", "plaintext",
    }
)  # fmt: skip
_ATTRIBUTES: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "title"}),
    "td": frozenset({"colspan", "rowspan", "align"}),
    "th": frozenset({"colspan", "rowspan", "align", "scope"}),
    "table": frozenset({"role"}),
    "tr": frozenset(),
}
_GLOBAL_ATTRIBUTES = frozenset({"style", "lang", "dir"})
_COLOR = re.compile(
    r"^(?:#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20}|rgb\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\))$"
)
_LENGTH = re.compile(
    r"^(?:0|[0-9]{1,3}(?:\.[0-9]{1,2})?(?:px|em|rem|%))(?:\s+(?:0|[0-9]{1,3}(?:px|em|rem|%))){0,3}$"
)
_STYLE_PROPERTIES: dict[str, re.Pattern[str]] = {
    "color": _COLOR,
    "background-color": _COLOR,
    "font-weight": re.compile(r"^(?:normal|bold|[1-9]00)$"),
    "font-style": re.compile(r"^(?:normal|italic)$"),
    "text-align": re.compile(r"^(?:left|right|center)$"),
    "text-decoration": re.compile(r"^(?:none|underline|line-through)$"),
    "font-size": _LENGTH,
    "padding": _LENGTH,
    "margin": _LENGTH,
    "line-height": re.compile(r"^[0-9]{1,2}(?:\.[0-9]{1,2})?(?:px|em|%)?$"),
}
_DIR_VALUES = frozenset({"ltr", "rtl"})
_LANG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,3}$")


def _safe_style(value: str) -> str:
    kept = []
    for declaration in value.split(";")[:12]:
        name, separator, setting = declaration.partition(":")
        name, setting = name.strip().lower(), setting.strip()
        pattern = _STYLE_PROPERTIES.get(name)
        if separator and pattern and pattern.fullmatch(setting):
            kept.append(f"{name}:{setting}")
    return ";".join(kept)


_ENUMERATED = {
    "dir": _DIR_VALUES,
    "align": frozenset({"left", "right", "center"}),
    "scope": frozenset({"row", "col"}),
    "role": frozenset({"presentation"}),
}


def _clean_attribute(name: str, value: str) -> str | None:
    """The sanitised value of an allowlisted attribute, or None to drop it."""
    if len(value) > 2048:
        return None
    if name == "style":
        return _safe_style(value) or None
    if name in _ENUMERATED:
        return value.lower() if value.lower() in _ENUMERATED[name] else None
    valid = {
        "href": url_is_safe(value),
        "lang": bool(_LANG.fullmatch(value)),
        "colspan": bool(re.fullmatch(r"[1-9][0-9]?", value)),
        "rowspan": bool(re.fullmatch(r"[1-9][0-9]?", value)),
    }
    return value if valid.get(name, name == "title") else None


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skipping = 0

    def _attributes(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        allowed = _ATTRIBUTES.get(tag, frozenset()) | _GLOBAL_ATTRIBUTES
        rendered = []
        for name, raw in attrs:
            name = name.lower()
            value = _clean_attribute(name, (raw or "").strip()) if name in allowed else None
            if value:
                rendered.append(f' {name}="{escape(value, quote=True)}"')
        if tag == "a" and any(item.startswith(" href=") for item in rendered):
            rendered.append(' rel="noopener noreferrer nofollow"')
        return "".join(rendered)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.skipping:
            if tag in _DROP_WITH_CONTENT:
                self.skipping += 1
            return
        if tag in _DROP_WITH_CONTENT:
            self.skipping = 1
            return
        if tag not in _ALLOWED_TAGS or len(self.stack) >= MAX_DEPTH:
            return
        self.out.append(f"<{tag}{self._attributes(tag, attrs)}>")
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skipping:
            if tag in _DROP_WITH_CONTENT:
                self.skipping -= 1
            return
        if tag in self.stack:
            while self.stack:
                top = self.stack.pop()
                self.out.append(f"</{top}>")
                if top == tag:
                    break

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.out.append(escape(data, quote=False))

    # Comments, declarations and processing instructions are dropped by the default no-ops.

    def close_all(self) -> str:
        self.close()
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_html(markup: str) -> str:
    """Return safe markup; oversized input is cut at the limit before parsing."""
    parser = _Sanitizer()
    parser.feed(markup[:MAX_HTML_CHARACTERS])
    return parser.close_all()
