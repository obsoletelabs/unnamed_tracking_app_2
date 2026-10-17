"""Versioned source-observation contract (version 1).

A payload carries only what the source actually reported. An omitted field is
unknown: it never deletes, clears or resets a stored value. `None` is treated
as omitted, so a client that serialises unset fields as `null` behaves the same
as one that leaves them out.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.database.models.game_identity import SourceKind

CONTRACT_VERSION: Literal[1] = 1

ProviderKey = Annotated[str, Field(pattern=r"^[a-z0-9._-]{1,50}$")]
ProviderIdentity = Annotated[str, Field(min_length=1, max_length=256)]
ShortText = Annotated[str, Field(max_length=128)]

# Scalar columns on the observation, by contract field name.
IDENTITY_FIELDS = ("title", "platform", "edition", "release_date")


class ObservedFacts(BaseModel):
    """Statistics and states a source can report for one record. All optional."""

    model_config = ConfigDict(extra="forbid")

    playtime_seconds: int | None = Field(default=None, ge=0, le=2_000_000_000)
    last_played_at: int | None = Field(default=None, ge=0)
    owned: bool | None = None
    wishlisted: bool | None = None
    installed: bool | None = None
    status: str | None = Field(default=None, max_length=50)
    favorite: bool | None = None
    achievements_total: int | None = Field(default=None, ge=0)
    achievements_unlocked: int | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=20000)
    developer: str | None = Field(default=None, max_length=200)
    publisher: str | None = Field(default=None, max_length=200)
    series: str | None = Field(default=None, max_length=200)
    tags: list[ShortText] | None = Field(default=None, max_length=50)
    features: list[ShortText] | None = Field(default=None, max_length=50)


class SourceConnectionRef(BaseModel):
    """The account, library or installation a source reports through."""

    model_config = ConfigDict(extra="forbid")

    source_kind: Literal["host", "plugin", "manual", "metadata"]
    source: str = Field(min_length=1, max_length=128)
    source_scope: str = Field(min_length=1, max_length=512)
    label: str | None = Field(default=None, max_length=200)

    @field_validator("source", "source_scope")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Use a nonblank source and scope")
        return value

    @model_validator(mode="after")
    def manual_source(self) -> SourceConnectionRef:
        if self.source_kind == SourceKind.MANUAL and self.source != "manual":
            raise ValueError("Manual entries use the source 'manual'")
        return self


class SourceObservationV1(BaseModel):
    """What one source record reported. `external_id` is the stable source ID."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=256)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    platform: str | None = Field(default=None, max_length=50)
    edition: str | None = Field(default=None, max_length=100)
    release_date: date | None = None
    provider_ids: dict[ProviderKey, ProviderIdentity] | None = Field(default=None, max_length=32)
    facts: ObservedFacts | None = None
    # when the source says the record last changed; guards against stale replays
    source_updated_at: int | None = Field(default=None, ge=0)
    # only an explicit report: True re-lists a record, False marks it unavailable
    available: bool | None = None

    @field_validator("external_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Use a nonblank external identity")
        return value

    def reported(self) -> dict[str, Any]:
        """Only the fields the source reported, JSON-ready. Everything else is unknown."""
        out: dict[str, Any] = {}
        for name in ("title", "platform", "edition"):
            if getattr(self, name) is not None:
                out[name] = getattr(self, name)
        if self.release_date is not None:
            out["release_date"] = self.release_date.isoformat()
        if self.provider_ids:
            out["provider_ids"] = dict(self.provider_ids)
        if self.facts is not None:
            facts = self.facts.model_dump(mode="json", exclude_none=True)
            if facts:
                out["facts"] = facts
        if self.source_updated_at is not None:
            out["source_updated_at"] = self.source_updated_at
        if self.available is not None:
            out["available"] = self.available
        return out


class ObservationBatchV1(BaseModel):
    """One connection and a bounded list of distinctly identified records."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[1] = CONTRACT_VERSION
    connection: SourceConnectionRef
    observations: list[SourceObservationV1] = Field(min_length=1, max_length=5000)

    @field_validator("observations")
    @classmethod
    def distinct_identities(cls, items: list[SourceObservationV1]) -> list[SourceObservationV1]:
        if len({item.external_id for item in items}) != len(items):
            raise ValueError("Each batch item must have a distinct external identity")
        return items
