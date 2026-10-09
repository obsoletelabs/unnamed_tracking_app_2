"""Production Nginx configuration, candidate validation and reversible graceful activation."""

from __future__ import annotations

import argparse
import asyncio
import http.client
import logging
import os
import re
import shlex
import ssl
import subprocess
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, TypeAdapter, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.env_handler import EnvConfigHandler
from src.core.real_ip import get_effective_real_ip_config
from src.database.models.app_integration_settings import AppIntegrationSettings
from src.database.session import SessionLocal, engine

logger = logging.getLogger(__name__)
NGINX_TLS_FIELDS = {
    "NGINX_TLS_ENABLED": "nginx_tls_enabled",
    "NGINX_TLS_REDIRECT_HTTP": "nginx_tls_redirect_http",
    "NGINX_TLS_CERTIFICATE": "nginx_tls_certificate",
    "NGINX_TLS_PRIVATE_KEY": "nginx_tls_private_key",
}
_RENDER = Path("/usr/local/bin/render-production-nginx")
_ACTIVE = Path("/etc/nginx/nginx.conf")
_TEMPLATES = Path("/etc/nginx")
_ACTIVATION_LOCK = asyncio.Lock()


def validate_tls_path(value: str) -> str:
    """Allow mounted filesystem paths, never Nginx directives or shell/sed syntax."""
    value = value.strip()
    if value and (len(value) > 512 or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value)):
        raise ValueError(
            "TLS file paths must be absolute and contain letters, digits, /, _, . or -"
        )
    return value


def validate_nginx_value(attribute: str, value: object) -> bool | str | None:
    if value is None:
        return None
    if attribute in {"nginx_tls_enabled", "nginx_tls_redirect_http"}:
        return TypeAdapter(bool).validate_python(value)
    if not isinstance(value, str):
        raise ValueError("TLS file paths must be strings")
    return validate_tls_path(value)


class NginxConfiguration(BaseModel):
    enabled: bool = False
    redirect_http: bool = False
    certificate: str = ""
    private_key: str = ""
    header: str
    trusted_proxies: str

    @field_validator("certificate", "private_key")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_tls_path(value)

    @model_validator(mode="after")
    def validate_pair(self) -> NginxConfiguration:
        if bool(self.certificate) != bool(self.private_key):
            raise ValueError("Supply the TLS certificate and private key paths together")
        if self.redirect_http and not self.enabled:
            raise ValueError("HTTP redirection requires TLS to be enabled")
        return self

    def environment(self) -> dict[str, str]:
        return {
            "NGINX_TLS_ENABLED": str(self.enabled).lower(),
            "NGINX_TLS_REDIRECT_HTTP": str(self.redirect_http).lower(),
            "NGINX_TLS_CERTIFICATE": self.certificate,
            "NGINX_TLS_PRIVATE_KEY": self.private_key,
            "NGINX_REALIP_HEADER": self.header,
            "NGINX_REALIP_TRUSTED_PROXIES": self.trusted_proxies,
        }


def effective_nginx_configuration(
    app: AppIntegrationSettings, handler: EnvConfigHandler
) -> NginxConfiguration:
    values = {}
    for name, attribute in NGINX_TLS_FIELDS.items():
        values[attribute.removeprefix("nginx_tls_")] = (
            handler.get(name) if handler.has(name) else getattr(app, attribute)
        )
    return NginxConfiguration(
        **{key: value for key, value in values.items() if value is not None},
        **get_effective_real_ip_config(
            handler, app.nginx_realip_header, app.nginx_realip_trusted_proxies
        ),
    )


def nginx_runtime_available() -> bool:
    return _RENDER.is_file() and _ACTIVE.is_file()


async def lock_nginx_configuration(
    db: AsyncSession, app: AppIntegrationSettings, handler: EnvConfigHandler
) -> NginxConfiguration:
    """Serialize setup/settings writers through the existing deployment singleton."""
    await db.scalar(
        select(AppIntegrationSettings.id)
        .where(AppIntegrationSettings.id == app.id)
        .with_for_update()
    )
    await db.refresh(app)
    return effective_nginx_configuration(app, handler)


class NginxActivationError(RuntimeError):
    """An invalid candidate must not replace the working configuration."""


