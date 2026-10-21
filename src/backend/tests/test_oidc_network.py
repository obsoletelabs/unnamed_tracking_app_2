"""OIDC network policy, bounded responses and hostile discovery configuration."""

import logging
import socket
import ssl
from http.client import HTTPConnection
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, Request
from starlette.responses import RedirectResponse

from src.core import oidc, oidc_http
from src.core.config import settings
from src.helpers import public_http


@pytest.mark.parametrize(
    ("stage", "reason"),
    [
        ("registration", "provider_failure"),
        ("callback_url", "insecure_callback"),
        ("discovery", "private_address"),
        ("discovery", "certificate_error"),
        ("discovery", "dns_error"),
        ("discovery", "timeout"),
        ("authorization_redirect", "provider_failure"),
    ],
)
async def test_login_failure_logs_safe_stage_without_provider_secrets(
    monkeypatch, caplog, stage, reason
):
    secret = "never-log-client-secret-token-authorization-code-nonce"
    error = RuntimeError(f"https://provider.example.test/?secret={secret}")
    metadata_error = httpx.TransportError(f"provider response: {secret}")
    metadata_error.__cause__ = {
        "certificate_error": ssl.SSLCertVerificationError(secret),
        "dns_error": socket.gaierror(-2, secret),
        "timeout": TimeoutError(secret),
    }.get(reason, ValueError("Host must resolve exclusively to public addresses."))
    client = SimpleNamespace(
        load_server_metadata=AsyncMock(return_value={"issuer": "https://issuer.example.test"}),
        authorize_redirect=AsyncMock(return_value=RedirectResponse("https://issuer.example.test")),
    )
    register = Mock()
    monkeypatch.setattr(oidc, "register_oidc_provider", register)
    monkeypatch.setattr(oidc, "oauth", SimpleNamespace(create_client=Mock(return_value=client)))
    monkeypatch.setattr(
        oidc, "callback_url", Mock(return_value="https://tracking.example.test/callback")
    )
    if stage == "registration":
        register.side_effect = error
    elif stage == "callback_url":
        oidc.callback_url.side_effect = ValueError("OIDC callback requires HTTPS")
    elif stage == "discovery":
        client.load_server_metadata.side_effect = metadata_error
    else:
        client.authorize_redirect.side_effect = error
    config = oidc.OidcConfig("https://issuer.example.test", "test-client", secret, slug="authentik")
    app = FastAPI()

    @app.get("/start")
    async def start(request: Request):
        return await oidc.begin_oidc(request, config)

    with caplog.at_level(logging.WARNING, logger="src.core.oidc"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://tracking.example.test"
        ) as http:
            response = await http.get("/start")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?oidc_error=authentication_failed"
    assert "provider='authentik'" in caplog.text
    assert f"stage={stage}" in caplog.text
    assert f"reason={reason}" in caplog.text
    assert secret not in caplog.text
    assert "provider.example.test" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


class ProviderResponse:
    status = 200

    def __init__(self, payload=b'{"issuer":"https://issuer.example.test"}', encoding="identity"):
        self.payload, self.encoding = payload, encoding

    def getheader(self, name, default=None):
        return self.encoding if name == "Content-Encoding" else default

    def getheaders(self):
        return [("Content-Type", "application/json")]

    def read(self, limit):
        return self.payload[:limit]


def connection(response=None):
    return Mock(spec=HTTPConnection, getresponse=Mock(return_value=response or ProviderResponse()))


@pytest.mark.parametrize(
    "url",
    [
        "http://issuer.example.test",
        "https://name:secret@issuer.example.test",
        "https://issuer.example.test/#fragment",
        "https://issuer.example.test:bad",
        "https://issuer.example.test/?secret=query",
        "file:///etc/passwd",
    ],
)
def test_issuer_rejects_insecure_or_ambiguous_urls(url):
    with pytest.raises(ValueError):
        oidc_http.OidcTransport(url)


@pytest.mark.parametrize(
    "value",
    [
        "http://sso.internal",
        "https://sso.internal/path",
        "https://sso.internal/?query",
        "https://name:secret@sso.internal",
    ],
)
def test_private_network_exception_requires_an_exact_https_origin(value):
    with pytest.raises(ValueError):
        oidc_http.private_issuer_origins(value)


