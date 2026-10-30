"""Short host-issued access snapshots for one isolated action's broker operations.

Snapshots never authorize host APIs or network egress. They only avoid repeating
an identical local-storage/settings authorization during the same bounded action.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

LOCAL_CAPABILITIES = frozenset({"plugin.storage", "plugin.settings"})
MAX_ACCESS_SECONDS = 5.0


@dataclass
class ActionAccess:
    plugin_id: str
    installation_id: str | None
    user_id: str | None
    capabilities: frozenset[str] = field(default_factory=frozenset)
    expires_at: float = 0.0
    revision: tuple[int, int] = (0, 0)


class RuntimeAccess:
    """Thread-local action access with synchronized host revocation generations."""

    def __init__(self) -> None:
        self._current = threading.local()
        self._lock = threading.RLock()
        self._revisions: dict[tuple[str, str | None], int] = {}

    def begin(self, plugin_id: str, installation_id: str | None, user_id: str | None) -> Any:
        previous = getattr(self._current, "action", None)
        self._current.action = ActionAccess(plugin_id, installation_id, user_id)
        return previous

    def end(self, previous: Any) -> None:
        self._current.action = previous

    def revision(self, plugin_id: str, user_id: str | None) -> tuple[int, int]:
        with self._lock:
            return (
                self._revisions.get((plugin_id, None), 0),
                self._revisions.get((plugin_id, user_id), 0) if user_id else 0,
            )

    def invalidate(self, plugin_id: str, user_id: str | None = None) -> None:
        with self._lock:
            key = (plugin_id, user_id)
            self._revisions[key] = self._revisions.get(key, 0) + 1

    def allows(
        self, plugin_id: str, installation_id: str | None, user_id: str | None,
        capability: str, version: int,
    ) -> bool:
        action = getattr(self._current, "action", None)
        return bool(
            version == 1 and capability in LOCAL_CAPABILITIES
            and action is not None and installation_id is not None and user_id is not None
            and (action.plugin_id, action.installation_id, action.user_id)
                == (plugin_id, installation_id, user_id)
            and action.expires_at > time.monotonic()
            and action.revision == self.revision(plugin_id, user_id)
            and capability in action.capabilities
        )

    def accept(
        self, plugin_id: str, installation_id: str | None, user_id: str | None,
        snapshot: Any, revision: tuple[int, int],
    ) -> None:
        action = getattr(self._current, "action", None)
        if (
            action is None or not isinstance(snapshot, dict)
            or installation_id is None or user_id is None
            or (action.plugin_id, action.installation_id, action.user_id)
                != (plugin_id, installation_id, user_id)
            or snapshot.get("installation_id") != installation_id
            or snapshot.get("user_id") != user_id
            or revision != self.revision(plugin_id, user_id)
        ):
            return
        duration = snapshot.get("expires_in_seconds")
        capabilities = snapshot.get("capabilities")
        if (
            isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not 0 < duration <= MAX_ACCESS_SECONDS
            or not isinstance(capabilities, list)
            or any(not isinstance(item, str) or item not in LOCAL_CAPABILITIES for item in capabilities)
        ):
            return
        action.capabilities = frozenset(capabilities)
        action.expires_at = time.monotonic() + duration
        action.revision = revision
