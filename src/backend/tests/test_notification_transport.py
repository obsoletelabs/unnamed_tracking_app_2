"""Bounded trusted webhook transport, disabled mentions and no credential redirects."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

RUNTIME = Path(__file__).resolve().parents[2] / "plugin-runtime/notification_transport.py"
if not RUNTIME.exists():
    RUNTIME = Path("/plugin-runtime/notification_transport.py")
SPEC = importlib.util.spec_from_file_location("tested_notification_transport", RUNTIME)
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)
URL = "https://discord.com/api/webhooks/1234567890/" + "a" * 40
PAYLOAD = {"content": "Example notice", "allowed_mentions": {"parse": []}}


@pytest.mark.parametrize(
    "status,retryable",
    [(301, False), (400, False), (403, False), (404, False), (429, True), (500, True), (503, True)],
)
def test_status_failure_is_sanitized_and_retry_is_core_owned(monkeypatch, status, retryable):
    opener = Mock()
    opener.open.side_effect = HTTPError(URL, status, "secret provider response", {}, None)
    monkeypatch.setattr(transport, "build_opener", lambda *_args: opener)
    assert transport.send_discord(URL, PAYLOAD) == {
        "success": False,
        "retryable": retryable,
        "error": "discord_rejected",
    }
    assert opener.open.call_args.kwargs == {"timeout": 10}
    assert opener.open.call_args.args[0].full_url == URL + "?wait=true"


def test_webhook_credentials_cannot_follow_a_redirect():
    assert (
        transport._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test")
        is None
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"content": "notice"},
        {**PAYLOAD, "allowed_mentions": {"parse": ["everyone"]}},
        {**PAYLOAD, "content": "x" * 2001},
        {**PAYLOAD, "embeds": []},
        {**PAYLOAD, "username": "a private account"},
        {"embeds": [{"description": "x" * 4097}], "allowed_mentions": {"parse": []}},
        {"embeds": [{"url": 123}], "allowed_mentions": {"parse": []}},
    ],
)
def test_invalid_raw_transport_payload_is_rejected_without_network(monkeypatch, payload):
    opener = Mock()
    monkeypatch.setattr(transport, "build_opener", opener)
    with pytest.raises(ValueError):
        transport.send_discord(URL, payload)
    opener.assert_not_called()


def test_transport_never_exposes_socket_error_details(monkeypatch):
    opener = Mock()
    opener.open.side_effect = OSError("credential in error: " + URL)
    monkeypatch.setattr(transport, "build_opener", lambda *_args: opener)
    result = transport.send_discord(URL, PAYLOAD)
    assert result == {"success": False, "retryable": True, "error": "discord_unavailable"}
