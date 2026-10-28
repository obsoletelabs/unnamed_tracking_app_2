"""Authenticated HTTP transport from the application host to plugin-runtime."""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from src.core.metrics import (
    PLUGIN_LIFECYCLE_OPERATIONS,
    PLUGIN_RUNTIME_DURATION,
    PLUGIN_RUNTIME_REQUESTS,
    plugin_runtime_operation,
)
from src.plugin_api.manager_state import manager_state
from src.plugin_api.runtime_token import TOKEN_HELP, runtime_token_valid

_ACTION_REQUEST_TIMEOUT = 35.0  # Allow the isolated runner's 30-second wall limit to report.


class PluginRuntimeUnavailable(RuntimeError):
    """Raised when the isolated runtime cannot be reached or used.

    `kind` says why, without exposing a token, a credential-bearing URL or a response body:
    `configuration` (a setting must be fixed; retrying cannot help), `unreachable`,
    `timeout` and `server_error` (the dependency may recover by itself).
    """

    def __init__(self, message: str, *, kind: str = "unreachable") -> None:
        super().__init__(message)
        self.kind = kind

    @property
    def retryable(self) -> bool:
        return self.kind != "configuration"


_NAME_LOOKUP_FAILURES = (
    "name or service not known",
    "temporary failure in name resolution",
    "nodename nor servname",
    "no address associated",
    "getaddrinfo failed",
)


def _origin(url: str) -> str:
    """Scheme, host and port only: never a password, token or path from the configured URL."""
    parts = urlsplit(url)
    host = parts.hostname or "the configured host"
    return f"{parts.scheme or 'http'}://{host}" + (f":{parts.port}" if parts.port else "")


def _unreachable(exc: httpx.HTTPError, base_url: str) -> PluginRuntimeUnavailable:
    where = _origin(base_url)
    if isinstance(exc, httpx.TimeoutException):
        return PluginRuntimeUnavailable(
            f"The plugin runtime at {where} did not answer in time. It may be busy or still "
            "starting; try again in a moment.",
            kind="timeout",
        )
    text = str(exc).lower()
    if any(marker in text for marker in _NAME_LOOKUP_FAILURES):
        return PluginRuntimeUnavailable(
            f"The host of the plugin runtime ({where}) could not be found. Check PLUGIN_RUNTIME_URL "
            "and that the plugin-runtime container is on the same network as the application.",
        )
    return PluginRuntimeUnavailable(
        f"The application could not connect to the plugin runtime at {where}. Check that the "
        "plugin-runtime container is running and that PLUGIN_RUNTIME_URL is correct.",
    )


class PluginRuntimeRequestError(RuntimeError):
    """Raised when the runtime rejects a validly reached request."""

    def __init__(self, message: str, *, status_code: int = 422, detail: str | None = None) -> None:
        """Retain bounded public rejection details; legacy callers default to 422."""
        self.status_code = status_code if 400 <= status_code < 500 else 422
        self.detail = detail or message
        super().__init__(message)


