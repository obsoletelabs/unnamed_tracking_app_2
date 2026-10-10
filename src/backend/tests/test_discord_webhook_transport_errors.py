"""Regression tests for permanent protected Discord transport failures."""

from src.features.notification_providers.plugin import _protected_transport_failure
from src.plugin_api.runtime_client import PluginRuntimeRequestError, PluginRuntimeUnavailable


def test_disabled_discord_egress_is_reported_as_permanent_configuration_failure() -> None:
    error = PluginRuntimeRequestError(
        "runtime rejected protected transport",
        status_code=422,
        detail="Discord egress is disabled in this runtime",
    )
    result = _protected_transport_failure(error)
    assert result.success is False
    assert result.retryable is False
    assert result.error == "discord_egress_disabled"


def test_other_permanent_transport_rejections_are_not_retried() -> None:
    error = PluginRuntimeRequestError(
        "runtime rejected protected transport",
        status_code=422,
        detail="Protected notification transport is invalid",
    )
    result = _protected_transport_failure(error)
    assert result.success is False
    assert result.retryable is False
    assert result.error == "provider_transport_rejected"


def test_runtime_outage_remains_retryable() -> None:
    result = _protected_transport_failure(PluginRuntimeUnavailable("runtime unavailable"))
    assert result.success is False
    assert result.retryable is True
    assert result.error == "provider_transport_unavailable"
