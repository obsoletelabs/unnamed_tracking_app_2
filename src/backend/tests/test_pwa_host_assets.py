"""Host upgrades must refresh offline policy without caching private responses."""

import asyncio
import io
import json

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from PIL import Image

from src.database.session import get_db
from src.plugin_api import pwa


@pytest.mark.asyncio
async def test_offline_page_allows_same_origin_themes_without_external_scripts():
    """Exercise the public HTTP response rather than an assumed browser policy."""
    application = FastAPI()
    application.include_router(pwa.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get("/pwa/offline.html")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    directives = {
        parts[0]: parts[1:]
        for directive in response.headers["Content-Security-Policy"].split(";")
        if (parts := directive.split())
    }
    assert directives["default-src"] == ["'none'"]
    assert "'self'" in directives["style-src"]
    assert directives["font-src"] == ["'self'"]
    assert directives["img-src"] == ["'self'"]
    assert directives["script-src"] == ["'unsafe-inline'"]


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_root_worker_composes_only_reviewed_host_fragments(monkeypatch, enabled):
    """Keep the canonical worker intact and bind its extension to the same lifecycle."""

    async def provider(_db):
        return {"plugin_id": "official.pwa"} if enabled else None

    async def assets(_plugin):
        return {}

    async def database():
        yield None

    monkeypatch.setattr(pwa, "provider", provider)
    monkeypatch.setattr(pwa, "checked_assets", assets)
    monkeypatch.setattr(pwa, "generation", lambda _plugin: "reviewed-generation")
    application = FastAPI()
    application.include_router(pwa.router)
    application.dependency_overrides[get_db] = database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get("/service-worker.js")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Service-Worker-Allowed"] == "/"
    canonical = (pwa._ASSETS / "service-worker.js").read_text(encoding="utf-8")
    notifications = (pwa._ASSETS / "notification-push.js").read_text(encoding="utf-8")
    assert response.text.endswith(canonical + "\n" + notifications)
    assert f'"enabled": {str(enabled).lower()}' in response.text
    expected_generation = "reviewed-generation" if enabled else "disabled"
    assert f'"generation": "{expected_generation}"' in response.text


@pytest.mark.parametrize(
    "changed", ["offline.html", "service-worker.js", "notification-push.js", "policy"]
)
def test_host_upgrades_invalidate_the_existing_installation_cache(tmp_path, monkeypatch, changed):
    """A host-only upgrade replaces the cache even when its plugin is unchanged."""
    for name in ("offline.html", "service-worker.js", "notification-push.js"):
        (tmp_path / name).write_text("Original reviewed asset", encoding="utf-8")
    monkeypatch.setattr(pwa, "_ASSETS", tmp_path)
    monkeypatch.setattr(pwa, "_HOST_ASSET_REVISION", pwa.host_asset_revision())
    plugin = {
        "plugin_id": "official.pwa",
        "installation_id": "d0a7d05a-93ea-4697-a103-de02eae0a49e",
        "version": "0.0.3",
        "digest": "1" * 64,
    }
    original = pwa.generation(plugin)
    assert pwa.generation(plugin) == original
    if changed == "policy":
        monkeypatch.setattr(pwa, "_OFFLINE_CSP", "default-src 'none'; base-uri 'none'")
    else:
        (tmp_path / changed).write_text("Updated reviewed asset", encoding="utf-8")
    monkeypatch.setattr(pwa, "_HOST_ASSET_REVISION", pwa.host_asset_revision())
    assert pwa.generation(plugin) != original


def inert_assets():
    declaration = pwa.PluginPwaDeclaration(name="Archive", short_name="Archive")
    manifest = {
        key: getattr(declaration, key)
        for key in ("name", "short_name", "theme_color", "background_color")
    }
    manifest.update(id="/", scope="/", start_url="/?pwa=1", display="standalone")
    files = {declaration.manifest: json.dumps(manifest).encode()}
    for path, size in zip(declaration.icons, (192, 512), strict=True):
        buffer = io.BytesIO()
        Image.new("RGB", (size, size)).save(buffer, format="PNG")
        files[path] = buffer.getvalue()
    return {"plugin_id": "example.inert-pwa", "declaration": declaration}, files


@pytest.mark.asyncio
async def test_inert_pwa_assets_are_checked_concurrently(monkeypatch):
    plugin, files = inert_assets()
    started = set()
    all_started, release = asyncio.Event(), asyncio.Event()

    async def asset(plugin_id, path):
        assert plugin_id == plugin["plugin_id"]
        started.add(path)
        if len(started) == len(files):
            all_started.set()
        await release.wait()
        return files[path]

    monkeypatch.setattr(pwa.client, "pwa_asset", asset)
    task = asyncio.create_task(pwa.checked_assets(plugin))
    try:
        await asyncio.wait_for(all_started.wait(), timeout=1)
        release.set()
        assert await task == files
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["manifest", "icon", "runtime"])
async def test_parallel_pwa_reads_still_reject_invalid_assets(monkeypatch, invalid):
    plugin, files = inert_assets()
    if invalid == "manifest":
        files[plugin["declaration"].manifest] = b'{"name":"Incorrect declaration"}'
    elif invalid == "icon":
        files[plugin["declaration"].icons[0]] = b"Not a PNG"

    async def asset(_plugin_id, path):
        if invalid == "runtime":
            raise pwa.PluginRuntimeUnavailable("Unavailable")
        return files[path]

    monkeypatch.setattr(pwa.client, "pwa_asset", asset)
    with pytest.raises(HTTPException) as failure:
        await pwa.checked_assets(plugin)
    assert failure.value.status_code == 503
