"""Deployment updates enforce locks and persist secrets through the public routes."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.api.routes import auth_oidc, deployment_settings, setup
from src.core.auth import get_current_admin
from src.core.crypto import decrypt_secret, encrypt_secret
from src.core.env_handler import EnvConfigHandler
from src.core.nginx_configuration import NginxActivationError
from src.database.models.app_integration_settings import AppIntegrationSettings
from src.database.models.oidc_settings import OidcSettings
from src.database.models.user import User
from src.database.session import get_db


class ConfigurationDb:
    """Use actual singleton rows without requiring PostgreSQL-specific application tables."""

    def __init__(self, session):
        self.session = session

    async def scalar(self, statement):
        return self.session.scalar(statement)

    def add(self, row):
        self.session.add(row)

    async def flush(self):
        self.session.flush()

    async def commit(self):
        self.session.commit()

    async def rollback(self):
        self.session.rollback()

    async def refresh(self, row):
        self.session.refresh(row)


@pytest.fixture
async def configuration(monkeypatch):
    engine = create_engine("sqlite://")
    AppIntegrationSettings.__table__.create(engine)
    OidcSettings.__table__.create(engine)
    environment = {}
    for routes in (deployment_settings, setup):
        monkeypatch.setattr(routes, "EnvConfigHandler", lambda: EnvConfigHandler(environment))
        monkeypatch.setattr(routes, "apply_deployment_provider_credentials", Mock())
    monkeypatch.setattr(setup, "set_password_policy_override", Mock())
    with Session(engine, expire_on_commit=False) as session:
        integration = AppIntegrationSettings()
        oidc = OidcSettings(enabled=False)
        session.add_all([integration, oidc])
        session.commit()
        app = FastAPI()
        for routes in (deployment_settings, setup, auth_oidc):
            app.include_router(routes.router)

        async def database():
            try:
                yield ConfigurationDb(session)
            finally:
                session.rollback()

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_admin] = lambda: User(
            username="administrator", email="admin@example.test", password_hash="unused"
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield SimpleNamespace(
                http=http, session=session, integration=integration, oidc=oidc, env=environment
            )
    engine.dispose()


async def test_provider_updates_encrypt_secrets_and_preserve_blank_values(configuration):
    response = await configuration.http.put(
        "/api/settings/deployment",
        json={"igdb_client_id": "client", "igdb_client_secret": "secret"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["providers"]["igdb_client_id"] == "client"
    assert response.json()["providers"]["igdb_client_secret_configured"] is True
    stored = configuration.integration.igdb_client_secret
    assert stored != "secret" and decrypt_secret(stored) == "secret"
    response = await configuration.http.put(
        "/api/settings/deployment", json={"igdb_client_secret": ""}
    )
    assert response.status_code == 200
    assert configuration.integration.igdb_client_secret == stored
    assert "secret" not in response.json()["providers"].values()


async def test_smtp_configuration_uses_existing_encryption_and_env_locks(configuration):
    response = await configuration.http.put(
        "/api/settings/deployment",
        json={
            "smtp_host": "smtp.example.test",
            "smtp_port": 465,
            "smtp_tls_mode": "ssl",
            "smtp_from_address": "sender@example.test",
            "smtp_password": "smtp-secret",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["providers"]["smtp_port"] == 465
    assert response.json()["providers"]["smtp_password_configured"]
    assert decrypt_secret(configuration.integration.smtp_password) == "smtp-secret"
    assert "smtp-secret" not in response.text
    response = await configuration.http.put("/api/settings/deployment", json={"smtp_password": ""})
    assert response.status_code == 200 and configuration.integration.smtp_password
    response = await configuration.http.put(
        "/api/settings/deployment", json={"smtp_password": None}
    )
    assert response.status_code == 200 and configuration.integration.smtp_password is None
    configuration.env["SMTP_HOST"] = "environment.example.test"
    response = await configuration.http.put(
        "/api/settings/deployment", json={"smtp_host": "replacement"}
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "field,value",
    [
        ("smtp_port", 0),
        ("smtp_tls_mode", "invalid"),
        ("smtp_from_address", "a\r\nBcc:bad@test"),
        ("smtp_host", "bad host"),
    ],
)
async def test_smtp_invalid_configuration_is_rejected(configuration, field, value):
    response = await configuration.http.put("/api/settings/deployment", json={field: value})
    assert response.status_code in {400, 422}


@pytest.mark.parametrize(
    ("field", "env_name"),
    [
        ("igdb_client_id", "IGDB_CLIENT_ID"),
        ("oidc_client_id", "OIDC_CLIENT_ID"),
        ("nginx_realip_header", "NGINX_REALIP_HEADER"),
    ],
)
async def test_environment_owned_fields_reject_api_updates(configuration, field, env_name):
    configuration.env[env_name] = "locked"
    response = await configuration.http.put("/api/settings/deployment", json={field: "override"})
    assert response.status_code == 409
    assert "managed by the deployment environment" in response.json()["detail"]


async def test_named_provider_normalization_retains_encrypted_secrets(configuration):
    secret = encrypt_secret("old-secret")
    configuration.oidc.providers_json = json.dumps(
        [{"slug": "provider", "enabled": True, "client_secret": secret}]
    )
    configuration.session.commit()
    response = await configuration.http.put(
        "/api/settings/deployment",
        json={
            "oidc_enabled": True,
            "oidc_providers_json": json.dumps(
                [
                    {
                        "name": " Company ",
                        "slug": " PROVIDER ",
                        "issuer_url": "https://issuer.test",
                        "client_id": "client",
                    }
                ]
            ),
        },
    )
    assert response.status_code == 200, response.text
    provider = json.loads(configuration.oidc.providers_json)[0]
    assert provider["slug"] == "provider" and provider["name"] == "Company"
    assert provider["client_secret"] == secret
    public = response.json()["oidc"]["named_providers"][0]
    assert public["client_secret_configured"] is True and "client_secret" not in public


@pytest.mark.parametrize(
    "providers",
    [
        "invalid-json",
        "{}",
        "[null]",
        '[{"name":"Name","slug":"bad slug"}]',
        '[{"name":"Name","slug":"valid"}]',
    ],
)
async def test_invalid_enabled_provider_updates_do_not_persist(configuration, providers):
    response = await configuration.http.put(
        "/api/settings/deployment", json={"oidc_enabled": True, "oidc_providers_json": providers}
    )
    assert response.status_code == 400
    configuration.session.refresh(configuration.oidc)
    assert configuration.oidc.enabled is False
    assert configuration.oidc.providers_json in (None, "[]")


async def test_setup_keeps_oidc_optional_and_ignores_environment_owned_secrets(configuration):
    configuration.env["IGDB_CLIENT_SECRET"] = "deployment-secret"
    response = await configuration.http.put(
        "/api/setup/configuration",
        json={"configuration": {"IGDB_CLIENT_SECRET": "override", "PASSWORD_MIN_LENGTH": 18}},
    )
    assert response.status_code == 200, response.text
    assert configuration.oidc.enabled is False
    assert configuration.integration.igdb_client_secret is None
    assert configuration.integration.password_min_length == 18


@pytest.mark.parametrize("from_environment", [True, False])
async def test_setup_resolves_complete_oidc_credentials_and_generated_callback(
    configuration, from_environment
):
    values = {
        "OIDC_ISSUER_URL": "https://issuer.test",
        "OIDC_CLIENT_ID": "client",
        "OIDC_CLIENT_SECRET": "secret",
    }
    if from_environment:
        configuration.env.update(values)
        values = {"OIDC_CLIENT_SECRET": "untrusted-override"}
    response = await configuration.http.put(
        "/api/setup/configuration", json={"sections": ["oidc"], "configuration": values}
    )
    assert response.status_code == 200, response.text
    assert configuration.oidc.enabled is True
    assert configuration.oidc.redirect_uri == "http://test/api/auth/oidc/callback"
    assert decrypt_secret(configuration.oidc.client_secret) == "secret"
    provider = json.loads(configuration.oidc.providers_json)[0]
    assert provider["issuer_url"] == "https://issuer.test"
    assert provider["autostart_enabled"] is True


@pytest.mark.parametrize("minimum", [0, 1025, "invalid"])
async def test_setup_rejects_invalid_password_policy(configuration, minimum):
    response = await configuration.http.put(
        "/api/setup/configuration", json={"configuration": {"PASSWORD_MIN_LENGTH": minimum}}
    )
    assert response.status_code == 400


async def test_real_ip_configuration_uses_the_existing_validators(configuration):
    response = await configuration.http.put(
        "/api/settings/deployment",
        json={
            "nginx_realip_header": "X-Forwarded-For",
            "nginx_realip_trusted_proxies": "10.0.0.0/8",
        },
    )
    assert response.status_code == 200, response.text
    assert configuration.integration.nginx_realip_header == "X-Forwarded-For"
    assert configuration.integration.nginx_realip_trusted_proxies == "10.0.0.0/8"
    response = await configuration.http.put(
        "/api/settings/deployment", json={"nginx_realip_trusted_proxies": "not-a-network"}
    )
    assert response.status_code == 400


async def test_public_url_persistence_validation_and_env_lock(configuration):
    response = await configuration.http.put(
        "/api/settings/deployment", json={"public_app_url": "https://app.example.test/"}
    )
    assert response.status_code == 200, response.text
    assert configuration.integration.public_app_url == "https://app.example.test"
    assert response.json()["app"]["public_app_url"] == "https://app.example.test"
    response = await configuration.http.put(
        "/api/settings/deployment", json={"public_app_url": "http://external.test"}
    )
    assert response.status_code == 400
    configuration.env["PUBLIC_APP_URL"] = "https://locked.example.test"
    response = await configuration.http.put(
        "/api/settings/deployment", json={"public_app_url": "https://override.test"}
    )
    assert response.status_code == 409
    response = await configuration.http.get("/api/settings/deployment")
    assert response.json()["provider_locks"]["public_app_url"] is True


async def test_smtp_automatic_port_remains_unset_on_mode_changes(configuration):
    response = await configuration.http.put(
        "/api/settings/deployment", json={"smtp_tls_mode": "ssl", "smtp_port": None}
    )
    assert response.status_code == 200 and configuration.integration.smtp_port is None
    response = await configuration.http.put("/api/settings/deployment", json={"smtp_port": 2525})
    assert response.status_code == 200
    response = await configuration.http.put(
        "/api/settings/deployment", json={"smtp_tls_mode": "none"}
    )
    assert response.status_code == 200 and configuration.integration.smtp_port == 2525


async def test_setup_persists_public_url_through_registry(configuration):
    response = await configuration.http.put(
        "/api/setup/configuration",
        json={"configuration": {"PUBLIC_APP_URL": "https://app.example.test/"}},
    )
    assert response.status_code == 200
    assert configuration.integration.public_app_url == "https://app.example.test"


async def test_tls_configuration_is_validated_persisted_and_environment_locked(configuration):
    response = await configuration.http.put(
        "/api/settings/deployment",
        json={
            "nginx_tls_enabled": True,
            "nginx_tls_redirect_http": True,
            "nginx_tls_certificate": "/etc/nginx/tls/cert.pem",
            "nginx_tls_private_key": "/etc/nginx/tls/key.pem",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["nginx"]["enabled"] and response.json()["nginx"]["redirect_http"]
    configuration.env["NGINX_TLS_ENABLED"] = "true"
    response = await configuration.http.put(
        "/api/settings/deployment", json={"nginx_tls_enabled": False}
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"nginx_tls_redirect_http": True},
        {"nginx_tls_certificate": "/tmp/only-cert.pem"},
        {"nginx_tls_certificate": "/tmp/cert;include.conf"},
    ],
)
async def test_invalid_tls_changes_do_not_persist(configuration, payload):
    response = await configuration.http.put("/api/settings/deployment", json=payload)
    assert response.status_code == 400
    configuration.session.refresh(configuration.integration)
    assert configuration.integration.nginx_tls_enabled is None
    assert configuration.integration.nginx_tls_certificate is None


async def test_failed_tls_activation_does_not_persist_and_smtp_save_does_not_reload(
    configuration, monkeypatch
):
    seen = []

    @asynccontextmanager
    async def activation(candidate):
        seen.append(candidate)
        if candidate is not None:
            raise NginxActivationError("invalid certificate")
        yield False

    monkeypatch.setattr(deployment_settings, "apply_nginx_change", activation)
    response = await configuration.http.put(
        "/api/settings/deployment", json={"nginx_tls_enabled": True}
    )
    assert response.status_code == 400
    configuration.session.refresh(configuration.integration)
    assert configuration.integration.nginx_tls_enabled is None
    assert (
        await configuration.http.put("/api/settings/deployment", json={"smtp_port": 2525})
    ).status_code == 200
    assert seen[-1] is None


async def test_setup_persists_boolean_tls_settings_without_string_coercion(configuration):
    response = await configuration.http.put(
        "/api/setup/configuration",
        json={"configuration": {"NGINX_TLS_ENABLED": False, "NGINX_TLS_REDIRECT_HTTP": False}},
    )
    assert response.status_code == 200, response.text
    assert configuration.integration.nginx_tls_enabled is False
    assert configuration.integration.nginx_tls_redirect_http is False


async def test_empty_proxy_list_is_an_explicit_no_trust_override(configuration):
    response = await configuration.http.put(
        "/api/settings/deployment", json={"nginx_realip_trusted_proxies": ""}
    )
    assert response.status_code == 200
    assert response.json()["real_ip"]["trusted_proxies"] == ""
    response = await configuration.http.get("/api/setup/configuration")
    proxy = next(section for section in response.json()["sections"] if section["id"] == "proxy")
    field = next(
        field for field in proxy["fields"] if field["name"] == "NGINX_REALIP_TRUSTED_PROXIES"
    )
    assert field["value"] == "" and field["source"] == "database"
