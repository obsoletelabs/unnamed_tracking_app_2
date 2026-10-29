"""Request breakdowns isolate concurrent work and exclude post-header/background work."""

import asyncio
import threading

import httpx
import pytest
from fastapi import FastAPI, Request
from prometheus_client import generate_latest
from sqlalchemy import create_engine, text
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse

from src.api.metrics import MetricsMiddleware
from src.core import metrics, request_metrics
from src.core.database_metrics import install_database_metrics


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(metrics.settings, "METRICS_ENABLED", True)


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    install_database_metrics(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE games (value TEXT)"))
        connection.execute(text("INSERT INTO games VALUES (:value)"), {"value": "private-value"})
    yield engine
    engine.dispose()


def sample(name, route, **labels):
    return metrics.REGISTRY.get_sample_value(name, {"method": "GET", "route": route, **labels}) or 0


def query_count(route):
    return sample("uta_http_database_queries_total", route, pool="application", query_group="games")


def select_game(database):
    with database.connect() as connection:
        connection.execute(
            text("SELECT value FROM games WHERE value = :value"), {"value": "private-value"}
        ).all()


@pytest.mark.asyncio
async def test_concurrent_requests_keep_query_counts_and_phases_separate(database):
    application = FastAPI()
    application.add_middleware(MetricsMiddleware)
    first_started, second_finished = asyncio.Event(), asyncio.Event()

    @application.get("/measured-first/{item}")
    async def first(item: str):
        with request_metrics.phase("auth.verify"):
            select_game(database)
            first_started.set()
            await second_finished.wait()
        with request_metrics.phase("private-" + item):
            pass
        return {"ok": True}

    @application.get("/measured-second/{item}")
    async def second(item: str):
        await first_started.wait()
        with request_metrics.phase("storage.prepare"):
            select_game(database)
            select_game(database)
        second_finished.set()
        return {"ok": bool(item)}

    routes = ["/measured-first/{item}", "/measured-second/{item}"]
    before = [query_count(route) for route in routes]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        responses = await asyncio.gather(
            client.get("/measured-first/private-id"),
            client.get("/measured-second/another-private-id"),
        )
    assert [response.status_code for response in responses] == [200, 200]
    assert [
        query_count(route) - initial for route, initial in zip(routes, before, strict=True)
    ] == [1, 2]
    assert sample("uta_http_phase_duration_seconds_count", routes[0], phase="auth.verify") == 1
    assert sample("uta_http_phase_duration_seconds_count", routes[1], phase="storage.prepare") == 1
    output = generate_latest(metrics.REGISTRY).decode()
    assert "private-id" not in output and "private-value" not in output
    assert "SELECT value" not in output


@pytest.mark.asyncio
async def test_background_queries_are_not_reported_as_response_start_work(database):
    application = FastAPI()
    application.add_middleware(MetricsMiddleware)

    async def background():
        with request_metrics.phase("storage.prepare"):
            select_game(database)

    @application.get("/background-breakdown")
    async def endpoint():
        select_game(database)
        return JSONResponse({"ok": True}, background=BackgroundTask(background))

    before = query_count("/background-breakdown")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        assert (await client.get("/background-breakdown")).status_code == 200
    assert query_count("/background-breakdown") == before + 1
    assert (
        sample(
            "uta_http_phase_duration_seconds_count",
            "/background-breakdown",
            phase="storage.prepare",
        )
        == 0
    )


@pytest.mark.asyncio
async def test_cancellation_closes_the_request_measurement():
    application = FastAPI()
    application.add_middleware(MetricsMiddleware)

    @application.get("/cancelled-breakdown")
    async def endpoint():
        with request_metrics.phase("storage.prepare"):
            raise asyncio.CancelledError

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        with pytest.raises(asyncio.CancelledError):
            await client.get("/cancelled-breakdown")
    assert (
        sample(
            "uta_http_phase_duration_seconds_count", "/cancelled-breakdown", phase="storage.prepare"
        )
        == 1
    )
    with request_metrics.phase("storage.prepare"):
        pass
    assert (
        sample(
            "uta_http_phase_duration_seconds_count", "/cancelled-breakdown", phase="storage.prepare"
        )
        == 1
    )


@pytest.mark.asyncio
async def test_disabled_metrics_do_not_allocate_request_breakdowns(database, monkeypatch):
    monkeypatch.setattr(metrics.settings, "METRICS_ENABLED", False)
    application = FastAPI()
    application.add_middleware(MetricsMiddleware)

    @application.get("/disabled-breakdown")
    async def endpoint():
        with request_metrics.phase("auth.verify"):
            select_game(database)
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        assert (await client.get("/disabled-breakdown")).status_code == 200
    assert query_count("/disabled-breakdown") == 0
    assert (
        sample("uta_http_phase_duration_seconds_count", "/disabled-breakdown", phase="auth.verify")
        == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("authenticated", [False, True])
async def test_server_timing_is_numeric_and_only_added_after_authentication(
    database, authenticated
):
    application = FastAPI()
    application.add_middleware(MetricsMiddleware)

    @application.get("/timing-breakdown/{private_id}")
    async def endpoint(request: Request):
        if authenticated:
            request.state.authenticated_actor = object()
        with request_metrics.phase("auth.verify"):
            select_game(database)
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get("/timing-breakdown/private-id")
    assert response.status_code == 200
    if authenticated:
        timing = response.headers["server-timing"]
        assert "auth_verify;dur=" in timing
        assert 'sql_queries;desc="1"' in timing
        assert "private" not in timing and "SELECT" not in timing
    else:
        assert "server-timing" not in response.headers


@pytest.mark.asyncio
async def test_scrape_encoding_is_shared_off_loop_and_survives_one_disconnect(monkeypatch):
    started, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()
    encoders = []

    def encode():
        encoders.append(threading.get_ident())
        started.set()
        assert release.wait(5)
        return b"fixture"

    monkeypatch.setattr(metrics, "_snapshots", ())
    monkeypatch.setattr(metrics, "_encode", encode)
    first = asyncio.create_task(metrics.render())
    second = asyncio.create_task(metrics.render())
    try:
        assert await asyncio.to_thread(started.wait, 5)
        # Scheduling remains responsive while the encoder waits in its worker.
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not second.done()
        release.set()
        assert await second == b"fixture"
        assert len(encoders) == 1 and encoders[0] != loop_thread
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)
