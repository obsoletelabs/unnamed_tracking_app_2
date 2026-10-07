"""Provider-neutral, bounded metadata extension contracts for Plugin API v1.1.1."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=512)]
ActionId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")]


class MetadataModel(BaseModel):
    """Reject unknown fields at the public boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class MediaType(StrEnum):
    """Canonical library domains supported by provider capabilities."""

    GAME = "game"
    MOVIE = "movie"
    TV_SHOW = "tv_show"
    ANIME = "anime"


class ProviderHealth(StrEnum):
    """Availability, distinguished from plugin process health."""

    DISABLED = "disabled"
    NOT_CONFIGURED = "not_configured"
    UNVALIDATED = "unvalidated"
    HEALTHY = "healthy"
    UNAVAILABLE = "temporarily_unavailable"
    RATE_LIMITED = "rate_limited"
    PLUGIN_UNAVAILABLE = "plugin_unavailable"
    INVALID_CONFIGURATION = "invalid_configuration"


class MetadataProviderRequest(MetadataModel):
    """Authenticated work issued by the host, including its execution policy."""

    request_id: UUID
    user_id: UUID
    query: str = Field(min_length=1, max_length=512)
    media_type: MediaType = MediaType.GAME
    policy: Literal["interactive", "background"] = "interactive"
    limit: int = Field(default=20, ge=1, le=50)
    cursor: Identifier | None = None
    resource: Literal["entity", "episodes", "airing", "relations", "recommendations"] = "entity"
    season_number: int | None = Field(default=None, ge=0, le=10_000)
    options: dict[ActionId, Annotated[str, Field(max_length=512)] | bool | int] = Field(
        default_factory=dict, max_length=32
    )


class MetadataCandidate(MetadataModel):
    """Lightweight identity; metadata and artwork are separate operations."""

    external_id: Identifier
    title: str = Field(min_length=1, max_length=512)
    year: int | None = Field(default=None, ge=1800, le=3000)
    provider: str = Field(min_length=1, max_length=128)
    media_type: MediaType = MediaType.GAME
    alternate_titles: tuple[Annotated[str, Field(max_length=512)], ...] = Field(
        default=(), max_length=20
    )
    provider_ids: dict[ActionId, Identifier] = Field(default_factory=dict, max_length=32)
    platforms: tuple[Annotated[str, Field(max_length=128)], ...] = Field(default=(), max_length=32)


class MetadataLink(MetadataModel):
    """A provider reference rendered by the existing application links UI."""

    label: str = Field(min_length=1, max_length=128)
    url: AnyHttpUrl


class MetadataEpisode(MetadataModel):
    """Provider-neutral episode data; watched state remains entirely host-owned."""

    episode_number: int = Field(ge=0, le=100_000)
    season_number: int | None = Field(default=None, ge=0, le=10_000)
    title: str | None = Field(default=None, max_length=512)
    description: str | None = Field(default=None, max_length=100_000)
    air_date: date | None = None
    air_at: int | None = Field(default=None, ge=0)
    runtime_minutes: int | None = Field(default=None, ge=0, le=100_000)
    still_url: AnyHttpUrl | None = None


class MetadataSeason(MetadataModel):
    season_number: int = Field(ge=0, le=10_000)
    external_id: Identifier | None = None
    title: str | None = Field(default=None, max_length=512)
    episode_count: int | None = Field(default=None, ge=0, le=100_000)
    air_date: date | None = None


class MetadataRelation(MetadataModel):
    """An identity and its relation, with no application database references."""

    candidate: MetadataCandidate
    relation: str = Field(min_length=1, max_length=64)
    group: Literal["related", "chain", "branch", "recommendation"] = "related"
    parent_external_id: Identifier | None = None
    parent_group: Literal["chain", "branch"] | None = None
    is_current: bool = False
    format: str | None = Field(default=None, max_length=64)
    episode_count: int | None = Field(default=None, ge=0, le=100_000)
    poster_url: AnyHttpUrl | None = None


class MetadataAiring(MetadataModel):
    status: Literal["ongoing", "completed", "paused", "cancelled", "unknown"] = "unknown"
    is_airing: bool | None = None
    aired_episodes: int | None = Field(default=None, ge=0, le=100_000)
    total_episodes: int | None = Field(default=None, ge=0, le=100_000)
    next_episode_number: int | None = Field(default=None, ge=0, le=100_000)
    next_episode_at: int | None = Field(default=None, ge=0)


