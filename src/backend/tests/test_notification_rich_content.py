"""Rich notification content: unsafe input is rejected and every renderer stays inert."""

import json
import random
import re
import string
import time

import pytest

from src.features.notification_html import sanitize_html
from src.features.notification_markdown import plain_text, render_markdown
from src.features.notification_rich import (
    GENERIC_PUBLIC_TEXT,
    GENERIC_PUBLIC_TITLE,
    apply_template,
    build_view,
    format_fact,
    preview,
    public_projection,
    render_email_html,
    render_plain,
)
from src.features.notification_safety import UnsafeContentError, validate_rich_content
from src.plugin_api.notification_story_contracts import (
    NOTIFICATION_CONTRACT_VERSION,
    NotificationContent,
    RendererContribution,
)


def content(**changes):
    document = {
        "title": "Update ready",
        "summary": "Version **2.0** is available.",
        "blocks": [{"type": "markdown", "markdown": "See the [notes](https://example.com/notes)."}],
        "facts": [
            {"key": "version", "label": "Version", "value": {"kind": "text", "value": "2.0.0"}},
            {"key": "size", "label": "Size", "value": {"kind": "integer", "value": 1234567}},
            {
                "key": "price",
                "label": "Price",
                "value": {"kind": "money", "amount": "4.5", "currency": "EUR"},
            },
            {"key": "at", "label": "At", "value": {"kind": "timestamp", "value": 1_700_000_000}},
        ],
    }
    document.update(changes)
    return NotificationContent.model_validate(document)


def urls(nodes):
    found = []
    for node in nodes:
        if node["t"] == "link":
            found.append(node["href"])
        for child in node.get("c", []) if isinstance(node.get("c"), list) else []:
            found.extend(urls([child]))
    return found


# --- acceptance-time rejection ---------------------------------------------------------------


@pytest.mark.parametrize(
    "markdown",
    [
        "<script>alert(1)</script>",
        "text <SCRIPT src=//evil.example/x.js></SCRIPT>",
        "<img src=x onerror=alert(1)>",
        "<a href='x' onclick = 'steal()'>x</a>",
        "<iframe src='https://evil.example'></iframe>",
        "<style>body{background:url(https://evil.example/t.png)}</style>",
        "<p style='background:url(//evil.example)'>x</p>",
        "<svg onload=alert(1)>",
        "<form action=https://evil.example><input name=p></form>",
        "<base href='https://evil.example/'>",
        "[x](javascript:alert(1))",
        "[x](JaVaScRiPt:alert(1))",
        "[x](data:text/html;base64,PHNjcmlwdD4=)",
        "[x](vbscript:msgbox(1))",
        "[x](http://example.com/insecure)",
        "[x](//evil.example/protocol-relative)",
        "[x](https://user:pw@example.com/)",
        "[x](https://127.0.0.1/)",
        "[x](https://localhost/)",
        "[x](/api/admin/users)",
        "[x](/../../etc/passwd)",
    ],
)
def test_unsafe_markdown_is_rejected(markdown):
    with pytest.raises(UnsafeContentError):
        validate_rich_content(content(blocks=[{"type": "markdown", "markdown": markdown}]))


@pytest.mark.parametrize(
    "text",
    [
        "Your password: hunter2hunter2",
        "api_key=sk_live_abcdefghijkl",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
        "-----BEGIN RSA PRIVATE KEY-----",
        "token=abc123def456ghi",
        "AKIAABCDEFGHIJKLMNOP",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJl",
        "Your verification code is 482913",
        "Reset code: 12345678",
        "Visit https://example.com/cb?token=abcdef0123 now",
        "Visit https://example.com/cb?password=hunter22",
    ],
)
def test_credentials_are_rejected_everywhere(text):
    for field in ("summary", "title"):
        with pytest.raises(UnsafeContentError):
            validate_rich_content(content(**{field: text}))
    with pytest.raises(UnsafeContentError):
        validate_rich_content(content(blocks=[{"type": "text", "text": text}]))
    with pytest.raises(UnsafeContentError):
        validate_rich_content(
            content(facts=[{"key": "k", "label": "L", "value": {"kind": "text", "value": text}}])
        )


