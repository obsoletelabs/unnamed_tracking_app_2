"""Public integration delegation v1 schemas. Legacy API-key scopes are separate."""

from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.api.schemas.game import GamePlayCompatibility
from src.database.models.game import GameStatus

SCOPE_DESCRIPTIONS = {
    "profile.read": "Read your display name and account identity",
    "games.read": "Read your library, ownership, ratings and tracking fields",
    "achievements.read": "Read provider achievements and local reports",
    "history.read": "Read your game field-change history",
    "lists.read": "Read your collections and custom lists",
    "media.read": "Read your tracked media",
    "tracking.write": "Change your game status, completion, playtime and ratings",
    "achievements.write": "Create manual achievements and edit local achievement reports",
    "compatibility.write": "Save your games' multiplayer settings",
    "library.sync": (
        "Add and update games in your library, upload their artwork and report what your "
        "launcher holds"
    ),
    "saves.read": "List and download your game save backups",
    "saves.write": "Upload new save backups and rename existing ones",
    "content.write": "Add screenshots, clips, soundtracks and documents to your games",
}


class DelegationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


def integration_scopes(value: list[str]) -> list[str]:
    if len(set(value)) != len(value) or set(value) - SCOPE_DESCRIPTIONS.keys():
        raise ValueError("Choose unique supported integration capabilities.")
    return sorted(value)


# Fixed origins of first-party desktop apps (see features/delegation.DESKTOP_APPS);
# an operator may disable or re-allow them like a web origin.
DESKTOP_ORIGINS = frozenset({"desktop:playnite"})


def integration_origin(value: str) -> str:
    """Operator approval is for an exact HTTPS origin, never a URL credential or path."""
    if value in DESKTOP_ORIGINS:
        return value
    parts = urlsplit(value)
    if any(
        (
            parts.scheme != "https",
            not parts.hostname,
            parts.username,
            parts.password,
            parts.path not in {"", "/"},
            parts.query,
            parts.fragment,
            any(char.isspace() for char in value),
            "\\" in value,
        )
    ):
        raise ValueError("Enter an HTTPS origin without a path, credentials or query.")
    port = parts.port
    assert parts.hostname is not None
    authority = parts.hostname.lower()
    if ":" in authority:
        authority = f"[{authority}]"
    if port is not None and port != 443:
        authority += f":{port}"
    return f"https://{authority}"


class ClientWrite(DelegationSchema):
    name: str = Field(min_length=1, max_length=100)
    origin: str = Field(max_length=512)
    enabled: bool = False

    _origin = field_validator("origin")(integration_origin)


class PolicyWrite(DelegationSchema):
    enabled: bool
    allow_guests: bool = False
    allow_any_origin: bool = False
    allow_legacy_api_keys: bool = False
    allow_desktop_apps: bool = True
    guest_retention_hours: int = Field(default=48, ge=1, le=8760)
    maximum_grant_hours: int = Field(default=336, ge=1, le=720)


class ExternalAccount(DelegationSchema):
    """Display context reported by the requester, never source-side identity proof."""

    provider: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=100)


class AccessRequest(DelegationSchema):
    client_id: UUID
    scopes: list[str] = Field(min_length=1, max_length=len(SCOPE_DESCRIPTIONS))
    code_challenge: str = Field(pattern=r"^[a-f0-9]{64}$")
    external_account: ExternalAccount | None = None

    _scopes = field_validator("scopes")(integration_scopes)


class DesktopAccessRequest(DelegationSchema):
    """A first-party desktop app asks the user for access from their browser."""

    scopes: list[str] = Field(min_length=1, max_length=len(SCOPE_DESCRIPTIONS))
    code_challenge: str = Field(pattern=r"^[a-f0-9]{64}$")
    # Shown on the consent page and in Connected apps so people can tell devices apart.
    device_name: str = Field(min_length=1, max_length=100)
    device_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")

    _scopes = field_validator("scopes")(integration_scopes)


class AccessDecision(DelegationSchema):
    approve: bool
    scopes: list[str] = Field(default_factory=list, max_length=len(SCOPE_DESCRIPTIONS))
    expires_in_hours: int | None = Field(default=24, ge=1, le=720)

    _scopes = field_validator("scopes")(integration_scopes)


class AccessExchange(DelegationSchema):
    verifier: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class GrantChange(DelegationSchema):
    scopes: list[str] = Field(min_length=1, max_length=len(SCOPE_DESCRIPTIONS))

    _scopes = field_validator("scopes")(integration_scopes)


class TrackingWrite(DelegationSchema):
    status: GameStatus | None = None
    favorite: bool | None = None
    playtime_seconds: int | None = Field(default=None, ge=0, le=2**53 - 1)
    completion_date: int | None = Field(default=None, ge=0, le=2**53 - 1)
    rating_story: float | None = Field(default=None, ge=0, le=10)
    rating_gameplay: float | None = Field(default=None, ge=0, le=10)
    rating_soundtrack: float | None = Field(default=None, ge=0, le=10)
    rating_overall: float | None = Field(default=None, ge=0, le=10)


class CompatibilityWrite(DelegationSchema):
    play_compatibility: GamePlayCompatibility | None


class AchievementReportWrite(DelegationSchema):
    provider: str = Field(
        default="manual", min_length=1, max_length=30, pattern=r"^[a-zA-Z0-9._-]+$"
    )
    external_id: str | None = Field(default=None, min_length=1, max_length=200)
    name: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    unlocked: bool
    unlocked_at: int | None = Field(default=None, ge=0, le=2**53 - 1)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("An achievement name cannot be blank.")
        return value.strip() if value is not None else value
