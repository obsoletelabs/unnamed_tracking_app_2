"""Username rule messages shared (by contract) with the frontend field validator."""

import pytest

from src.core.auth import username_rule_error, validate_username

# Keep in step with src/frontend/src/tests/usernameValidation.test.ts.
VECTORS = [
    ("", "Enter a username."),
    ("   ", "Enter a username."),
    ("a" * 101, "Username is too long: use at most 100 characters (this one has 101)."),
    (
        "new name",
        "Username cannot contain spaces. Use only ASCII letters, digits, underscores, hyphens "
        "and dots.",
    ),
    (
        "me@example.com",
        "Username cannot contain \u201c@\u201d. Use only ASCII letters, digits, underscores, "
        "hyphens and dots.",
    ),
    (
        "caf\u00e9 \u00fc!",
        "Username cannot contain \u201c\u00e9\u201d, spaces, \u201c\u00fc\u201d, \u201c!\u201d. "
        "Use only ASCII letters, digits, underscores, hyphens and dots.",
    ),
    (
        "a/b\\c:d;e,f'g",
        "Username cannot contain \u201c/\u201d, \u201c\\\u201d, \u201c:\u201d, \u201c;\u201d, "
        "\u201c,\u201d, \u2026. Use only ASCII letters, digits, underscores, hyphens and dots.",
    ),
    ("a" * 100, None),
    ("  padded.Name_1-x  ", None),
]


@pytest.mark.parametrize(("value", "expected"), VECTORS)
def test_username_rule_error_names_the_broken_rule(value, expected):
    assert username_rule_error(value) == expected


def test_validate_username_raises_the_same_message_and_strips():
    assert validate_username("  ok_name  ") == "ok_name"
    with pytest.raises(ValueError, match="cannot contain spaces"):
        validate_username("not ok")