def test_ordinary_content_is_accepted():
    accepted = validate_rich_content(
        content(summary="Build 1234 finished in 3 minutes. Tokens saved: none.")
    )
    assert accepted.title == "Update ready"
    validate_rich_content(content(summary="Use the code block `x = 1` and **bold**."))


def test_wire_contract_still_rejects_unsafe_urls_and_paths():
    for action in (
        {"id": "go", "label": "Go", "target": {"kind": "link", "url": "javascript:alert(1)"}},
        {"id": "go", "label": "Go", "target": {"kind": "link", "url": "http://example.com"}},
        {"id": "go", "label": "Go", "target": {"kind": "host_route", "path": "/api/users"}},
    ):
        with pytest.raises(ValueError):
            content(actions=[action])
    with pytest.raises(ValueError):
        content(images=[{"source": {"kind": "external", "url": "https://10.0.0.1/a.png"}}])


# --- Markdown --------------------------------------------------------------------------------


def test_markdown_renders_the_safe_subset():
    nodes = render_markdown(
        "# Title\n\nHello **bold** and *em* with `code`.\n\n- one\n- two\n\n1. a\n2. b\n\n> quoted\n\n---\n\n```\nraw <b>\n```"
    )
    assert [node["t"] for node in nodes] == ["h", "p", "ul", "ol", "quote", "hr", "pre"]
    assert nodes[0]["level"] == 3
    assert nodes[-1]["v"] == "raw <b>"
    assert plain_text(nodes).startswith("Title")


@pytest.mark.parametrize(
    "text",
    ["my_plugin_id", "run snake_case_name now", "path/to/file_name_v2.txt", "a_b_c and x_y"],
)
def test_underscores_inside_words_stay_literal(text):
    assert plain_text(render_markdown(text)) == text
    assert not [
        node for block in render_markdown(text) for node in block["c"] if node["t"] != "text"
    ]


def test_underscore_emphasis_still_works_between_words():
    nodes = render_markdown("an _emphasised_ word")[0]["c"]
    assert [node["t"] for node in nodes] == ["text", "em", "text"]


def test_markdown_never_produces_markup_or_unsafe_links():
    nodes = render_markdown(
        "<b>html</b> ![tracker](https://evil.example/t.png) "
        "[bad](javascript:alert(1)) [ok](https://example.com/x) [app](/settings)"
    )
    assert urls(nodes) == ["https://example.com/x", "/settings"]
    serialized = json.dumps(nodes)
    assert "<b>html</b>" in serialized  # shown as text, not interpreted
    assert "tracker" in serialized and "javascript" not in serialized
    assert render_email_html(content(summary="<b>x</b> [a](javascript:alert(1))")).count("<b>") == 0


def test_markdown_is_bounded_for_hostile_input():
    started = time.monotonic()
    for hostile in (
        "*" * 20_000,
        "[" * 10_000 + "](" * 10_000,
        "**a*" * 5_000,
        "> " * 5_000 + "x",
        "- " * 8_000,
        "`" * 9_999,
        "[a](" + "(" * 9_000,
        "_" * 9_999 + "x",
    ):
        nodes = render_markdown(hostile)
        assert len(json.dumps(nodes)) < 400_000
    assert time.monotonic() - started < 5


def test_markdown_fuzz_never_emits_unsafe_structure():
    rng = random.Random(1090)
    alphabet = string.ascii_letters + " \n*_`[]()<>!#-+>\\:/.&;\"'=" + "javascriptdata:"
    for _ in range(400):
        source = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 200)))
        nodes = render_markdown(source)
        for href in urls(nodes):
            assert href.startswith(("https://", "/")) and not href.startswith("//")
        assert "<script" not in render_email_html(content(summary=source or "x")).lower()


