"""Pydantic schemas for scan and metadata import settings."""

# The scan settings API intentionally mirrors adjacent settings schemas.
import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.database.models.user_scan_settings import (
    LEGACY_DATA_PROVIDER_NAMES,
    LEGACY_IMAGE_PROVIDER_NAMES,
)

# Orders hold registered metadata provider IDs ("core.igdb", "<plugin>.<provider>"), so
# core and optional plugin providers share one order. Orders saved before optional
# providers became plugins hold legacy display names; they stay valid for older
# clients and are resolved against the current provider registry at discovery time.
# Matches metadata provider identifiers accepted by account metadata choices.
PROVIDER_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
MAX_ORDER_ENTRIES = 256


class ScanSettingsUpdate(BaseModel):
    """Partial update — every field optional."""

    provider_order: list[str] | None = Field(default=None, max_length=MAX_ORDER_ENTRIES)
    image_provider_order: list[str] | None = Field(default=None, max_length=MAX_ORDER_ENTRIES)
    save_developer: bool | None = None
    save_publisher: bool | None = None
    save_series: bool | None = None
    save_tags: bool | None = None
    save_features: bool | None = None
    save_description: bool | None = None
    save_age_rating: bool | None = None
    save_release_date: bool | None = None
    save_time_to_beat: bool | None = None
    save_key_art: bool | None = None
    save_banner: bool | None = None
    save_logo: bool | None = None
    save_icon: bool | None = None

    @field_validator("provider_order")
    @classmethod
    def validate_provider_order(cls, value: list[str] | None) -> list[str] | None:
        """Validate the configured order of metadata and search providers."""
        return _validate_order(value, LEGACY_DATA_PROVIDER_NAMES)

    @field_validator("image_provider_order")
    @classmethod
    def validate_image_provider_order(cls, value: list[str] | None) -> list[str] | None:
        """Validate the configured order of artwork providers."""
        return _validate_order(value, LEGACY_IMAGE_PROVIDER_NAMES)


def _validate_order(value: list[str] | None, legacy_names: frozenset[str]) -> list[str] | None:
    if value is None:
        return None
    if len(set(value)) != len(value):
        raise ValueError("provider order cannot contain duplicates.")
    invalid = [
        entry
        for entry in value
        if entry not in legacy_names and not PROVIDER_ID_PATTERN.fullmatch(entry)
    ]
    if invalid:
        raise ValueError(
            "provider order values must be metadata provider IDs or legacy names "
            f"{sorted(legacy_names)}."
        )
    return value


class ScanSettingsRead(BaseModel):
    """Full representation returned to clients."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    provider_order: list[str]
    image_provider_order: list[str]
    save_developer: bool
    save_publisher: bool
    save_series: bool
    save_tags: bool
    save_features: bool
    save_description: bool
    save_age_rating: bool
    save_release_date: bool
    save_time_to_beat: bool
    save_key_art: bool
    save_banner: bool
    save_logo: bool
    save_icon: bool
    # {provider ID: epoch seconds last used} (older rows may also hold display
    # names) — read-only, recorded when a provider supplies a selected, searched
    # or refreshed result; not settable via ScanSettingsUpdate
    provider_last_used: dict[str, int]
    created_at: int
    updated_at: int