class MetadataPatch(MetadataModel):
    """Partial canonical fields; missing values never erase existing information."""

    title: str | None = Field(default=None, min_length=1, max_length=512)
    year: int | None = Field(default=None, ge=1800, le=3000)
    release_date: date | None = None
    description: str | None = Field(default=None, max_length=100_000)
    developer: str | None = Field(default=None, max_length=200)
    publisher: str | None = Field(default=None, max_length=200)
    series: str | None = Field(default=None, max_length=200)
    age_rating: str | None = Field(default=None, max_length=64)
    tags: tuple[Annotated[str, Field(max_length=128)], ...] = Field(default=(), max_length=128)
    features: tuple[Annotated[str, Field(max_length=128)], ...] = Field(default=(), max_length=128)
    genres: tuple[Annotated[str, Field(max_length=128)], ...] = Field(default=(), max_length=128)
    platforms: tuple[Annotated[str, Field(max_length=128)], ...] = Field(default=(), max_length=64)
    time_to_beat_hours: float | None = Field(default=None, ge=0, le=100_000, allow_inf_nan=False)
    runtime_minutes: int | None = Field(default=None, ge=0, le=100_000)
    director: str | None = Field(default=None, max_length=512)
    writer: str | None = Field(default=None, max_length=512)
    creators: tuple[Annotated[str, Field(max_length=512)], ...] = Field(default=(), max_length=64)
    studios: tuple[Annotated[str, Field(max_length=256)], ...] = Field(default=(), max_length=64)
    countries: tuple[Annotated[str, Field(max_length=128)], ...] = Field(default=(), max_length=64)
    languages: tuple[Annotated[str, Field(max_length=128)], ...] = Field(default=(), max_length=64)
    provider_ids: dict[ActionId, Identifier] = Field(default_factory=dict, max_length=32)
    alternate_titles: tuple[Annotated[str, Field(max_length=512)], ...] = Field(
        default=(), max_length=20
    )
    links: tuple[MetadataLink, ...] = Field(default=(), max_length=32)
    episode_runtime_minutes: int | None = Field(default=None, ge=0, le=100_000)
    episode_count: int | None = Field(default=None, ge=0, le=100_000)
    format: str | None = Field(default=None, max_length=64)
    scores: dict[ActionId, Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]] = Field(
        default_factory=dict, max_length=32
    )
    titles: dict[ActionId, Annotated[str, Field(max_length=512)]] = Field(
        default_factory=dict, max_length=20
    )
    # Bounded aggregate across pages, including long-running anime and daily TV shows.
    episodes: tuple[MetadataEpisode, ...] = Field(default=(), max_length=10_000)
    seasons: tuple[MetadataSeason, ...] = Field(default=(), max_length=100)
    airing: MetadataAiring | None = None
    relations: tuple[MetadataRelation, ...] = Field(default=(), max_length=500)
    relation_group: str | None = Field(default=None, max_length=512)


class MediaAsset(MetadataModel):
    """An asset reference; providers never write the host filesystem."""

    kind: Literal["key_art", "banner", "logo", "icon", "screenshot", "hero", "poster"]
    url: AnyHttpUrl
    priority: int = Field(default=100, ge=0, le=10_000)
    width: int | None = Field(default=None, ge=1, le=50_000)
    height: int | None = Field(default=None, ge=1, le=50_000)


class ProviderFailure(MetadataModel):
    """Safe failure data without remote response bodies or credential values."""

    code: Literal[
        "not_configured", "invalid_configuration", "unavailable", "rate_limited",
        "timeout", "plugin_unavailable", "invalid_response",
    ]
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86_400)


class ProviderResponse(MetadataModel):
    """The same response envelope for every provider operation."""

    candidates: tuple[MetadataCandidate, ...] = Field(default=(), max_length=50)
    metadata: MetadataPatch | None = None
    assets: tuple[MediaAsset, ...] = Field(default=(), max_length=200)
    health: ProviderHealth | None = None
    failure: ProviderFailure | None = None
    next_cursor: Identifier | None = None


class ProviderOperations(MetadataModel):
    """Independent optional capabilities, implemented by existing runtime actions."""

    search: ActionId | None = None
    metadata: ActionId | None = None
    media: ActionId | None = None
    health: ActionId


class ProviderConfigurationField(MetadataModel):
    """Author-declared credential ownership rendered by the host configuration UI."""

    key: ActionId
    label: str = Field(min_length=1, max_length=128)
    scope: Literal["system", "user", "both"] = "both"
    required: bool = True
    secret: bool = True


class MetadataProviderRegistration(MetadataModel):
    """Namespaced registration bound by the host to an authenticated installation."""

    provider_id: ActionId
    name: str = Field(min_length=1, max_length=128)
    media_types: tuple[MediaType, ...] = Field(min_length=1, max_length=4)
    identifier_namespace: ActionId | None = None
    metadata_resources: tuple[
        Literal["entity", "episodes", "airing", "relations", "recommendations"], ...
    ] = Field(default=("entity",), min_length=1, max_length=5)
    episode_source: Literal["primary", "fallback"] = "fallback"
    operations: ProviderOperations
    configuration: tuple[ProviderConfigurationField, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def unique_declarations(self) -> "MetadataProviderRegistration":
        """Reject ambiguous media and configuration declarations."""
        keys = [item.key for item in self.configuration]
        if len(keys) != len(set(keys)) or len(self.media_types) != len(set(self.media_types)):
            raise ValueError("provider contains duplicate declarations")
        if len(self.metadata_resources) != len(set(self.metadata_resources)):
            raise ValueError("provider contains duplicate metadata resources")
        if not any((self.operations.search, self.operations.metadata, self.operations.media)):
            raise ValueError("provider must expose an application capability")
        return self
