"""The rollout inventory document must account for everything the code still ships."""

from pathlib import Path

from src.core.preferences import DEFAULTS
from src.features.notification_contract import LEGACY_ADAPTERS
from src.features.notification_controller import EVENT_TYPES
from src.features.notification_policy import INBOX_PROVIDER, PUSH_PROVIDER
from src.features.smtp_configuration import SMTP_PROVIDER

DOCUMENT = (
    Path(__file__).resolve().parents[3] / "wiki/docs/development/notification-rollout.md"
).read_text(encoding="utf-8")
MIGRATIONS = Path(__file__).resolve().parents[1] / "src/database/migrations/versions"
RESERVED_EVENT_TYPES = (
    "auth.password_reset",
    "auth.user_invite",
    "destination.verification",
    "system.plugin.update",
)
ROUTES = (
    "/api/notifications",
    "/api/notifications/policy",
    "/api/notifications/stories/{story_id}",
    "/api/settings/notification-providers",
    "/api/settings/notification-timing",
    "/api/settings/notification-diagnostics",
)


def test_every_host_event_type_is_inventoried():
    for event_type in (*EVENT_TYPES.values(), *RESERVED_EVENT_TYPES):
        assert f"`{event_type}`" in DOCUMENT, event_type


def test_every_provider_and_compatibility_surface_is_inventoried():
    for provider in (INBOX_PROVIDER, SMTP_PROVIDER, PUSH_PROVIDER):
        assert f"`{provider}`" in DOCUMENT, provider
    for surface in LEGACY_ADAPTERS:
        assert f"`{surface}`" in DOCUMENT, surface
    for route in ROUTES:
        assert f"`{route}" in DOCUMENT, route


def test_every_notification_preference_is_inventoried():
    keys = [key for key in DEFAULTS if key.startswith(("notify_", "notification_"))]
    assert keys
    for key in keys:
        assert f"`{key}`" in DOCUMENT or key.startswith("notify_"), key
    assert "`notify_*`" in DOCUMENT


def test_every_notification_migration_is_documented_and_history_is_linear():
    revisions = {}
    for path in MIGRATIONS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "notification" not in path.name.lower():
            continue
        revisions[path.name.split("_", 1)[0]] = text
    assert revisions
    for revision in revisions:
        assert f"`{revision}`" in DOCUMENT, revision
