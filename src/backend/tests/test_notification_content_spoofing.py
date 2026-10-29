"""Direction overrides and invisible characters must not reach a notification's displayed text."""

import pytest

from src.features.notification_contract import _line, _text
from src.features.notification_safety import UnsafeContentError, check_plain_text

SPOOFED = "Invoice \u202egpj.exe"


def test_overrides_and_invisible_characters_are_rejected_in_composed_text():
    for value in (SPOOFED, "a\u200bb", "a\u2066b\u2069", "\ufeffTitle", "a\u2060b"):
        with pytest.raises(UnsafeContentError):
            check_plain_text(value)


def test_right_to_left_text_and_joiners_are_still_allowed():
    check_plain_text("שלום ‏ hello ‎")
    check_plain_text("family \U0001f468‍\U0001f469‍\U0001f467")


def test_legacy_title_and_body_are_stripped_not_rejected():
    assert _line(SPOOFED, "Notification") == "Invoice gpj.exe"
    assert _text("line\u200bone\nline\u202etwo", "x") == "lineone\nlinetwo"
    assert _line("\u200b\u200b", "Notification") == "Notification"
