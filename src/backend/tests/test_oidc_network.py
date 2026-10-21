"""OIDC network policy, bounded responses and hostile discovery configuration."""

from http.client import HTTPConnection
from unittest.mock import Mock

import httpx
import pytest

from src.core import oidc, oidc_http
from src.core.config import settings
from src.helpers import public_http


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


async def test_explicit_private_access_is_limited_to_selected_issuer_origin(monkeypatch):
    private, public = connection(), connection()
    private_factory = Mock(return_value=private)
    public_factory = Mock(return_value=public)
    monkeypatch.setattr(oidc_http, "HTTPSConnection", private_factory)
    monkeypatch.setattr(oidc_http, "public_connection", public_factory)
    transport = oidc_http.OidcTransport(
        "https://sso.internal:8443/realm", "https://sso.internal:8443,https://other.internal"
    )
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
async def test_public_provider_cannot_resolve_to_private_or_metadata_addresses(
    monkeypatch, address
):
    family = public_http.socket.AF_INET6 if ":" in address else public_http.socket.AF_INET
    answer = (family, public_http.socket.SOCK_STREAM, 6, "", (address, 443))
    monkeypatch.setattr(public_http.socket, "getaddrinfo", Mock(return_value=[answer]))
    async with httpx.AsyncClient(
        transport=oidc_http.OidcTransport("https://public.example.test")
    ) as client:
        with pytest.raises(httpx.TransportError, match="OIDC provider connection failed"):
            await client.get("https://public.example.test/keys?secret=never-log-this")


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
    monkeypatch.setattr(oidc_http, "public_connection", factory)
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

    def connect(_url, **_kwargs):
        observed.append(threading.get_ident())
        return upstream

    monkeypatch.setattr(oidc_http, "public_connection", connect)
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
def test_request_host_requires_canonical_configuration_except_local_development(
    monkeypatch, development
):
    from fastapi import FastAPI, Request

    from src.api.routes import auth_oidc

    monkeypatch.setattr(settings, "DEBUG", development)
    monkeypatch.setattr(settings, "STARTUP_MODE", "")
    app = FastAPI()
    app.include_router(auth_oidc.router)

    def request(host):
        return Request(
            {
                "type": "http",
                "scheme": "http",
                "server": (host, 80),
                "path": "/",
                "root_path": "",
                "headers": [(b"host", host.encode())],
                "router": app.router,
            }
        )

    config = oidc.OidcConfig("https://issuer.example.test", "client", "secret")
    with pytest.raises(ValueError, match="Configure the public app URL"):
        oidc.callback_url(request("attacker.example.test"), config)
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


async def test_real_private_https_requires_a_trusted_hostname_certificate(
    monkeypatch, tls_provider
):
    url, cert_path = tls_provider
    transport = oidc_http.OidcTransport(url, url)
    # An explicit network grant never trusts a self-signed certificate by itself.
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.TransportError):
            await client.get(url + "/jwks")
    create_context = oidc_http.ssl.create_default_context
    monkeypatch.setattr(
        oidc_http.ssl, "create_default_context", lambda: create_context(cafile=cert_path)
    )
    async with httpx.AsyncClient(transport=oidc_http.OidcTransport(url, url)) as client:
        response = await client.get(url + "/jwks")
        assert response.status_code == 200 and response.json() == {"keys": []}
    # The same trusted certificate still gives no network permission without the origin grant.
    async with httpx.AsyncClient(transport=oidc_http.OidcTransport(url)) as client:
        with pytest.raises(httpx.TransportError):
            await client.get(url + "/jwks")