@pytest.mark.parametrize("exceptions", ["", "https://sso.internal:8443,https://other.internal"])
async def test_configured_issuer_trust_is_limited_to_its_exact_origin(monkeypatch, exceptions):
    private, public = connection(), connection()
    private_factory = Mock(return_value=private)
    public_factory = Mock(return_value=public)
    monkeypatch.setattr(oidc_http, "HTTPSConnection", private_factory)
    monkeypatch.setattr(oidc_http, "public_connection", public_factory)
    transport = oidc_http.OidcTransport("https://sso.internal:8443/realm", exceptions)
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://sso.internal:8443/keys")
        await client.get("https://other.internal/keys")
        await client.get("https://sso.internal:8444/keys")
    private_factory.assert_called_once()
    assert private_factory.call_args.args == ("sso.internal", 8443)
    assert [call.args[0] for call in public_factory.call_args_list] == [
        "https://other.internal/keys",
        "https://sso.internal:8444/keys",
    ]
    assert private.close.call_count == 1
    assert public.close.call_count == 2


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.2", "169.254.169.254", "::1"])
async def test_cross_origin_provider_endpoints_cannot_resolve_to_private_or_metadata_addresses(
    monkeypatch, address
):
    family = public_http.socket.AF_INET6 if ":" in address else public_http.socket.AF_INET
    answer = (family, public_http.socket.SOCK_STREAM, 6, "", (address, 443))
    monkeypatch.setattr(public_http.socket, "getaddrinfo", Mock(return_value=[answer]))
    async with httpx.AsyncClient(
        transport=oidc_http.OidcTransport("https://public.example.test")
    ) as client:
        with pytest.raises(httpx.TransportError, match="OIDC provider connection failed"):
            await client.get("https://other.example.test/keys?secret=never-log-this")


async def test_localhost_http_is_development_only_and_uses_a_literal_loopback(monkeypatch):
    with pytest.raises(ValueError):
        oidc_http.OidcTransport("http://localhost:9090")
    factory = Mock(return_value=connection())
    monkeypatch.setattr(oidc_http, "HTTPConnection", factory)
    async with httpx.AsyncClient(
        transport=oidc_http.OidcTransport("http://localhost:9090", development=True)
    ) as client:
        assert (await client.get("http://localhost:9090/token")).status_code == 200
    factory.assert_called_once_with("127.0.0.1", 9090, timeout=8)


@pytest.mark.parametrize("failure", ["redirect", "oversized", "compressed"])
async def test_provider_responses_are_bounded_closed_and_never_redirected(monkeypatch, failure):
    response = ProviderResponse()
    if failure == "redirect":
        response.status = 302
    elif failure == "oversized":
        response.payload = b"x" * (oidc_http.MAX_RESPONSE_BYTES + 1)
    else:
        response.encoding = "gzip"
    upstream = connection(response)
    factory = Mock(return_value=upstream)
    monkeypatch.setattr(oidc_http, "HTTPSConnection", factory)
    async with httpx.AsyncClient(
        transport=oidc_http.OidcTransport("https://issuer.example.test"),
        follow_redirects=True,
    ) as client:
        with pytest.raises(httpx.TransportError):
            await client.get("https://issuer.example.test/discovery")
    factory.assert_called_once()
    upstream.close.assert_called_once()
    assert upstream.request.call_args.kwargs["headers"]["accept-encoding"] == "identity"


async def test_http_work_runs_off_the_event_loop(monkeypatch):
    import threading

    event_loop_thread = threading.get_ident()
    observed = []
    upstream = connection()

    def connect(_host, _port, **_kwargs):
        observed.append(threading.get_ident())
        return upstream

    monkeypatch.setattr(oidc_http, "HTTPSConnection", connect)
    async with httpx.AsyncClient(
        transport=oidc_http.OidcTransport("https://issuer.example.test")
    ) as client:
        await client.get("https://issuer.example.test/keys")
    assert observed and observed[0] != event_loop_thread


