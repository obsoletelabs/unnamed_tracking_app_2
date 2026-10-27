"""#263: plugin runtime failures name the failed part and a safe recovery, and say whether retrying can help."""

import httpx
import pytest

from src.plugin_api import runtime_client
from src.plugin_api.runtime_client import PluginRuntimeClient, PluginRuntimeUnavailable
from src.plugin_api.runtime_token import runtime_token_valid

REAL_CLIENT = httpx.AsyncClient  # patched per call below; keep the original
TOKEN = "t" * 40
SECRET_URL = "http://user:hunter2@runtime-host.internal:8123/private/path?token=abc"


def client_with(handler, *, url=SECRET_URL, token=TOKEN, monkeypatch=None):
    real = REAL_CLIENT
    monkeypatch.setattr(
        runtime_client.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    return PluginRuntimeClient(base_url=url, token=token)


def raising(exc):
    def handler(request):
        raise exc

    return handler


async def failure(monkeypatch, handler, **kw):
    client = client_with(handler, monkeypatch=monkeypatch, **kw)
    with pytest.raises(PluginRuntimeUnavailable) as caught:
        await client.health()
    return caught.value


@pytest.fixture(autouse=True)
def _quiet_manager(monkeypatch, tmp_path):
    from src.plugin_api.manager_state import ManagerState

    monkeypatch.setattr(
        runtime_client, "manager_state", lambda: ManagerState(tmp_path / "manager.json")
    )


async def test_a_missing_token_is_a_configuration_error_that_names_the_setting(monkeypatch):
    error = await failure(monkeypatch, lambda r: httpx.Response(200, json={}), token="short")
    assert error.kind == "configuration" and error.retryable is False
    assert (
        "PLUGIN_RUNTIME_TOKEN" in str(error)
        and "32 characters" in str(error)
        and "restart" in str(error)
    )


@pytest.mark.parametrize("token", ["x" * 31, "x" * 257, "x" * 32 + "é", "x" * 32 + ":"])
def test_plugin_runtime_token_rejects_header_or_env_unsafe_values(token):
    assert not runtime_token_valid(token)


def test_plugin_runtime_token_accepts_generated_hex_value():
    assert runtime_token_valid("a9" * 32)


@pytest.mark.parametrize(
    ("exc", "kind", "phrase"),
    [
        (httpx.ConnectTimeout("slow"), "timeout", "did not answer in time"),
        (httpx.ReadTimeout("slow"), "timeout", "try again in a moment"),
        (
            httpx.ConnectError("[Errno -2] Name or service not known"),
            "unreachable",
            "could not be found",
        ),
        (
            httpx.ConnectError("Temporary failure in name resolution"),
            "unreachable",
            "PLUGIN_RUNTIME_URL",
        ),
        (httpx.ConnectError("[Errno 111] Connection refused"), "unreachable", "could not connect"),
    ],
)
async def test_network_failures_are_told_apart(monkeypatch, exc, kind, phrase):
    error = await failure(monkeypatch, raising(exc))
    assert error.kind == kind and error.retryable is True
    assert phrase in str(error)
    assert "runtime-host.internal:8123" in str(error)  # the host the operator configured


async def test_messages_never_carry_credentials_or_urls_with_secrets(monkeypatch):
    for exc in (httpx.ConnectError("refused for user:hunter2@host"), httpx.ConnectTimeout("t")):
        text = str(await failure(monkeypatch, raising(exc)))
        for secret in ("hunter2", "abc", "private/path", TOKEN, "user:"):
            assert secret not in text


async def test_a_server_error_without_detail_is_retryable_and_points_at_the_logs(monkeypatch):
    error = await failure(
        monkeypatch, lambda r: httpx.Response(502, text="<html>bad gateway</html>")
    )
    assert error.kind == "server_error" and error.retryable is True
    assert "502" in str(error) and "plugin-runtime logs" in str(error)
    assert "bad gateway" not in str(error)  # the response body is never relayed
    detailed = await failure(
        monkeypatch, lambda r: httpx.Response(503, json={"detail": "Bubblewrap is unavailable."})
    )
    assert str(detailed) == "Bubblewrap is unavailable."


async def test_a_healthy_runtime_still_answers(monkeypatch):
    client = client_with(
        lambda r: httpx.Response(200, json={"status": "ok"}), monkeypatch=monkeypatch
    )
    assert await client.health() == {"status": "ok"}


def test_origin_hides_everything_but_scheme_host_and_port():
    assert runtime_client._origin(SECRET_URL) == "http://runtime-host.internal:8123"
    assert runtime_client._origin("") == "http://the configured host"
