"""Validation for the shared host-to-runtime transport token."""

import re

_TOKEN = re.compile(r"[A-Za-z0-9._~-]{32,256}\Z")


def runtime_token_valid(token: str) -> bool:
    """Accept header-safe, dotenv-friendly secrets without logging their contents."""
    return _TOKEN.fullmatch(token) is not None


TOKEN_HELP = (
    "PLUGIN_RUNTIME_TOKEN must be at least 32 characters and at most 256: "
    "ASCII letters, digits, dots, underscores, "
    "tildes or hyphens. Generate one with `openssl rand -hex 32`, set the same value "
    "for the application and plugin runtime, then restart both."
)