async def test_discovery_cannot_inject_http_client_or_pkce_options(monkeypatch):
    monkeypatch.setattr(oidc, "oauth", oidc.OAuth())
    monkeypatch.setattr(oidc, "_registered_configs", {})
    monkeypatch.setattr(settings, "OIDC_PRIVATE_ISSUER_ORIGINS", "")
    config = oidc.OidcConfig("https://issuer.example.test", "client", "secret")
    oidc.register_oidc_provider(config)
    application = oidc.oauth.create_client("oidc")
    malicious = {
        "authorization_endpoint": "https://issuer.example.test/authorize",
        "token_endpoint": "https://issuer.example.test/token",
        "proxy": "http://127.0.0.1:1234",
        "transport": "unsafe",
        "follow_redirects": True,
        "trust_env": True,
        "verify": False,
        "headers": {"Authorization": "attacker"},
        "code_challenge_method": "plain",
    }
    async with application._get_oauth_client(**malicious) as client:
        assert client._transport is application.client_kwargs["transport"]
        assert client.code_challenge_method == "S256"
        assert not client.follow_redirects and not client.trust_env
        assert "authorization" not in client.headers


@pytest.mark.parametrize("development", [False, True])
def test_current_request_generates_a_callback_without_required_canonical_configuration(
    monkeypatch, development
):
    from fastapi import FastAPI, Request

    from src.api.routes import auth_oidc

    monkeypatch.setattr(settings, "DEBUG", development)
    monkeypatch.setattr(settings, "STARTUP_MODE", "")
    app = FastAPI()
    app.include_router(auth_oidc.router)

    def request(host, scheme="http"):
        return Request(
            {
                "type": "http",
                "scheme": scheme,
                "server": (host, 443 if scheme == "https" else 80),
                "path": "/",
                "root_path": "",
                "headers": [(b"host", host.encode())],
                "router": app.router,
            }
        )

    config = oidc.OidcConfig("https://issuer.example.test", "client", "secret")
    assert oidc.callback_url(request("archive.example.test", "https"), config) == (
        "https://archive.example.test/api/auth/oidc/callback"
    )
    with pytest.raises(ValueError, match="HTTPS"):
        oidc.callback_url(request("archive.example.test"), config)
    if development:
        assert oidc.callback_url(request("localhost"), config) == (
            "http://localhost/api/auth/oidc/callback"
        )
    else:
        with pytest.raises(ValueError):
            oidc.callback_url(request("localhost"), config)


@pytest.mark.parametrize(
    "uri",
    [
        "http://tracking.example.test/api/auth/oidc/callback",
        "https://user:secret@tracking.example.test/api/auth/oidc/callback",
        "https://tracking.example.test/api/auth/oidc/callback#fragment",
    ],
)
def test_explicit_callback_rejects_insecure_or_ambiguous_urls(monkeypatch, uri):
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "STARTUP_MODE", "")
    config = oidc.OidcConfig("https://issuer.example.test", "client", "secret", redirect_uri=uri)
    with pytest.raises(ValueError):
        oidc.callback_url(None, config)


@pytest.fixture
def tls_provider(tmp_path):
    import datetime
    import ssl
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "issuer.pem", tmp_path / "issuer-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = b'{"keys":[]}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"https://localhost:{server.server_port}", str(cert_path)
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.parametrize("legacy_exception", [False, True])
async def test_configured_private_https_requires_a_trusted_hostname_certificate(
    monkeypatch, tls_provider, legacy_exception
):
    url, cert_path = tls_provider
    exceptions = url if legacy_exception else ""
    transport = oidc_http.OidcTransport(url, exceptions)
    # Choosing an issuer trusts its network origin, never an untrusted certificate.
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.TransportError) as rejected:
            await client.get(url + "/jwks")
        assert isinstance(rejected.value.__cause__, ssl.SSLCertVerificationError)
    create_context = oidc_http.ssl.create_default_context
    monkeypatch.setattr(
        oidc_http.ssl, "create_default_context", lambda: create_context(cafile=cert_path)
    )
    async with httpx.AsyncClient(transport=oidc_http.OidcTransport(url, exceptions)) as client:
        response = await client.get(url + "/jwks")
        assert response.status_code == 200 and response.json() == {"keys": []}
        # A different hostname remains outside the configured origin's private trust.
        with pytest.raises(httpx.TransportError) as rejected:
            await client.get(url.replace("localhost", "127.0.0.1") + "/jwks")
        assert isinstance(rejected.value.__cause__, ValueError)
    # Even a trusted certificate cannot authenticate a different issuer hostname.
    wrong_hostname = url.replace("localhost", "127.0.0.1")
    async with httpx.AsyncClient(transport=oidc_http.OidcTransport(wrong_hostname)) as client:
        with pytest.raises(httpx.TransportError) as rejected:
            await client.get(wrong_hostname + "/jwks")
        assert isinstance(rejected.value.__cause__, ssl.SSLCertVerificationError)
