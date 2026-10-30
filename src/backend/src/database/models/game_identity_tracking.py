"""Metric counters, user adjustments, field values with provenance and manual achievements.

Phase 1 of the game identity model (#93); no existing consumer uses these yet.

- A metric counter groups the observations that report the *same* counter for a
  tracked identity (Playnite and a manual entry both counting one Minecraft
  Java install). Different counters are independent and add up; members of one
  counter are reconciled, never summed.
- An adjustment is the user's correction of a counter's effective value, kept
  against the counter rather than written over a source value.
- A field value records an effective field (title, completion status, rating)
  for an identity or an umbrella with its origin, quality and lock. User-owned
  tracking facts are field values with origin `user` and no observation, so they
  are never mistaken for source observations.
- Manual achievements are user-authored and kept apart from provider rows in
  `achievements`.

See wiki/docs/development/game-identity-model.md for the rules.
"""

from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp
from src.database.models.game_identity import sql_values

# Repeated column declarations keep each table's schema contract explicit.
# pylint: disable=duplicate-code


class GameMetric(StrEnum):
    """Metrics that can have counters. Validated in code; each has its own policy."""

    PLAYTIME = "playtime"
    LAST_PLAYED = "last_played"
    ACHIEVEMENTS = "achievements"
    COMPLETION = "completion"
    OWNERSHIP = "ownership"
    INSTALLED = "installed"


class AdjustmentKind(StrEnum):
    """Closed set: an offset from the base, or a replacement value."""

    OFFSET = "offset"
    OVERRIDE = "override"


class FieldValueOrigin(StrEnum):
    """Closed set of where an effective field value came from."""

    USER = "user"
    SOURCE = "source"
    METADATA = "metadata"
    LEGACY = "legacy"
    SYSTEM = "system"


class FieldValueState(StrEnum):
    """Closed set: one current value per field; proposals await a user choice."""

    CURRENT = "current"
    PROPOSED = "proposed"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class FieldQuality(StrEnum):
    """How trustworthy a value is. Validated in code."""

    PLACEHOLDER = "placeholder"
    LOW = "low"
    STANDARD = "standard"
    CANONICAL = "canonical"


class LockOrigin(StrEnum):
    """Closed set: who locked a field. Existing `locked_fields` entries are `legacy`."""

    USER = "user"
    LEGACY = "legacy"
    API_KEY = "api_key"


class ManualFactOrigin(StrEnum):
    """Who authored a manual achievement. Validated in code."""

    USER = "user"
    DELEGATED = "delegated"
    LEGACY = "legacy"