class PluginRuntimeClient:
    """Authenticated client for the isolated plugin runtime and broker."""

    # Public methods mirror the distinct operations of the versioned runtime contract.
    # pylint: disable=too-many-public-methods

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        resolved_url = base_url or os.getenv("PLUGIN_RUNTIME_URL") or "http://plugin-runtime:8000"
        resolved_token = token or os.getenv("PLUGIN_RUNTIME_TOKEN") or ""
        self.base_url = resolved_url.rstrip("/")
        self.token = resolved_token

    def _headers(self) -> dict[str, str]:
        """Authenticate transport and mirror the host administrator's isolation decision."""
        if not runtime_token_valid(self.token):
            raise PluginRuntimeUnavailable(
                TOKEN_HELP,
                kind="configuration",
            )
        approved = manager_state().settings().get("reduced_isolation_acknowledged") is True
        headers = {
            "X-Plugin-Runtime-Token": self.token,
            "X-Plugin-Reduced-Isolation-Acknowledged": str(approved).lower(),
        }
        gateway_url = os.getenv("PLUGIN_GATEWAY_URL", "").strip().rstrip("/")
        if gateway_url:
            headers["X-Plugin-Gateway-URL"] = gateway_url
        return headers

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Send a runtime operation with current credentials and policy acknowledgement."""
        operation = plugin_runtime_operation(method, path)
        started = time.monotonic()
        outcome = "error"
        lifecycle_operation = (
            "start"
            if method == "POST" and path.endswith("/start")
            else "stop"
            if method == "POST" and path.endswith("/stop")
            else "remove"
            if method == "DELETE" and path.startswith("/plugins/")
            else "rollback"
            if method == "PUT"
            and path.endswith("/installation")
            and kwargs.get("json", {}).get("commit") is False
            else None
        )
        try:
            result = await self._send(method, path, **kwargs)
            outcome = "ok"
            return result
        except PluginRuntimeRequestError:
            outcome = "rejected"
            raise
        except PluginRuntimeUnavailable as exc:
            outcome = exc.kind
            raise
        finally:
            PLUGIN_RUNTIME_REQUESTS.labels(operation, outcome).inc()
            PLUGIN_RUNTIME_DURATION.labels(operation).observe(time.monotonic() - started)
            if lifecycle_operation is not None:
                PLUGIN_LIFECYCLE_OPERATIONS.labels(lifecycle_operation, outcome).inc()

    async def _send(self, method: str, path: str, **kwargs: Any) -> Any:
        """Send one request and translate transport and status failures."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers={**self._headers(), **kwargs.pop("headers", {})},
                    **kwargs,
                )
        except httpx.HTTPError as exc:
            raise _unreachable(exc, self.base_url) from exc
        if response.status_code >= 500:
            try:
                body = response.json()
                detail = body.get("detail") if isinstance(body, dict) else None
            except ValueError:
                detail = None
            raise PluginRuntimeUnavailable(
                detail[:1024]
                if isinstance(detail, str)
                else f"The plugin runtime reported an internal error ({response.status_code}). "
                "Check the plugin-runtime logs, then try again.",
                kind="server_error",
            )
        if response.status_code >= 400:
            try:
                body = response.json()
                detail = body.get("detail") if isinstance(body, dict) else None
            except ValueError:
                detail = None
            raise PluginRuntimeRequestError(
                f"plugin runtime rejected the request ({response.status_code}): {response.text[:1024]}",
                status_code=response.status_code,
                detail=detail[:1024] if isinstance(detail, str) else None,
            )
        return response.json() if response.content else None

    async def health(self) -> dict[str, Any]:
        """Read runtime availability and the actual isolation mode."""
        return await self._request("GET", "/health")

    async def run_scheduled_task(
        self,
        plugin_id: str,
        installation_id: str,
        task_id: str,
        trigger: str,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch an installation-bound task without accepting arbitrary actions or users."""
        result = await self._request(
            "POST",
            f"/plugins/{quote(plugin_id, safe='')}/tasks/{quote(task_id, safe='')}",
            json={
                "installation_id": installation_id,
                "trigger": trigger,
                **({"user_id": user_id} if user_id is not None else {}),
            },
            timeout=_ACTION_REQUEST_TIMEOUT,
        )
        if not isinstance(result, dict):
            raise PluginRuntimeRequestError("plugin task returned an invalid response")
        return result

    async def plugins(self) -> list[dict[str, Any]]:
        """List runtime installations and their lifecycle state."""
        return await self._request("GET", "/plugins")

    async def plugin_ui(self, plugin_id: str) -> dict[str, Any]:
        """Read the package's declared UI document."""
        return await self._request("GET", f"/plugins/{plugin_id}/ui")

    async def plugin_state(self, plugin_id: str) -> dict[str, Any]:
        """Revalidate one installation's current lifecycle and payload integrity."""
        return await self._request("GET", f"/plugins/{quote(plugin_id, safe='')}/state")

    async def logs(self, plugin_id: str) -> dict[str, Any]:
        """Read the bounded structured event buffer for one plugin."""
        return await self._request("GET", f"/plugins/{plugin_id}/logs")

    async def frontend_asset(self, plugin_id: str, path: str) -> bytes:
        """Retrieve a verified sandboxed frontend asset."""
        data = await self._request("GET", f"/plugins/{plugin_id}/frontend/{path}")
        return base64.b64decode(str(data["content"]))

    async def pwa_asset(self, plugin_id: str, path: str) -> bytes:
        """Retrieve a verified PWA asset from its declared package path."""
        data = await self._request("GET", f"/plugins/{quote(plugin_id, safe='')}/pwa/{path}")
        return base64.b64decode(str(data["content"]), validate=True)

    async def native_frontend_asset(self, plugin_id: str, path: str) -> bytes:
        """Retrieve a verified native frontend asset."""
        data = await self._request("GET", f"/plugins/{plugin_id}/native-frontend/{path}")
        return base64.b64decode(str(data["content"]))

    async def start(self, plugin_id: str, user_id: str | None = None) -> None:
        """Start a plugin with its authenticated host user context."""
        payload = {"user_id": user_id} if user_id is not None else None
        await self._request("POST", f"/plugins/{plugin_id}/start", json=payload)

    async def stop(self, plugin_id: str) -> None:
        """Disable the installation and stop its worker."""
        await self._request("POST", f"/plugins/{plugin_id}/disable")

    async def stop_runtime(self, plugin_id: str) -> None:
        """Stop the worker while retaining the enabled installation."""
        await self._request("POST", f"/plugins/{plugin_id}/stop")

    async def finish_activation(self, plugin_id: str, operation_id: str, *, commit: bool) -> None:
        """Commit an activation or restore its previous execution state."""
        await self._request(
            "PUT",
            f"/plugins/{quote(plugin_id, safe='')}/activation",
            json={"operation_id": operation_id, "commit": commit},
        )

    async def package_archive(self, plugin_id: str, history_id: str | None = None) -> bytes:
        """Read the active package or a retained historical package."""
        path = f"/plugins/{quote(plugin_id, safe='')}/archive"
        if history_id:
            path += f"/{quote(history_id, safe='')}"
        data = await self._request("GET", path)
        return base64.b64decode(data["package"], validate=True)

    async def prune_history(
        self, plugin_id: str, retain: int, history_id: str | None = None
    ) -> None:
        """Apply retention or remove one reviewed package snapshot."""
        await self._request(
            "PUT",
            f"/plugins/{quote(plugin_id, safe='')}/history",
            json={"retain": retain, "history_id": history_id},
        )

    async def purge_data(self, plugin_id: str) -> None:
        """Remove plugin-owned persistent data through the runtime broker."""
        await self._request("POST", f"/plugins/{quote(plugin_id, safe='')}/purge")

    async def delete(self, plugin_id: str) -> None:
        """Uninstall the package and its runtime-owned data."""
        await self._request("DELETE", f"/plugins/{plugin_id}")

    async def save_secret(self, plugin_id: str, key: str, value: str) -> None:
        """Write one authorized plugin secret through the storage broker."""
        await self._request(
            "POST", f"/plugins/{plugin_id}/storage", json={"key": key, "value": value}
        )

    async def install_package(
        self,
        package: bytes,
        filename: str,
        *,
        installation_id: str,
        replace: bool = False,
        source_metadata: dict[str, Any] | None = None,
        trust_metadata: dict[str, Any] | None = None,
        operation_id: str | None = None,
        expected_version: str | None = None,
    ) -> dict[str, Any]:
        """Upload verified package bytes and activation metadata."""
        return await self._request(
            "PUT",
            "/plugins/install/prepare" if operation_id else "/plugins/install",
            content=package,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Plugin-Package-Name": filename,
                "X-Plugin-Installation-ID": installation_id,
                "X-Plugin-Replace": "true" if replace else "false",
                "X-Plugin-Operation-ID": operation_id or "",
                "X-Plugin-Expected-Version": expected_version or "",
                "X-Plugin-Source": base64.urlsafe_b64encode(
                    json.dumps(source_metadata or {}, separators=(",", ":")).encode("utf-8")
                ).decode("ascii"),
                "X-Plugin-Trust": base64.urlsafe_b64encode(
                    json.dumps(trust_metadata or {}, separators=(",", ":")).encode("utf-8")
                ).decode("ascii"),
            },
        )

    async def finish_installation(self, plugin_id: str, operation_id: str, *, commit: bool) -> None:
        """Publish a prepared package after grants commit, or restore its predecessor."""
        await self._request(
            "PUT",
            f"/plugins/{quote(plugin_id, safe='')}/installation",
            json={"operation_id": operation_id, "commit": commit},
        )

    async def plugin_health(self, plugin_id: str) -> bool:
        """Read the supervised worker's affirmative readiness result."""
        data = await self._request("GET", f"/plugins/{plugin_id}/health")
        return bool(data.get("healthy"))

    async def save_settings(self, plugin_id: str, values: dict[str, Any]) -> None:
        """Persist the plugin's ordinary configuration."""
        await self._request("PUT", f"/plugins/{plugin_id}/settings", json=values)

    async def read_settings(self, plugin_id: str) -> dict[str, Any]:
        """Read persisted ordinary configuration; the runtime omits secret fields."""
        data = await self._request("GET", f"/plugins/{plugin_id}/settings")
        values = data.get("values") if isinstance(data, dict) else None
        if not isinstance(values, dict):
            raise PluginRuntimeRequestError("plugin runtime returned invalid settings")
        return values

    async def action(
        self, plugin_id: str, action_id: str, values: dict[str, Any], *, user_id: str | None = None
    ) -> dict[str, Any]:
        """Execute one declared action within the isolated runner's wall limit."""
        return await self._request(
            "POST",
            f"/plugins/{quote(plugin_id, safe='')}/actions/{quote(action_id, safe='')}",
            json={"values": values, "user_id": user_id},
            timeout=_ACTION_REQUEST_TIMEOUT,
        )

    async def route(
        self,
        plugin_id: str,
        route_id: str,
        request: dict[str, Any],
        *,
        user_id: str,
    ) -> dict[str, Any]:
        """Execute one declared backend route as the authenticated request user."""

        result = await self._request(
            "POST",
            f"/plugins/{quote(plugin_id, safe='')}/routes/{quote(route_id, safe='')}",
            json={"request": request, "user_id": user_id},
            timeout=_ACTION_REQUEST_TIMEOUT,
        )
        if not isinstance(result, dict):
            raise PluginRuntimeRequestError("plugin backend route returned an invalid response")
        return result

    async def public_app_action(
        self,
        plugin_id: str,
        values: dict[str, Any],
        *,
        installation_id: str,
        actor_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Send verified external context through the protected runtime transport."""
        return await self._request(
            "POST",
            f"/plugins/{quote(plugin_id, safe='')}/public-app-actions",
            json={
                "values": values,
                "installation_id": installation_id,
                "actor_id": actor_id,
                "context": context,
            },
            timeout=_ACTION_REQUEST_TIMEOUT,
        )

    async def notification_delivery(
        self,
        plugin_id: str,
        action_id: str,
        values: dict[str, Any],
        *,
        user_id: str,
        installation_id: str,
        attempt_id: str,
        server_revision: int | None = None,
    ) -> dict[str, Any]:
        """Private host transport; authority is outside plugin/UI action values."""
        return await self._request(
            "POST",
            f"/plugins/{quote(plugin_id, safe='')}/notification-deliveries/{quote(action_id, safe='')}",
            json={
                "values": values,
                "user_id": user_id,
                "installation_id": installation_id,
                "attempt_id": attempt_id,
                **({"server_revision": server_revision} if server_revision is not None else {}),
            },
            timeout=_ACTION_REQUEST_TIMEOUT,
        )

    async def notification_story_action(
        self,
        plugin_id: str,
        action_id: str,
        values: dict[str, Any],
        *,
        user_id: str,
        installation_id: str,
        story_id: str,
        revision: int,
    ) -> dict[str, Any]:
        """Host callback for a declared story action; authority was rechecked by the host."""
        return await self._request(
            "POST",
            f"/plugins/{quote(plugin_id, safe='')}/notification-story-actions/{quote(action_id, safe='')}",
            json={
                "values": values,
                "user_id": user_id,
                "installation_id": installation_id,
                "story_id": story_id,
                "revision": revision,
            },
            timeout=_ACTION_REQUEST_TIMEOUT,
        )

    async def notification_provider_action(
        self,
        plugin_id: str,
        action_id: str,
        configuration: dict[str, Any],
        *,
        user_id: str,
        installation_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Private enrollment authority; no caller-selected context reaches public actions."""
        return await self._request(
            "POST",
            f"/plugins/{quote(plugin_id, safe='')}/notification-provider-actions/{quote(action_id, safe='')}",
            json={
                "configuration": configuration,
                "user_id": user_id,
                "installation_id": installation_id,
                "context": context,
            },
            timeout=_ACTION_REQUEST_TIMEOUT,
        )

    async def notification_layout(
        self,
        plugin_id: str,
        action_id: str,
        values: dict[str, Any],
        *,
        user_id: str,
        installation_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        """The renderer never receives transport credentials or legacy egress authority."""
        return await self._request(
            "POST",
            f"/plugins/{quote(plugin_id, safe='')}/notification-layouts/{quote(action_id, safe='')}",
            json={
                "values": values,
                "user_id": user_id,
                "installation_id": installation_id,
                "attempt_id": attempt_id,
            },
            timeout=_ACTION_REQUEST_TIMEOUT,
        )

    async def notification_transport(
        self,
        plugin_id: str,
        webhook: str,
        payload: dict[str, Any],
        *,
        user_id: str,
        installation_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        """Host-only broker operation; this envelope is never executed in a plugin worker."""
        return await self._request(
            "POST",
            f"/plugins/{quote(plugin_id, safe='')}/notification-transports/discord",
            json={
                "webhook": webhook,
                "payload": payload,
                "user_id": user_id,
                "installation_id": installation_id,
                "attempt_id": attempt_id,
            },
            timeout=_ACTION_REQUEST_TIMEOUT,
        )