# --- HTML sanitiser --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("markup", "forbidden"),
    [
        ("<script>alert(1)</script><p>ok</p>", ["script", "alert"]),
        ("<p onclick='x()'>ok</p>", ["onclick"]),
        ("<img src=x onerror=alert(1)>", ["img", "onerror"]),
        ("<a href='javascript:alert(1)'>x</a>", ["javascript"]),
        ("<a href='JAVASCRIPT:alert(1)'>x</a>", ["javascript"]),
        ("<a href=' data:text/html,x'>x</a>", ["data:"]),
        ("<a href='http://example.com'>x</a>", ["http://"]),
        ("<iframe src=https://evil.example></iframe>", ["iframe", "evil"]),
        ("<object data=x></object><embed src=x>", ["object", "embed"]),
        ("<svg><script>alert(1)</script></svg>", ["svg", "alert"]),
        ("<math><mi xlink:href='javascript:1'>x</mi></math>", ["math", "javascript"]),
        ("<style>@import 'https://evil.example/x.css';</style>t", ["style", "import", "evil"]),
        ("<p style='background:url(https://evil.example/x)'>t</p>", ["url", "evil", "style="]),
        ("<p style='width:expression(alert(1))'>t</p>", ["expression"]),
        ("<p style='position:fixed;top:0'>t</p>", ["position", "style="]),
        ("<form action=x><input name=a></form>t", ["form", "input"]),
        ("<meta http-equiv=refresh content='0;url=https://evil.example'>", ["meta", "evil"]),
        ("<link rel=stylesheet href=https://evil.example/x.css>", ["link", "evil"]),
        ("<base href=https://evil.example/>", ["base", "evil"]),
        ("<!-- <script>alert(1)</script> -->t", ["script", "alert", "--"]),
        ("<scr<script>ipt>alert(1)</scr</script>ipt>", ["<script"]),
        ('<a href="https://example.com" target=_blank>x</a>', ["target"]),
    ],
)
def test_html_sanitiser_removes_active_content(markup, forbidden):
    cleaned = sanitize_html(markup).lower()
    for token in forbidden:
        assert token not in cleaned, cleaned


def test_html_sanitiser_keeps_safe_formatting_and_links():
    cleaned = sanitize_html(
        '<p style="color:#336699;font-weight:bold;position:fixed">Hi <strong>there</strong> '
        '<a href="https://example.com/a?b=1&c=2" title="t">link</a> <a href="/settings">app</a></p>'
        "<ul><li>one</li></ul><table><tr><th scope=row>k</th><td colspan=2>v</td></tr></table>"
    )
    assert 'style="color:#336699;font-weight:bold"' in cleaned
    assert 'href="https://example.com/a?b=1&amp;c=2"' in cleaned
    assert 'rel="noopener noreferrer nofollow"' in cleaned
    assert 'href="/settings"' in cleaned
    assert "<ul><li>one</li></ul>" in cleaned and 'colspan="2"' in cleaned


def test_html_sanitiser_is_bounded_and_closes_tags():
    deep = "<div>" * 5_000 + "x"
    cleaned = sanitize_html(deep)
    assert cleaned.count("<div>") <= 16 and cleaned.count("<div>") == cleaned.count("</div>")
    assert len(sanitize_html("a" * 500_000)) <= 60_000
    assert sanitize_html("<p>unclosed <b>bold") == "<p>unclosed <b>bold</b></p>"


# --- templates -------------------------------------------------------------------------------


def contribution(kind, template, surface="email"):
    return RendererContribution(
        contract_version=NOTIFICATION_CONTRACT_VERSION,
        event_type="example.update",
        surface=surface,
        kind=kind,
        template=template,
    )


def test_html_template_substitutes_escaped_values_then_sanitises():
    hostile = content(
        title="<script>alert(1)</script>",
        summary='"><img src=x onerror=alert(1)>',
        facts=[{"key": "n", "label": "N", "value": {"kind": "text", "value": "<b onmouseover=x>"}}],
    )
    html = apply_template(
        contribution("html_template", "<h1>{title}</h1><div>{summary}</div><p>{fact_n}</p>"),
        hostile,
    )
    tags = re.findall(r"<[^>]*>", html)
    assert not any(re.match(r"</?(script|img)", tag) for tag in tags)
    assert not any("onerror" in tag or "onmouseover" in tag for tag in tags)  # text only
    assert "&lt;script&gt;" in html
    assert tags[0] == "<h1>"


