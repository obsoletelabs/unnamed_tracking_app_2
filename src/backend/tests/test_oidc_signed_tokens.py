"""Exercise the real Authlib authorization-code and JWT verifier with a mock issuer."""

import base64
import hashlib
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from authlib.integrations.starlette_client import OAuth
from authlib.jose import JsonWebKey, jwt
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Response
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from src.api.routes import auth_oidc
from src.core import oidc
from src.core.auth import session_cookie_name
from src.database.models.auth import UserSession
from src.database.models.user import User
from src.database.session import get_db
from tests.test_oidc_callback import CallbackDb

ISSUER = "https://issuer.example.test"


@pytest.fixture(scope="module")
def signing_keys():
    return [
        JsonWebKey.generate_key("RSA", 2048, is_private=True),
        JsonWebKey.generate_key("RSA", 2048, is_private=True),
    ]


@pytest.fixture
async def provider(monkeypatch, signing_keys):
    config = oidc.OidcConfig(ISSUER, "test-client", "test-secret")
    oauth = OAuth()
    monkeypatch.setattr(oidc, "oauth", oauth)
    monkeypatch.setattr(auth_oidc, "oauth", oauth)
    monkeypatch.setattr(oidc, "_registered_configs", {})
    monkeypatch.setattr(auth_oidc, "_get_config", AsyncMock(return_value=config))
    oidc.register_oidc_provider(config)
    client = oauth.create_client("oidc")
    state = SimpleNamespace(
        config=config,
        nonce=None,
        challenge=None,
        code_used=False,
        claims={},
        key_index=0,
        jwks_index=0,
        jwks_calls=0,
        token_extra={},
        metadata_issuer=ISSUER,
        algorithms=["RS256"],
        algorithm="RS256",
        profile={"sub": "reader-subject", "email": "reader@example.test", "email_verified": True},
    )

    def handler(request):
        if request.url.path == "/.well-known/openid-configuration":
            return Response(
                200,
                json={
                    "issuer": state.metadata_issuer,
                    "authorization_endpoint": ISSUER + "/authorize",
                    "token_endpoint": ISSUER + "/token",
                    "jwks_uri": ISSUER + "/jwks",
                    "userinfo_endpoint": ISSUER + "/userinfo",
                    "id_token_signing_alg_values_supported": state.algorithms,
                },
            )
        if request.url.path == "/jwks":
            state.jwks_calls += 1
            index = state.jwks_index
            public = signing_keys[index].as_dict(is_private=False)
            public["kid"] = f"key-{index}"
            return Response(200, json={"keys": [public]})
        if request.url.path == "/userinfo":
            assert request.headers["authorization"] == "Bearer access-token"
            return Response(200, json=state.profile)
        if request.url.path == "/token":
            if state.code_used:
                return Response(400, json={"error": "invalid_grant"})
            state.code_used = True
            form = parse_qs(request.content.decode())
            verifier = form["code_verifier"][0]
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            assert challenge == state.challenge
            assert form["redirect_uri"] == ["http://test/api/auth/oidc/callback"]
            now = int(time.time())
            claims = {
                "iss": ISSUER,
                "sub": "reader-subject",
                "aud": "test-client",
                "exp": now + 300,
                "iat": now,
                "nonce": state.nonce,
                "email": "reader@example.test",
                "email_verified": True,
            }
            claims.update(state.claims)
            claims = {key: value for key, value in claims.items() if value is not None}
            header = {"alg": state.algorithm, "kid": f"key-{state.key_index}"}
            key = signing_keys[state.key_index] if state.algorithm != "HS256" else "test-secret"
            encoded = jwt.encode(header, claims, key).decode()
            return Response(
                200,
                json={
                    "access_token": "access-token",
                    "token_type": "Bearer",
                    "id_token": encoded,
                    **state.token_extra,
                },
            )
        raise AssertionError(f"Unexpected provider path: {request.url.path}")

    client.client_kwargs["transport"] = MockTransport(handler)
    engine = create_engine("sqlite://")
    User.__table__.create(engine)
    UserSession.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as session:
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="signed-token-test-only")
        app.include_router(auth_oidc.router)
        app.dependency_overrides[get_db] = lambda: CallbackDb(session)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            state.http, state.session, state.client = http, session, client
            yield state
    engine.dispose()


