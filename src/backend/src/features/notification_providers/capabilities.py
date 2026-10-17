"""Effective provider capabilities, discovered from the live adapter at every dispatch.

A declaration is only a request to present richer content. The host narrows it to what the
adapter can actually do (an edit declaration without an ``edit`` implementation is ignored) and
records why in ``notes`` so the downgrade is visible in diagnostics. Declarations never carry
trust, consent or grants; those are always rechecked separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.features.notification_contract import (
    BUILTIN_PROFILES,
    TRANSPORT_PROFILES,
    ProviderCapabilityProfile,
)
from src.plugin_api.notification_story_contracts import NotificationProviderCapabilities


@dataclass(frozen=True)
class ResolvedProfile:
    renderer: str
    capabilities: NotificationProviderCapabilities
    critical_supported: bool
    notes: tuple[str, ...] = ()

    @property
    def canonical(self) -> bool:
        return self.renderer == "canonical"

    @property
    def can_edit(self) -> bool:
        return self.capabilities.message_updates in {"edit", "replace"}


def _base(provider_id: str, transport: str | None) -> ProviderCapabilityProfile:
    builtin = BUILTIN_PROFILES.get(provider_id)
    if builtin is not None:
        return builtin
    return TRANSPORT_PROFILES.get(transport or "legacy", TRANSPORT_PROFILES["legacy"])


def resolve_profile(provider: Any) -> ResolvedProfile:
    """Profile of one live adapter; unknown or undeclared adapters keep the legacy baseline."""
    provider_id = str(getattr(provider, "id", ""))
    transport = getattr(provider, "transport", None)
    base = _base(provider_id, transport)
    declared = getattr(provider, "capabilities", None)
    notes: list[str] = []
    critical = bool(getattr(provider, "critical_supported", False)) or base.critical_supported
    if not isinstance(declared, NotificationProviderCapabilities) or (
        provider_id in BUILTIN_PROFILES or transport not in {None, "plugin"}
    ):
        if declared is not None:
            notes.append("declaration_ignored")
        return ResolvedProfile(base.renderer, base.capabilities, critical, tuple(notes))
    capabilities = declared
    updates: dict[str, Any] = {}
    if capabilities.message_updates != "none" and not callable(getattr(provider, "edit", None)):
        updates["message_updates"] = "none"
        notes.append("edit_not_implemented")
    if updates:
        capabilities = capabilities.model_copy(update=updates)
    return ResolvedProfile("canonical", capabilities, critical, tuple(notes))