def test_markdown_template_escapes_values_and_cannot_forge_links():
    hostile = content(title="[click](https://example.com) **x**")
    rendered = apply_template(contribution("markdown_template", "# {title}", "card"), hostile)
    nodes = render_markdown(rendered)
    assert urls(nodes) == []
    assert "click" in plain_text(nodes)


@pytest.mark.parametrize(
    "template",
    [
        "{__class__}",
        "{0}",
        "{user_id}",
        "{title.__class__}",
        "{title!r}",
        "{title:>99}",
        "{fact_a.b}",
    ],
)
def test_templates_cannot_reach_other_data(template):
    with pytest.raises(ValueError):
        contribution("markdown_template", template, "card")


def test_default_email_has_live_link_and_no_images_or_scripts():
    html = render_email_html(
        content(
            images=[
                {"source": {"kind": "external", "url": "https://example.com/a.png"}, "alt": "A"}
            ]
        ),
        live_url="https://app.example/api/notifications/live/nls1_x",
        base_url="https://app.example",
    )
    assert "<img" not in html and "<script" not in html
    assert "View the latest status" in html and "Version" in html


# --- plain text, facts and the PUBLIC projection ---------------------------------------------


def test_plain_text_is_understandable_without_the_app():
    text = render_plain(content(), live_url="https://app.example/l")
    assert text.startswith("Update ready")
    for expected in ("Version 2.0 is available", "Version: 2.0.0", "Size: 1,234,567", "4.50 EUR"):
        assert expected in text
    assert "2023-11-14 22:13:20 UTC" in text and "Latest status: https://app.example/l" in text
    assert "<" not in text and "**" not in text


def test_fact_formatting_never_uses_binary_floats():
    facts = {f.key: format_fact(f)["text"] for f in content().facts}
    assert facts["price"] == "4.50 EUR" and facts["size"] == "1,234,567"


def test_preview_is_deterministic_and_short():
    assert preview("a   b\n c") == "a b c"
    assert len(preview("word " * 500)) == 280 and preview("word " * 500).endswith("…")


def test_public_projection_uses_only_approved_public_fields():
    private = content(summary="Secret token holder is alice@example.com")
    view = public_projection(
        public_title=None,
        public_body=None,
        phase="available",
        revision=3,
        updated_at=1_700_000_000,
        expires_at=None,
        now=1_700_000_100,
    )
    assert view["title"] == GENERIC_PUBLIC_TITLE and view["text"] == GENERIC_PUBLIC_TEXT
    assert set(view) == {
        "title", "text", "status", "revision", "updated_at", "updated_iso", "expires_at", "stale",
    }  # fmt: skip
    assert "alice" not in json.dumps(view) and private.summary not in json.dumps(view)
    stale = public_projection(
        public_title="Released", public_body="Out now", phase="x", revision=1,
        updated_at=1, expires_at=5, now=10,
    )  # fmt: skip
    assert stale["stale"] and stale["title"] == "Released"


def test_view_marks_stale_content_and_keeps_actions_inert():
    view = build_view(
        content(
            expires_at=100,
            actions=[
                {"id": "open", "label": "Open", "target": {"kind": "host_route", "path": "/x"}},
                {
                    "id": "run",
                    "label": "Run",
                    "style": "danger",
                    "target": {"kind": "plugin_action", "action_id": "do.it", "input": {"a": 1}},
                },
            ],
        ),
        images=[],
        now=200,
    )
    assert view["stale"] is True
    assert view["actions"][0] == {
        "id": "open", "label": "Open", "style": "default", "kind": "route", "href": "/x",
    }  # fmt: skip
    # Plugin action entries never expose the plugin's id, input or target.
    assert view["actions"][1] == {"id": "run", "label": "Run", "style": "danger", "kind": "action"}