class GameMetricCounter(Base):
    """One counter of one metric for one tracked identity (`games` row).

    Retired counters are archived, so adjustments and history survive a split."""

    __tablename__ = "game_metric_counters"
    __table_args__ = (
        # target of members' and adjustments' metric-checked reference
        UniqueConstraint(
            "user_id", "id", "metric", name="uq_game_metric_counters_user_id_id_metric"
        ),
        ForeignKeyConstraint(
            ["user_id", "game_id"],
            ["games.user_id", "games.id"],
            ondelete="CASCADE",
            name="fk_game_metric_counters_game",
        ),
        CheckConstraint("btrim(metric) <> ''", name="ck_game_metric_counters_metric"),
        Index("ix_game_metric_counters_game_metric", "game_id", "metric"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    game_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    archived_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class GameMetricCounterMember(Base):
    """An observation reporting a counter. One open membership per observation and
    metric, so an observation can never be counted twice for the same metric."""

    __tablename__ = "game_metric_counter_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "counter_id", "metric"],
            [
                "game_metric_counters.user_id",
                "game_metric_counters.id",
                "game_metric_counters.metric",
            ],
            ondelete="CASCADE",
            name="fk_game_metric_counter_members_counter",
        ),
        ForeignKeyConstraint(
            ["user_id", "observation_id"],
            ["game_source_observations.user_id", "game_source_observations.id"],
            ondelete="CASCADE",
            name="fk_game_metric_counter_members_observation",
        ),
        # see GameRelationship: plain key nulls the pointer, deferred key checks the owner
        ForeignKeyConstraint(
            ["decision_id"],
            ["game_identity_decisions.id"],
            ondelete="SET NULL",
            name="fk_game_metric_counter_members_decision",
        ),
        ForeignKeyConstraint(
            ["user_id", "decision_id"],
            ["game_identity_decisions.user_id", "game_identity_decisions.id"],
            deferrable=True,
            initially="DEFERRED",
            name="fk_game_metric_counter_members_decision_owner",
        ),
        CheckConstraint(
            "removed_at IS NULL OR removed_at >= added_at",
            name="ck_game_metric_counter_members_period",
        ),
        Index(
            "uq_game_metric_counter_members_open",
            "observation_id",
            "metric",
            unique=True,
            postgresql_where=text("removed_at IS NULL"),
        ),
        Index("ix_game_metric_counter_members_counter_id", "counter_id"),
        Index("ix_game_metric_counter_members_observation_id", "observation_id"),
        Index(
            "ix_game_metric_counter_members_decision_id",
            "decision_id",
            postgresql_where=text("decision_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    counter_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # repeats the counter's metric so the database can keep membership per metric
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    observation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # explicit source preference within the counter; lower wins
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    decision_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    added_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    removed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class GameMetricAdjustment(Base):
    """The user's correction of a counter's effective value.

    Stored as an offset against the selected base (or an override) so a later
    source report moves the effective value instead of erasing the correction.
    A new adjustment supersedes the current one; at most one is current."""

    __tablename__ = "game_metric_adjustments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "counter_id", "metric"],
            [
                "game_metric_counters.user_id",
                "game_metric_counters.id",
                "game_metric_counters.metric",
            ],
            ondelete="CASCADE",
            name="fk_game_metric_adjustments_counter",
        ),
        ForeignKeyConstraint(
            ["user_id", "base_observation_id"],
            ["game_source_observations.user_id", "game_source_observations.id"],
            name="fk_game_metric_adjustments_base_observation",
        ),
        CheckConstraint(
            f"kind IN ({sql_values(AdjustmentKind)})", name="ck_game_metric_adjustments_kind"
        ),
        CheckConstraint(
            "(kind = 'offset' AND offset_value IS NOT NULL AND override_value IS NULL)"
            " OR (kind = 'override' AND override_value IS NOT NULL AND offset_value IS NULL)",
            name="ck_game_metric_adjustments_value",
        ),
        Index(
            "uq_game_metric_adjustments_current",
            "counter_id",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        Index("ix_game_metric_adjustments_counter_id", "counter_id"),
        Index(
            "ix_game_metric_adjustments_base_observation_id",
            "base_observation_id",
            postgresql_where=text("base_observation_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    counter_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # e.g. -36000 seconds of playtime
    offset_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    override_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    # the base the user corrected, and its value then, so a changed or split base
    # can be rebased deliberately instead of applying the offset to another source
    base_observation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    base_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    superseded_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class GameFieldValue(Base):
    """An effective field value of a tracked identity or an umbrella, with provenance.

    One `current` value per target and field; earlier values stay as
    `superseded`, and a source change awaiting the user (a rename of a locked
    title) is `proposed` until accepted or `rejected`."""

    __tablename__ = "game_field_values"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "game_id"],
            ["games.user_id", "games.id"],
            ondelete="CASCADE",
            name="fk_game_field_values_game",
        ),
        ForeignKeyConstraint(
            ["user_id", "umbrella_id"],
            ["game_umbrellas.user_id", "game_umbrellas.id"],
            name="fk_game_field_values_umbrella",
        ),
        ForeignKeyConstraint(
            ["user_id", "observation_id"],
            ["game_source_observations.user_id", "game_source_observations.id"],
            name="fk_game_field_values_observation",
        ),
        CheckConstraint(
            "(game_id IS NULL) <> (umbrella_id IS NULL)", name="ck_game_field_values_one_target"
        ),
        CheckConstraint(
            f"origin IN ({sql_values(FieldValueOrigin)})", name="ck_game_field_values_origin"
        ),
        CheckConstraint(
            f"state IN ({sql_values(FieldValueState)})", name="ck_game_field_values_state"
        ),
        # a value that came from a source names the observation, and one from a metadata
        # provider names the observation or the provider; a user-typed one names neither
        CheckConstraint(
            "(origin <> 'source' OR observation_id IS NOT NULL)"
            " AND (origin <> 'metadata' OR observation_id IS NOT NULL OR source_label IS NOT NULL)"
            " AND (origin <> 'user' OR (observation_id IS NULL AND source_label IS NULL))",
            name="ck_game_field_values_observation",
        ),
        CheckConstraint(
            f"locked = (lock_origin IS NOT NULL) AND "
            f"(lock_origin IS NULL OR lock_origin IN ({sql_values(LockOrigin)}))",
            name="ck_game_field_values_lock",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_game_field_values_confidence",
        ),
        CheckConstraint("btrim(field) <> ''", name="ck_game_field_values_field"),
        Index(
            "uq_game_field_values_current_game",
            "game_id",
            "field",
            unique=True,
            postgresql_where=text("state = 'current' AND game_id IS NOT NULL"),
        ),
        Index(
            "uq_game_field_values_current_umbrella",
            "umbrella_id",
            "field",
            unique=True,
            postgresql_where=text("state = 'current' AND umbrella_id IS NOT NULL"),
        ),
        Index(
            "uq_game_field_values_open_proposal",
            "game_id",
            "umbrella_id",
            "field",
            "observation_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("state = 'proposed'"),
        ),
        Index(
            "ix_game_field_values_game_id",
            "game_id",
            postgresql_where=text("game_id IS NOT NULL"),
        ),
        Index(
            "ix_game_field_values_umbrella_id",
            "umbrella_id",
            postgresql_where=text("umbrella_id IS NOT NULL"),
        ),
        Index(
            "ix_game_field_values_observation_id",
            "observation_id",
            postgresql_where=text("observation_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    game_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    umbrella_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # a `games` column name or a tracking fact such as completion_status
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FieldValueState.CURRENT.value, server_default="current"
    )
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    observation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # the metadata provider that supplied the value, when no source observation exists
    source_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quality: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FieldQuality.STANDARD.value, server_default="standard"
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    # when the source observed it, and when a person last edited it (separate
    # from recorded_at, which is only when this row was written)
    observed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    edited_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    recorded_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    resolved_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    lock_origin: Mapped[str | None] = mapped_column(String(20), nullable=True)


class GameManualAchievement(Base):
    """A user-authored achievement for a tracked identity or a whole umbrella.

    Provider achievements stay in `achievements`; nothing here is replaced by a
    sync. Removing one archives it. `match_key` lets the user say explicitly that
    achievements on different versions are the same achievement."""

    __tablename__ = "game_manual_achievements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "game_id"],
            ["games.user_id", "games.id"],
            ondelete="CASCADE",
            name="fk_game_manual_achievements_game",
        ),
        ForeignKeyConstraint(
            ["user_id", "umbrella_id"],
            ["game_umbrellas.user_id", "game_umbrellas.id"],
            name="fk_game_manual_achievements_umbrella",
        ),
        CheckConstraint(
            "(game_id IS NULL) <> (umbrella_id IS NULL)",
            name="ck_game_manual_achievements_one_target",
        ),
        CheckConstraint(
            "achieved_at IS NULL OR achieved", name="ck_game_manual_achievements_achieved_at"
        ),
        CheckConstraint("btrim(title) <> ''", name="ck_game_manual_achievements_title"),
        CheckConstraint("position >= 0", name="ck_game_manual_achievements_position"),
        Index(
            "ix_game_manual_achievements_game_id",
            "game_id",
            postgresql_where=text("game_id IS NOT NULL"),
        ),
        Index(
            "ix_game_manual_achievements_umbrella_id",
            "umbrella_id",
            postgresql_where=text("umbrella_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    game_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    umbrella_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    achieved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    achieved_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    match_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ManualFactOrigin.USER.value, server_default="user"
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=unix_timestamp, onupdate=unix_timestamp
    )
    archived_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
