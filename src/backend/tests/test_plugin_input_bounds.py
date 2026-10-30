"""Plugin-fed collections and request bodies stay bounded (#398)."""

import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from src.api.routes.plugin_manager import backend as plugin_backend
from src.features.achievement_catalogue import catalogue_rows
from src.features.provider_play import MAX_CUSTOM_FIELDS, provider_custom_values
from src.plugin_api.contracts import PluginUiDocument, UiAction, UiTable, UiTableColumn


def _request(chunks: list[bytes], *, length: bool = False) -> Request:
    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    headers = [(b"content-type", b"application/json")]
    if length:
        headers.append((b"content-length", str(sum(map(len, chunks))).encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "query_string": b"",
        "headers": headers,
    }
    return Request(scope, receive)


async def test_a_chunked_body_over_the_limit_is_refused_without_a_content_length():
    big = b"x" * (plugin_backend._MAX_PLUGIN_ROUTE_BODY_BYTES // 2 + 1)
    with pytest.raises(HTTPException) as refused:
        await plugin_backend._backend_route_request(_request([big, big, big]), {})
    assert refused.value.status_code == 413


async def test_a_small_chunked_body_is_still_read():
    body = json.dumps({"value": 1}).encode()
    chunks = [body[:5], body[5:]]
    result = await plugin_backend._backend_route_request(_request(chunks), {})
    assert result["body"] == {"value": 1}


def test_long_achievement_ids_sharing_a_prefix_become_one_row():
    prefix = "a" * 200
    items = [
        {"external_id": prefix + "-one", "name": "One"},
        {"external_id": prefix + "-two", "name": "Two"},
    ]
    rows = catalogue_rows({"metadata": {"achievements": items}})
    assert [row["external_id"] for row in rows] == [prefix]


def test_provider_custom_fields_cannot_grow_without_bound():
    current = {f"k{i}": i for i in range(MAX_CUSTOM_FIELDS)}
    assert provider_custom_values({"custom": {"extra": 1}}, current) is None
    merged = provider_custom_values({"custom": {"k0": 99, "extra": 1}}, current)
    assert merged is not None and len(merged) == MAX_CUSTOM_FIELDS and merged["k0"] == 99
    assert "extra" not in merged


def test_ui_document_collections_are_bounded():
    actions = tuple(UiAction(id=f"a{i}", label="Action") for i in range(257))
    with pytest.raises(ValidationError, match="at most 256"):
        PluginUiDocument(plugin_id="example.plugin", title="Example", actions=actions)
    columns = tuple(UiTableColumn(id=f"c{i}", label="Column") for i in range(65))
    with pytest.raises(ValidationError, match="at most 64 columns"):
        PluginUiDocument(
            plugin_id="example.plugin",
            title="Example",
            tables=(UiTable(id="t", title="Table", columns=columns),),
        )