def _run(command: list[str], environment: dict[str, str] | None = None) -> None:
    try:
        subprocess.run(
            command, env=environment, check=True, capture_output=True, text=True, timeout=20
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.error("Nginx configuration command failed: %s", getattr(exc, "stderr", str(exc)))
        raise NginxActivationError(
            "Nginx rejected the configuration. Check mounted certificate paths and server logs; "
            "the previous configuration has been retained."
        ) from exc


def _write_active(contents: bytes) -> None:
    with tempfile.NamedTemporaryFile(
        dir=_ACTIVE.parent, prefix=".nginx-restore-", delete=False
    ) as out:
        path = Path(out.name)
        out.write(contents)
    try:
        path.chmod(_ACTIVE.stat().st_mode)
        os.replace(path, _ACTIVE)
    finally:
        path.unlink(missing_ok=True)


def _restore(previous: bytes) -> None:
    _write_active(previous)
    _run(["/usr/sbin/nginx", "-s", "reload"])
    marker = re.search(
        rb'location = /_startup/nginx-revision \{ return 200 "([a-f0-9]{32})";', previous
    )
    if marker:
        _confirm_active(b"listen 443 ssl;" in previous, marker[1].decode())


def _confirm_active(enabled: bool, revision: str) -> None:
    """A reload signal succeeding does not prove that new workers accepted the config."""
    deadline = time.monotonic() + 6
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # Only the container's loopback probe may accept the existing self-signed certificate.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    while time.monotonic() < deadline:
        connection = (
            http.client.HTTPSConnection("127.0.0.1", 443, timeout=0.5, context=context)
            if enabled
            else http.client.HTTPConnection("127.0.0.1", 80, timeout=0.5)
        )
        try:
            connection.request("GET", "/_startup/nginx-revision")
            response = connection.getresponse()
            if response.status == 200 and response.read(64).decode() == revision:
                return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(0.1)
    raise NginxActivationError(
        "Nginx did not activate the candidate; the previous configuration was restored."
    )


def _activate(configuration: NginxConfiguration) -> bytes:
    previous = _ACTIVE.read_bytes()
    template = "readytlsredirect.conf" if configuration.redirect_http else "readytls.conf"
    if not configuration.enabled:
        template = "ready.conf"
    with tempfile.NamedTemporaryFile(
        dir=_ACTIVE.parent, prefix=".nginx-candidate-", delete=False
    ) as out:
        candidate = Path(out.name)
    installed = False
    revision = uuid4().hex
    try:
        _run(
            [str(_RENDER), str(_TEMPLATES / template), str(candidate)],
            {**os.environ, **configuration.environment()},
        )
        rendered = candidate.read_text(encoding="utf-8")
        server = "    server {"
        if server not in rendered:
            raise NginxActivationError("The production Nginx template has no server block")
        probe = f'\n        location = /_startup/nginx-revision {{ return 200 "{revision}"; }}'
        candidate.write_text(rendered.replace(server, server + probe), encoding="utf-8")
        _run(["/usr/sbin/nginx", "-t", "-c", str(candidate)])
        candidate.chmod(_ACTIVE.stat().st_mode)
        os.replace(candidate, _ACTIVE)
        installed = True
        _run(["/usr/sbin/nginx", "-s", "reload"])
        _confirm_active(configuration.enabled, revision)
    except BaseException:
        if installed:
            _restore(previous)
        raise
    finally:
        candidate.unlink(missing_ok=True)
    return previous


@asynccontextmanager
async def apply_nginx_change(configuration: NginxConfiguration | None) -> AsyncIterator[bool]:
    """Keep activation and its database commit serialized; roll back both on failure."""
    if configuration is None or not nginx_runtime_available():
        yield False
        return
    async with _ACTIVATION_LOCK:
        activation = asyncio.create_task(asyncio.to_thread(_activate, configuration))
        cancelled = False
        while True:
            try:
                previous = await asyncio.shield(activation)
                break
            except asyncio.CancelledError:
                # A disconnected request must not abandon a thread changing the live file.
                if activation.cancelled():
                    raise
                cancelled = True
        try:
            if cancelled:
                raise asyncio.CancelledError
            yield True
        except BaseException:
            restoration = asyncio.create_task(asyncio.to_thread(_restore, previous))
            while True:
                try:
                    await asyncio.shield(restoration)
                    break
                except asyncio.CancelledError:
                    if restoration.cancelled():
                        raise
            raise


async def _startup_environment(output: Path | None = None) -> None:
    # The existing production entrypoint invokes this after migrations/backend health.
    async with SessionLocal() as db:
        row = await db.scalar(select(AppIntegrationSettings).limit(1))
        configuration = effective_nginx_configuration(
            row or AppIntegrationSettings(), EnvConfigHandler()
        )
    await engine.dispose()
    exports = "".join(
        f"export {name}={shlex.quote(value)}\n"
        for name, value in configuration.environment().items()
    )
    if output is None:
        print(exports, end="")
    else:
        output.write_text(exports, encoding="utf-8")
        output.chmod(0o600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    asyncio.run(_startup_environment(parser.parse_args().output))
