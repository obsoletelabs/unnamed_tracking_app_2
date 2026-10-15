"""Deployment credential precedence keeps each provider's ID and secret together."""

import pytest

from src.core.crypto import encrypt_secret
from src.core.provider_credentials import (
    _ENVIRONMENT_FALLBACKS,
    resolve_metadata_provider_credentials,
)
from src.database.models.app_integration_settings import AppIntegrationSettings


@pytest.fixture
def saved_igdb():
    return AppIntegrationSettings(
        igdb_client_id="saved-client", igdb_client_secret=encrypt_secret("saved-secret")
    )


def test_environment_igdb_credentials_override_saved_pair(monkeypatch, saved_igdb):
    monkeypatch.setitem(_ENVIRONMENT_FALLBACKS, "igdb_client_id", "env-client")
    monkeypatch.setitem(_ENVIRONMENT_FALLBACKS, "igdb_client_secret", "env-secret")
    credentials = resolve_metadata_provider_credentials(None, saved_igdb)
    assert (credentials.igdb_client_id, credentials.igdb_client_secret) == (
        "env-client",
        "env-secret",
    )


def test_saved_igdb_credentials_apply_without_environment(monkeypatch, saved_igdb):
    monkeypatch.setitem(_ENVIRONMENT_FALLBACKS, "igdb_client_id", None)
    monkeypatch.setitem(_ENVIRONMENT_FALLBACKS, "igdb_client_secret", None)
    credentials = resolve_metadata_provider_credentials(None, saved_igdb)
    assert (credentials.igdb_client_id, credentials.igdb_client_secret) == (
        "saved-client",
        "saved-secret",
    )
