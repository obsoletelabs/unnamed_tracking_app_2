"""The application starts and stops its background services through one lifespan handler."""

import pytest

from src import main

STARTUP = (
    "bootstrap_primary_user",
    "start_image_prefetch",
    "start_provider_health_loop",
    "start_session_retention_loop",
    "start_jobs_loop",
    "start_direct_metrics_listener",
    "start_cache_inventory_loop",
    "start_notification_backlog_loop",
)
SHUTDOWN = (
    "close_auth_limit_connections",
    "stop_image_prefetch",
    "stop_provider_health_loop",
    "stop_direct_metrics_listener",
    "stop_cache_inventory_loop",
    "stop_notification_backlog_loop",
    "close_plugin_runtime_transport",
)


def test_no_deprecated_event_handlers_are_registered() -> None:
    assert not main.app.router.on_startup
    assert not main.app.router.on_shutdown


@pytest.mark.asyncio
async def test_lifespan_runs_services_in_order(monkeypatch) -> None:
    calls: list[str] = []
    for name in STARTUP + SHUTDOWN:

        async def record(name: str = name) -> None:
            calls.append(name)

        monkeypatch.setattr(main, name, record)

    async with main.lifespan(main.app):
        assert calls == list(STARTUP)
    assert calls == list(STARTUP + SHUTDOWN)