async def start(provider):
    response = await provider.http.get("/api/auth/oidc/login")
    assert response.status_code == 302
    parameters = parse_qs(urlsplit(response.headers["location"]).query)
    assert parameters["code_challenge_method"] == ["S256"]
    provider.nonce = parameters["nonce"][0]
    provider.challenge = parameters["code_challenge"][0]
    provider.state = parameters["state"][0]


async def finish(provider, **query):
    return await provider.http.get(
        "/api/auth/oidc/callback?"
        + urlencode(
            {
                "code": "one-use-code",
                "state": provider.state,
                **query,
            }
        )
    )


def assert_denied(provider, response):
    assert response.status_code == 303
    assert response.headers["location"] == "/login?oidc_error=authentication_failed"
    assert provider.session.scalar(select(User.id)) is None
    assert provider.session.scalar(select(UserSession.id)) is None
    assert session_cookie_name("test") not in response.cookies


async def test_real_signed_login_uses_pkce_and_consumes_state(provider):
    await start(provider)
    response = await finish(provider)
    assert response.headers["location"] == "/login?oidc=success"
    assert provider.session.scalar(select(UserSession.id)) is not None
    replay = await finish(provider)
    assert replay.headers["location"] == "/login?oidc_error=authentication_failed"
    assert len(provider.session.scalars(select(UserSession)).all()) == 1


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://attacker.example.test"},
        {"aud": "other-client"},
        {"aud": ["test-client", "other-client"], "azp": "other-client"},
        {"exp": 1},
        {"iat": 4102444800},
        {"nonce": "other-login"},
        {"nonce": None},
        {"nonce": "other-login", "nonce_supported": False},
        {"sub": None},
    ],
)
async def test_real_signed_invalid_claims_cannot_create_account(provider, claims):
    await start(provider)
    provider.claims = claims
    assert_denied(provider, await finish(provider))


async def test_signature_from_a_different_key_is_rejected(provider):
    await start(provider)
    provider.key_index = 1
    assert_denied(provider, await finish(provider))


async def test_unknown_key_refreshes_jwks_and_accepts_real_rotation(provider):
    await start(provider)
    await provider.client.fetch_jwk_set()
    provider.key_index = provider.jwks_index = 1
    response = await finish(provider)
    assert response.headers["location"] == "/login?oidc=success"
    assert provider.jwks_calls == 2


async def test_discovery_cannot_replace_configured_issuer(provider):
    await start(provider)
    provider.client.server_metadata["issuer"] = "https://attacker.example.test"
    response = await finish(provider)
    assert_denied(provider, response)
    assert not provider.code_used


async def test_symmetric_algorithm_cannot_use_public_key_as_mac_secret(provider):
    await start(provider)
    provider.algorithm = "HS256"
    provider.client.server_metadata["id_token_signing_alg_values_supported"] = ["HS256", "RS256"]
    assert_denied(provider, await finish(provider))


@pytest.mark.parametrize("same_subject", [True, False])
async def test_userinfo_requires_valid_id_token_and_matching_subject(provider, same_subject):
    await start(provider)
    provider.claims = {"email": None, "email_verified": None}
    if not same_subject:
        provider.profile["sub"] = "different-user"
    response = await finish(provider)
    if same_subject:
        assert response.headers["location"] == "/login?oidc=success"
        assert provider.session.scalar(select(User.email)) == "reader@example.test"
    else:
        assert_denied(provider, response)


async def test_unsigned_token_response_fields_are_not_identity_claims(provider, monkeypatch):
    await start(provider)
    provider.claims = {"email_verified": None}
    provider.token_extra = {"email_verified": True, "groups": ["administrators"]}
    config = replace(provider.config, require_verified_email=True, admin_group="administrators")
    monkeypatch.setattr(auth_oidc, "_get_config", AsyncMock(return_value=config))
    # Changing policy replaces the cached client; retain the isolated provider transport.
    oidc.register_oidc_provider(config)
    oidc.oauth.create_client("oidc").client_kwargs["transport"] = provider.client.client_kwargs[
        "transport"
    ]
    response = await finish(provider)
    assert response.headers["location"] == "/login?oidc_error=verified_email_required"
    assert provider.session.scalar(select(User.id)) is None
