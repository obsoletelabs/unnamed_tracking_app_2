"""Public Web Push library compatibility and server key/contact validation."""

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid

from src.features.notification_push_config import PushConfiguration, generate_vapid_key


def test_generated_vapid_key_matches_library_and_browser_public_format():
    private = generate_vapid_key()
    configuration = PushConfiguration(subject="mailto:admin@example.test", private_key=private)
    signing = Vapid.from_string(private)
    public = signing.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    assert base64.urlsafe_b64decode(configuration.public_key + "=") == public
    assert len(public) == 65 and public[0] == 4
    assert configuration.configured
    assert private not in repr(configuration)
    assert generate_vapid_key() != private


@pytest.mark.parametrize("value", ["/tmp/key.pem", "../private.pem", "not base64", "Zm9yZ2Vk"])
def test_private_key_cannot_be_a_file_path_or_forged_data(value):
    with pytest.raises(ValueError):
        PushConfiguration(private_key=value)


def test_other_curve_cannot_be_used_as_a_vapid_key():
    key = ec.generate_private_key(ec.SECP384R1())
    value = base64.b64encode(
        key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    ).decode()
    with pytest.raises(ValueError, match="P-256"):
        PushConfiguration(private_key=value)


@pytest.mark.parametrize(
    "value",
    [
        "http://example.test",
        "file:///tmp/key",
        "mailto:a@b\r\nBcc:all@b",
        "https://user:secret@example.test",
    ],
)
def test_contact_rejects_unsafe_schemes_headers_and_credentials(value):
    with pytest.raises(ValueError):
        PushConfiguration(subject=value)


def test_contact_does_not_use_any_user_account_email_implicitly():
    assert not PushConfiguration(private_key=generate_vapid_key()).configured
    assert PushConfiguration().public_key == ""
    assert (
        PushConfiguration(subject="mailto:Sender@EXAMPLE.TEST").subject
        == "mailto:Sender@example.test"
    )
