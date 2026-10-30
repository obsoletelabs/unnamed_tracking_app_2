"""Acceptance-time safety checks for rich notification content.

Content is rejected, not silently rewritten, when it carries executable markup, unsafe URLs, CSS
or credential-shaped secrets. Renderers escape everything again (defence in depth); these checks
make a producer's mistake visible instead of shipping a mangled message.
"""

import re
from collections.abc import Iterator
from urllib.parse import parse_qsl, urlsplit

from pydantic import TypeAdapter, ValidationError

from src.plugin_api.notification_story_contracts import (
    SPOOFING_CHARACTERS,
    ApplicationPath,
    MarkdownBlock,
    NotificationContent,
    PublicHttpsUrl,
    TextFactValue,
)

_HTTPS_URL: TypeAdapter[str] = TypeAdapter(PublicHttpsUrl)
_APP_PATH: TypeAdapter[str] = TypeAdapter(ApplicationPath)


class UnsafeContentError(ValueError):
    """The content contains something the host never renders or forwards."""


_EXECUTABLE_MARKUP = re.compile(
    r"<\s*/?\s*(?:script|iframe|frame|frameset|object|embed|applet|form|input|button|textarea"
    r"|select|base|meta|link|style|svg|math|template|noscript)\b"
    r"|<\s*[a-z][^>]{0,200}?[\s/\"']on[a-z]+\s*="
    r"|<\s*[a-z][^>]{0,200}?\sstyle\s*=",
    re.IGNORECASE,
)
_DANGEROUS_SCHEME = re.compile(
    r"(?:^|[^a-z0-9+.-])(?:javascript|vbscript|data|file|blob|about)\s*:", re.IGNORECASE
)
_CSS = re.compile(r"expression\s*\(|@import|behaviou?r\s*:|-moz-binding|url\s*\(", re.IGNORECASE)
_MARKDOWN_TARGET = re.compile(r"\]\(\s*<?([^)\s>]*)")
_SECRET_KEY = (
    r"(?:pass(?:word|wd)?|secret|api[_-]?key|access[_-]?token|auth[_-]?token|token|signature)"
)
_CREDENTIALS = (
    re.compile(r"-----BEGIN [A-Z ]{0,30}PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    re.compile(r"\bBasic\s+[A-Za-z0-9+/=]{16,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(
        rf"(?<![A-Za-z0-9]){_SECRET_KEY}\s*[:=]\s*[\"']?(?!\s)[^\s\"']{{6,}}", re.IGNORECASE
    ),
    re.compile(
        r"\b(?:verification|confirmation|one[- ]time|security|login|sign[- ]?in|reset|otp|2fa)"
        r"[^\n]{0,24}\bcode\b[^\n0-9]{0,16}[0-9]{4,10}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:otp|passcode)\b[^\n0-9]{0,12}[0-9]{4,10}\b", re.IGNORECASE),
)
CREDENTIAL_PATTERNS = _CREDENTIALS
_SECRET_QUERY = re.compile(
    r"^(?:pass(?:word|wd)?|secret|api[_-]?key|apikey|key|access[_-]?token|auth|token|sig|signature"
    r"|code|session|sid|jwt|credential|bearer)$",
    re.IGNORECASE,
)
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


def content_strings(content: NotificationContent) -> Iterator[str]:
    """Every producer-controlled string, for scanning."""
    yield content.title
    yield content.summary
    for block in content.blocks:
        yield block.markdown if isinstance(block, MarkdownBlock) else block.text
    for fact in content.facts:
        yield fact.label
        if isinstance(fact.value, TextFactValue):
            yield fact.value.value
    for image in content.images:
        yield image.alt
        if image.source.kind == "external":
            yield image.source.url
    for action in content.actions:
        yield action.label
        target = action.target
        if target.kind == "link":
            yield target.url
        elif target.kind == "host_route":
            yield target.path
        else:
            yield from (value for value in target.input.values() if isinstance(value, str))
    if content.attribution:
        yield content.attribution.label
        if content.attribution.url:
            yield content.attribution.url


def url_is_safe(value: str) -> bool:
    """True for a public HTTPS URL or an application path, and nothing else."""
    for adapter in (_HTTPS_URL, _APP_PATH):
        try:
            adapter.validate_python(value)
        except ValidationError:
            continue
        return not _has_secret_query(value)
    return False


def _has_secret_query(url: str) -> bool:
    try:
        query = urlsplit(url).query
    except ValueError:
        return True
    return any(_SECRET_QUERY.fullmatch(key) and val for key, val in parse_qsl(query))


def markdown_link_targets(markdown: str) -> list[str]:
    return [match.group(1) for match in _MARKDOWN_TARGET.finditer(markdown)]


def markdown_is_safe(markdown: str) -> bool:
    """True when Markdown a provider would display verbatim has no markup, image or unsafe link.

    Template output is composed by the host but its literal text comes from the plugin, so it is
    held to the same rule as producer Markdown before any provider receives it as source.
    """
    if _EXECUTABLE_MARKUP.search(markdown) or "![" in markdown:
        return False
    return all(url_is_safe(target) for target in markdown_link_targets(markdown))


def check_plain_text(value: str) -> None:
    """The checks every displayed string gets, for text the host composes from a template."""
    _check_text(value, markup=False)


def _check_text(value: str, *, markup: bool) -> None:
    if SPOOFING_CHARACTERS.search(value):
        raise UnsafeContentError("Content contains hidden or direction-overriding characters")
    if _EXECUTABLE_MARKUP.search(value):
        raise UnsafeContentError("Content contains executable or embedded markup")
    if _CSS.search(value) and "<" in value:
        raise UnsafeContentError("Content contains CSS that is not allowed")
    if markup and any(not url_is_safe(target) for target in markdown_link_targets(value)):
        raise UnsafeContentError("Markdown links must use public HTTPS URLs or application paths")
    for credential in _CREDENTIALS:
        if credential.search(value):
            raise UnsafeContentError("Content appears to contain a credential or secret")
    for url in _URL_IN_TEXT.findall(value):
        if _has_secret_query(url):
            raise UnsafeContentError("A URL in the content carries a secret parameter")


def validate_rich_content(content: NotificationContent) -> NotificationContent:
    """Reject unsafe content. The wire model already enforces structure, URL syntax and sizes."""
    for block in content.blocks:
        if isinstance(block, MarkdownBlock):
            if any(
                _DANGEROUS_SCHEME.search(target) for target in markdown_link_targets(block.markdown)
            ):
                raise UnsafeContentError("Markdown links must not use script or data schemes")
            _check_text(block.markdown, markup=True)
    for value in content_strings(content):
        _check_text(value, markup=False)
    for action in content.actions:
        if action.target.kind == "link" and not url_is_safe(action.target.url):
            raise UnsafeContentError("Action links must use public HTTPS URLs")
    for image in content.images:
        if image.source.kind == "external" and not url_is_safe(image.source.url):
            raise UnsafeContentError("Image URLs must be public HTTPS URLs")
    if content.attribution and content.attribution.url and not url_is_safe(content.attribution.url):
        raise UnsafeContentError("Attribution links must use public HTTPS URLs")
    return content
