"""Live configuration failure isolation and ENV precedence without Docker access."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.core import nginx_configuration as nginx
from src.core.env_handler import EnvConfigHandler
from src.database.models.app_integration_settings import AppIntegrationSettings


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    active = tmp_path / "nginx.conf"
    active.write_text("original working configuration")
    renderer = tmp_path / "render"
    renderer.touch()
    monkeypatch.setattr(nginx, "_ACTIVE", active)
    monkeypatch.setattr(nginx, "_RENDER", renderer)
    monkeypatch.setattr(nginx, "_confirm_active", Mock())
    calls = []

    def run(command, environment=None):
        calls.append(command)
        if command[0] == str(renderer):
            assert environment["NGINX_TLS_ENABLED"] == "true"
            from pathlib import Path

            Path(command[-1]).write_text("http {\n    server {\n        listen 443 ssl;\n    }\n}")

    monkeypatch.setattr(nginx, "_run", run)
    return active, calls, run


def configuration():
    return nginx.NginxConfiguration(
        enabled=True, header="X-Forwarded-For", trusted_proxies="127.0.0.1/32"
    )


async def test_activation_validates_before_replacing_then_confirms_new_workers(runtime):
    active, calls, _ = runtime
    async with nginx.apply_nginx_change(configuration()) as applied:
        assert applied
        assert "nginx-revision" in active.read_text()
    assert [command[1] for command in calls[1:]] == ["-t", "-s"]
    nginx._confirm_active.assert_called_once()


async def test_invalid_candidate_never_replaces_working_config(runtime, monkeypatch):
    active, _, run = runtime

    def invalid(command, environment=None):
        if "-t" in command:
            raise nginx.NginxActivationError("invalid certificate")
        run(command, environment)

    monkeypatch.setattr(nginx, "_run", invalid)
    with pytest.raises(nginx.NginxActivationError):
        async with nginx.apply_nginx_change(configuration()):
            raise AssertionError("Invalid candidate reached persistence")
    assert active.read_text() == "original working configuration"
    assert not list(active.parent.glob(".nginx-candidate-*"))


async def test_failed_reload_restores_previous_config(runtime, monkeypatch):
    active, _, run = runtime
    reloads = 0

    def fail_once(command, environment=None):
        nonlocal reloads
        if "reload" in command:
            reloads += 1
            if reloads == 1:
                raise nginx.NginxActivationError("reload failed")
        run(command, environment)

    monkeypatch.setattr(nginx, "_run", fail_once)
    with pytest.raises(nginx.NginxActivationError):
        async with nginx.apply_nginx_change(configuration()):
            pass
    assert reloads == 2
    assert active.read_text() == "original working configuration"


async def test_persistence_failure_restores_live_config(runtime):
    active, calls, _ = runtime
    with pytest.raises(RuntimeError, match="commit failed"):
        async with nginx.apply_nginx_change(configuration()):
            raise RuntimeError("commit failed")
    assert active.read_text() == "original working configuration"
    assert sum("reload" in command for command in calls) == 2


async def test_cancelled_request_waits_for_activation_and_restores_config(runtime, monkeypatch):
    active, _, _ = runtime
    started = threading.Event()
    release = threading.Event()
    activate = nginx._activate

    def delayed(configuration):
        started.set()
        assert release.wait(5)
        return activate(configuration)

    monkeypatch.setattr(nginx, "_activate", delayed)

    async def request():
        async with nginx.apply_nginx_change(configuration()):
            raise AssertionError("Cancelled request reached persistence")

    task = asyncio.create_task(request())
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active.read_text() == "original working configuration"


async def test_worker_activation_failure_restores_config(runtime, monkeypatch):
    active, _, _ = runtime
    monkeypatch.setattr(
        nginx,
        "_confirm_active",
        Mock(side_effect=nginx.NginxActivationError("workers rejected new listener")),
    )
    with pytest.raises(nginx.NginxActivationError):
        async with nginx.apply_nginx_change(configuration()):
            raise AssertionError("Unconfirmed workers reached persistence")
    assert active.read_text() == "original working configuration"


def test_environment_tls_values_override_saved_values():
    row = AppIntegrationSettings(nginx_tls_enabled=False, nginx_tls_redirect_http=False)
    resolved = nginx.effective_nginx_configuration(
        row, EnvConfigHandler({"NGINX_TLS_ENABLED": "true", "NGINX_TLS_REDIRECT_HTTP": "true"})
    )
    assert resolved.enabled and resolved.redirect_http


async def test_startup_export_file_excludes_database_debug_output(tmp_path, monkeypatch):
    db = AsyncMock()
    db.__aenter__.return_value = db

    async def logged_query(_query):
        print("DEBUG SQL: SELECT configuration (not shell code)")
        return AppIntegrationSettings(nginx_tls_enabled=True, nginx_tls_redirect_http=True)

    db.scalar.side_effect = logged_query
    monkeypatch.setattr(nginx, "SessionLocal", lambda: db)
    monkeypatch.setattr(nginx, "engine", SimpleNamespace(dispose=AsyncMock()))
    monkeypatch.setattr(nginx, "EnvConfigHandler", lambda: EnvConfigHandler({}))
    output = tmp_path / "nginx.env"
    await nginx._startup_environment(output)
    contents = output.read_text()
    assert "DEBUG SQL" not in contents
    assert "export NGINX_TLS_ENABLED=true\n" in contents
    assert "export NGINX_TLS_REDIRECT_HTTP=true\n" in contents
    assert len(contents.splitlines()) == 6
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "value",
    ["relative/key.pem", "/tmp/key;include.conf", "/tmp/$(secret)", "/tmp/cert#sed", "/tmp/a\nb"],
)
def test_tls_paths_cannot_inject_shell_or_nginx_syntax(value):
    with pytest.raises(ValueError):
        nginx.validate_tls_path(value)
