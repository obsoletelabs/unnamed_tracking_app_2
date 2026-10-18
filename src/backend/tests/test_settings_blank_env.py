"""The shipped example.env leaves optional values blank; startup must still parse it."""

from pathlib import Path

from src.core.config import Settings

EXAMPLE_ENV = Path(__file__).resolve().parents[3] / "example.env"


def test_blank_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("SMTP_PORT", "")
    monkeypatch.setenv("SMTP_TLS_MODE", "")
    monkeypatch.setenv("DEFAULT_UI_STYLE", "")

    parsed = Settings(_env_file=None)

    assert parsed.SMTP_PORT == 587
    assert parsed.SMTP_TLS_MODE == "starttls"
    assert parsed.DEFAULT_UI_STYLE == "modern"


def test_example_env_parses_without_edits(monkeypatch):
    if not EXAMPLE_ENV.exists():  # the backend image only contains src/backend
        return
    for name in ("SMTP_PORT", "SMTP_TLS_MODE", "DEFAULT_UI_STYLE"):
        monkeypatch.delenv(name, raising=False)

    parsed = Settings(_env_file=EXAMPLE_ENV)

    assert parsed.SMTP_PORT == 587
