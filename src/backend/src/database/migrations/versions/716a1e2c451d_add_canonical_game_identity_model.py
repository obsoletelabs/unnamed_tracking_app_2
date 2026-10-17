"""Add the canonical game identity model.

Revision ID: 716a1e2c451d
Revises: c10098e58426
Create Date: 2026-10-16 21:59:01.629160

Phase 1 of the game identity overhaul (#93). Generated with Alembic and
reviewed by hand. The migration is purely additive: it creates the identity
tables beside `games` and adds the `(user_id, id)` key on `games` that their
composite foreign keys reference. No existing table, column, index or row is
changed or removed, and no consumer reads the new tables yet.

Review notes:
- Autogenerate created `uq_games_user_id_id` last; it must exist before the
  tables whose foreign keys reference it, so it is created first.
- Every operation is create-if-missing so database adoption can replay it.
- Autogenerate proposed no operations on the retained legacy tables (`cards`,
  `sets`, `bounties`, `bounty_*`); env.py filters them and none are added here.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.database import migration_helpers as h

revision: str = "716a1e2c451d"
down_revision: str | None = "e4a0157eef4b"
branch_labels: str | None = None
depends_on: str | None = None

GAMES_USER_KEY = "uq_games_user_id_id"

# dependents first, for downgrade
NEW_TABLES = (
    "game_metric_adjustments",
    "game_metric_counter_members",
    "game_metric_counters",
    "game_field_values",
    "game_manual_achievements",
    "game_source_observation_links",
    "game_source_observation_revisions",
    "game_relationships",
    "game_identity_decisions",
    "game_source_observations",
    "game_source_connections",
    "game_identities",
    "game_umbrellas",
)


def _create_index(name: str, table: str, columns: list[str], **kw: object) -> None:
    # migration_helpers.create_index_if_missing has no NULLS NOT DISTINCT option
    if not h.has_index(table, name):
        op.create_index(name, table, columns, **kw)


def _has_games_user_key() -> bool:
    # inspected in the current schema; helpers.has_constraint searches all schemas
    inspector = sa.inspect(op.get_bind())
    return GAMES_USER_KEY in {item["name"] for item in inspector.get_unique_constraints("games")}


def upgrade() -> None:
    if not _has_games_user_key():
        op.create_unique_constraint(GAMES_USER_KEY, "games", ["user_id", "id"])
    h.create_table_if_missing(
        "game_source_connections",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("source_kind", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("source_scope", sa.String(length=512), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("connected_at", sa.BigInteger(), nullable=False),
        sa.Column("disconnected_at", sa.BigInteger(), nullable=True),
        sa.Column("last_complete_inventory_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "btrim(source) <> '' AND btrim(source_scope) <> ''",
            name="ck_game_source_connections_nonblank",
        ),
        sa.CheckConstraint(
            "source_kind <> 'manual' OR source = 'manual'",
            name="ck_game_source_connections_manual_source",
        ),
        sa.CheckConstraint(
            "source_kind IN ('host', 'plugin', 'manual', 'metadata')",
            name="ck_game_source_connections_source_kind",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "id", name="uq_game_source_connections_user_id_id"),
        sa.UniqueConstraint(
            "user_id",
            "source_kind",
            "source",
            "source_scope",
            name="uq_game_source_connections_identity",
        ),
    )
    h.create_table_if_missing(
        "game_umbrellas",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), server_default="game", nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("sort_title", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("archived_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_game_umbrellas_title"),
        sa.CheckConstraint(
            "kind IN ('game', 'series', 'collection')", name="ck_game_umbrellas_kind"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "id", "kind", name="uq_game_umbrellas_user_id_id_kind"),
        sa.UniqueConstraint("user_id", "id", name="uq_game_umbrellas_user_id_id"),
    )
    _create_index(
        "ix_game_umbrellas_user_sort_title_active",
        "game_umbrellas",
        ["user_id", "sort_title"],
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    h.create_table_if_missing(
        "game_identities",
        sa.Column("game_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("umbrella_id", sa.UUID(), nullable=False),
        sa.Column("umbrella_kind", sa.String(length=20), server_default="game", nullable=False),
        sa.Column("kind", sa.String(length=20), server_default="standard", nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("edition", sa.String(length=100), nullable=True),
        sa.Column("presentation", sa.String(length=20), server_default="grouped", nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("umbrella_kind = 'game'", name="ck_game_identities_umbrella_kind"),
        sa.CheckConstraint("position >= 0", name="ck_game_identities_position"),
        sa.ForeignKeyConstraint(
            ["user_id", "game_id"],
            ["games.user_id", "games.id"],
            name="fk_game_identities_game",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "umbrella_id", "umbrella_kind"],
            ["game_umbrellas.user_id", "game_umbrellas.id", "game_umbrellas.kind"],
            name="fk_game_identities_umbrella",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("game_id"),
    )
    _create_index("ix_game_identities_umbrella_id", "game_identities", ["umbrella_id"])
    _create_index(
        "uq_game_identities_one_primary",
        "game_identities",
        ["umbrella_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )
    h.create_table_if_missing(
        "game_manual_achievements",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("game_id", sa.UUID(), nullable=True),
        sa.Column("umbrella_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon_url", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("achieved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("achieved_at", sa.BigInteger(), nullable=True),
        sa.Column("match_key", sa.String(length=200), nullable=True),
        sa.Column("origin", sa.String(length=20), server_default="user", nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("archived_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_game_manual_achievements_title"),
        sa.CheckConstraint(
            "(game_id IS NULL) <> (umbrella_id IS NULL)",
            name="ck_game_manual_achievements_one_target",
        ),
        sa.CheckConstraint(
            "achieved_at IS NULL OR achieved", name="ck_game_manual_achievements_achieved_at"
        ),
        sa.CheckConstraint("position >= 0", name="ck_game_manual_achievements_position"),
        sa.ForeignKeyConstraint(
            ["user_id", "game_id"],
            ["games.user_id", "games.id"],
            name="fk_game_manual_achievements_game",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "umbrella_id"],
            ["game_umbrellas.user_id", "game_umbrellas.id"],
            name="fk_game_manual_achievements_umbrella",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_index(
        "ix_game_manual_achievements_game_id",
        "game_manual_achievements",
        ["game_id"],
        postgresql_where=sa.text("game_id IS NOT NULL"),
    )
    _create_index(
        "ix_game_manual_achievements_umbrella_id",
        "game_manual_achievements",
        ["umbrella_id"],
        postgresql_where=sa.text("umbrella_id IS NOT NULL"),
    )
    h.create_table_if_missing(
        "game_metric_counters",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("game_id", sa.UUID(), nullable=False),
        sa.Column("metric", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("archived_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("btrim(metric) <> ''", name="ck_game_metric_counters_metric"),
        sa.ForeignKeyConstraint(
            ["user_id", "game_id"],
            ["games.user_id", "games.id"],
            name="fk_game_metric_counters_game",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "id", "metric", name="uq_game_metric_counters_user_id_id_metric"
        ),
    )
    _create_index(
        "ix_game_metric_counters_game_metric", "game_metric_counters", ["game_id", "metric"]
    )
    h.create_table_if_missing(
        "game_source_observations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("external_id", sa.String(length=256), nullable=False),
        sa.Column("observed_title", sa.String(length=500), nullable=True),
        sa.Column("observed_platform", sa.String(length=50), nullable=True),
        sa.Column("observed_edition", sa.String(length=100), nullable=True),
        sa.Column("observed_release_date", sa.Date(), nullable=True),
        sa.Column(
            "observed_provider_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "observed_facts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "field_observed_at",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("source_updated_at", sa.BigInteger(), nullable=True),
        sa.Column("availability", sa.String(length=20), server_default="available", nullable=False),
        sa.Column("unavailable_since", sa.BigInteger(), nullable=True),
        sa.Column("payload_digest", sa.String(length=64), nullable=True),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("first_observed_at", sa.BigInteger(), nullable=False),
        sa.Column("last_observed_at", sa.BigInteger(), nullable=False),
        sa.Column("archived_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "availability IN ('available', 'unavailable', 'unknown')",
            name="ck_game_source_observations_availability",
        ),
        sa.CheckConstraint(
            "btrim(external_id) <> ''", name="ck_game_source_observations_external_id"
        ),
        sa.CheckConstraint(
            "unavailable_since IS NULL OR availability = 'unavailable'",
            name="ck_game_source_observations_unavailable_since",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_game_source_observations_revision"),
        sa.ForeignKeyConstraint(
            ["user_id", "connection_id"],
            ["game_source_connections.user_id", "game_source_connections.id"],
            name="fk_game_source_observations_connection",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id", "external_id", name="uq_game_source_observations_record"
        ),
        sa.UniqueConstraint("user_id", "id", name="uq_game_source_observations_user_id_id"),
    )
    h.create_table_if_missing(
        "game_field_values",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("game_id", sa.UUID(), nullable=True),
        sa.Column("umbrella_id", sa.UUID(), nullable=True),
        sa.Column("field", sa.String(length=64), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("state", sa.String(length=20), server_default="current", nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("observation_id", sa.UUID(), nullable=True),
        sa.Column("quality", sa.String(length=20), server_default="standard", nullable=False),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("observed_at", sa.BigInteger(), nullable=True),
        sa.Column("edited_at", sa.BigInteger(), nullable=True),
        sa.Column("recorded_at", sa.BigInteger(), nullable=False),
        sa.Column("resolved_at", sa.BigInteger(), nullable=True),
        sa.Column("locked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("lock_origin", sa.String(length=20), nullable=True),
        sa.CheckConstraint(
            "locked = (lock_origin IS NOT NULL) AND (lock_origin IS NULL OR lock_origin IN ('user', 'legacy', 'api_key'))",
            name="ck_game_field_values_lock",
        ),
        sa.CheckConstraint(
            "(origin NOT IN ('source', 'metadata') OR observation_id IS NOT NULL) AND (origin <> 'user' OR observation_id IS NULL)",
            name="ck_game_field_values_observation",
        ),
        sa.CheckConstraint("btrim(field) <> ''", name="ck_game_field_values_field"),
        sa.CheckConstraint(
            "origin IN ('user', 'source', 'metadata', 'legacy', 'system')",
            name="ck_game_field_values_origin",
        ),
        sa.CheckConstraint(
            "state IN ('current', 'proposed', 'superseded', 'rejected')",
            name="ck_game_field_values_state",
        ),
        sa.CheckConstraint(
            "(game_id IS NULL) <> (umbrella_id IS NULL)", name="ck_game_field_values_one_target"
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_game_field_values_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "game_id"],
            ["games.user_id", "games.id"],
            name="fk_game_field_values_game",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "observation_id"],
            ["game_source_observations.user_id", "game_source_observations.id"],
            name="fk_game_field_values_observation",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "umbrella_id"],
            ["game_umbrellas.user_id", "game_umbrellas.id"],
            name="fk_game_field_values_umbrella",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_index(
        "ix_game_field_values_game_id",
        "game_field_values",
        ["game_id"],
        postgresql_where=sa.text("game_id IS NOT NULL"),
    )
    _create_index(
        "ix_game_field_values_observation_id",
        "game_field_values",
        ["observation_id"],
        postgresql_where=sa.text("observation_id IS NOT NULL"),
    )
    _create_index(
        "ix_game_field_values_umbrella_id",
        "game_field_values",
        ["umbrella_id"],
        postgresql_where=sa.text("umbrella_id IS NOT NULL"),
    )
    _create_index(
        "uq_game_field_values_current_game",
        "game_field_values",
        ["game_id", "field"],
        unique=True,
        postgresql_where=sa.text("state = 'current' AND game_id IS NOT NULL"),
    )
    _create_index(
        "uq_game_field_values_current_umbrella",
        "game_field_values",
        ["umbrella_id", "field"],
        unique=True,
        postgresql_where=sa.text("state = 'current' AND umbrella_id IS NOT NULL"),
    )
    _create_index(
        "uq_game_field_values_open_proposal",
        "game_field_values",
        ["game_id", "umbrella_id", "field", "observation_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("state = 'proposed'"),
    )
    h.create_table_if_missing(
        "game_identity_decisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("subject_kind", sa.String(length=30), nullable=False),
        sa.Column("first_game_id", sa.UUID(), nullable=True),
        sa.Column("second_game_id", sa.UUID(), nullable=True),
        sa.Column("observation_id", sa.UUID(), nullable=True),
        sa.Column("first_umbrella_id", sa.UUID(), nullable=True),
        sa.Column("second_umbrella_id", sa.UUID(), nullable=True),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("actor_kind", sa.String(length=20), nullable=False),
        sa.Column("actor_detail", sa.String(length=200), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("rule_version", sa.String(length=50), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("prior_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("resulting_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("superseded_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "(subject_kind = 'identity_pair' AND first_game_id IS NOT NULL AND second_game_id IS NOT NULL AND first_game_id < second_game_id AND observation_id IS NULL AND first_umbrella_id IS NULL AND second_umbrella_id IS NULL) OR (subject_kind = 'observation_identity' AND observation_id IS NOT NULL AND second_game_id IS NOT NULL AND first_game_id IS NULL AND first_umbrella_id IS NULL AND second_umbrella_id IS NULL) OR (subject_kind = 'identity_umbrella' AND first_game_id IS NOT NULL AND second_umbrella_id IS NOT NULL AND second_game_id IS NULL AND observation_id IS NULL AND first_umbrella_id IS NULL) OR (subject_kind = 'umbrella_pair' AND first_umbrella_id IS NOT NULL AND second_umbrella_id IS NOT NULL AND first_umbrella_id < second_umbrella_id AND first_game_id IS NULL AND second_game_id IS NULL AND observation_id IS NULL)",
            name="ck_game_identity_decisions_subject",
        ),
        sa.CheckConstraint(
            "actor_kind IN ('user', 'automatic', 'migration', 'source')",
            name="ck_game_identity_decisions_actor_kind",
        ),
        sa.CheckConstraint(
            "outcome <> 'automatic_same' OR (actor_kind = 'automatic' AND reason IS NOT NULL AND rule_version IS NOT NULL AND confidence IS NOT NULL)",
            name="ck_game_identity_decisions_automatic_explained",
        ),
        sa.CheckConstraint(
            "outcome IN ('unresolved', 'confirmed_same', 'confirmed_separate', 'automatic_same')",
            name="ck_game_identity_decisions_outcome",
        ),
        sa.CheckConstraint(
            "outcome NOT IN ('confirmed_same', 'confirmed_separate') OR actor_kind IN ('user', 'migration')",
            name="ck_game_identity_decisions_confirmed_by_user",
        ),
        sa.CheckConstraint(
            "subject_kind IN ('identity_pair', 'observation_identity', 'identity_umbrella', 'umbrella_pair')",
            name="ck_game_identity_decisions_subject_kind",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_game_identity_decisions_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "first_game_id"],
            ["games.user_id", "games.id"],
            name="fk_game_identity_decisions_first_game",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "first_umbrella_id"],
            ["game_umbrellas.user_id", "game_umbrellas.id"],
            name="fk_game_identity_decisions_first_umbrella",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "observation_id"],
            ["game_source_observations.user_id", "game_source_observations.id"],
            name="fk_game_identity_decisions_observation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "second_game_id"],
            ["games.user_id", "games.id"],
            name="fk_game_identity_decisions_second_game",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "second_umbrella_id"],
            ["game_umbrellas.user_id", "game_umbrellas.id"],
            name="fk_game_identity_decisions_second_umbrella",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "id", name="uq_game_identity_decisions_user_id_id"),
    )
    _create_index(
        "ix_game_identity_decisions_current_outcome",
        "game_identity_decisions",
        ["user_id", "outcome"],
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    _create_index(
        "ix_game_identity_decisions_first_game_id",
        "game_identity_decisions",
        ["first_game_id"],
        postgresql_where=sa.text("first_game_id IS NOT NULL"),
    )
    _create_index(
        "ix_game_identity_decisions_first_umbrella_id",
        "game_identity_decisions",
        ["first_umbrella_id"],
        postgresql_where=sa.text("first_umbrella_id IS NOT NULL"),
    )
    _create_index(
        "ix_game_identity_decisions_observation_id",
        "game_identity_decisions",
        ["observation_id"],
        postgresql_where=sa.text("observation_id IS NOT NULL"),
    )
    _create_index(
        "ix_game_identity_decisions_second_game_id",
        "game_identity_decisions",
        ["second_game_id"],
        postgresql_where=sa.text("second_game_id IS NOT NULL"),
    )
    _create_index(
        "ix_game_identity_decisions_second_umbrella_id",
        "game_identity_decisions",
        ["second_umbrella_id"],
        postgresql_where=sa.text("second_umbrella_id IS NOT NULL"),
    )
    _create_index(
        "uq_game_identity_decisions_current_subject",
        "game_identity_decisions",
        [
            "user_id",
            "subject_kind",
            "first_game_id",
            "second_game_id",
            "observation_id",
            "first_umbrella_id",
            "second_umbrella_id",
        ],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    h.create_table_if_missing(
        "game_metric_adjustments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("counter_id", sa.UUID(), nullable=False),
        sa.Column("metric", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("offset_value", sa.BigInteger(), nullable=True),
        sa.Column("override_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("base_observation_id", sa.UUID(), nullable=True),
        sa.Column("base_value", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("superseded_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "(kind = 'offset' AND offset_value IS NOT NULL AND override_value IS NULL) OR (kind = 'override' AND override_value IS NOT NULL AND offset_value IS NULL)",
            name="ck_game_metric_adjustments_value",
        ),
        sa.CheckConstraint(
            "kind IN ('offset', 'override')", name="ck_game_metric_adjustments_kind"
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "base_observation_id"],
            ["game_source_observations.user_id", "game_source_observations.id"],
            name="fk_game_metric_adjustments_base_observation",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "counter_id", "metric"],
            [
                "game_metric_counters.user_id",
                "game_metric_counters.id",
                "game_metric_counters.metric",
            ],
            name="fk_game_metric_adjustments_counter",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_index(
        "ix_game_metric_adjustments_base_observation_id",
        "game_metric_adjustments",
        ["base_observation_id"],
        postgresql_where=sa.text("base_observation_id IS NOT NULL"),
    )
    _create_index(
        "ix_game_metric_adjustments_counter_id", "game_metric_adjustments", ["counter_id"]
    )
    _create_index(
        "uq_game_metric_adjustments_current",
        "game_metric_adjustments",
        ["counter_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    h.create_table_if_missing(
        "game_source_observation_revisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("observation_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "changes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("observed_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_game_source_observation_revisions_number"),
        sa.ForeignKeyConstraint(
            ["user_id", "observation_id"],
            ["game_source_observations.user_id", "game_source_observations.id"],
            name="fk_game_source_observation_revisions_observation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "observation_id", "revision", name="uq_game_source_observation_revisions_number"
        ),
    )
    h.create_table_if_missing(
        "game_metric_counter_members",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("counter_id", sa.UUID(), nullable=False),
        sa.Column("metric", sa.String(length=40), nullable=False),
        sa.Column("observation_id", sa.UUID(), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("decision_id", sa.UUID(), nullable=True),
        sa.Column("added_at", sa.BigInteger(), nullable=False),
        sa.Column("removed_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "removed_at IS NULL OR removed_at >= added_at",
            name="ck_game_metric_counter_members_period",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["game_identity_decisions.id"],
            name="fk_game_metric_counter_members_decision",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "counter_id", "metric"],
            [
                "game_metric_counters.user_id",
                "game_metric_counters.id",
                "game_metric_counters.metric",
            ],
            name="fk_game_metric_counter_members_counter",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "decision_id"],
            ["game_identity_decisions.user_id", "game_identity_decisions.id"],
            name="fk_game_metric_counter_members_decision_owner",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "observation_id"],
            ["game_source_observations.user_id", "game_source_observations.id"],
            name="fk_game_metric_counter_members_observation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_index(
        "ix_game_metric_counter_members_counter_id", "game_metric_counter_members", ["counter_id"]
    )
    _create_index(
        "ix_game_metric_counter_members_decision_id",
        "game_metric_counter_members",
        ["decision_id"],
        postgresql_where=sa.text("decision_id IS NOT NULL"),
    )
    _create_index(
        "ix_game_metric_counter_members_observation_id",
        "game_metric_counter_members",
        ["observation_id"],
    )
    _create_index(
        "uq_game_metric_counter_members_open",
        "game_metric_counter_members",
        ["observation_id", "metric"],
        unique=True,
        postgresql_where=sa.text("removed_at IS NULL"),
    )
    h.create_table_if_missing(
        "game_relationships",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=100), server_default="", nullable=False),
        sa.Column("from_game_id", sa.UUID(), nullable=True),
        sa.Column("from_umbrella_id", sa.UUID(), nullable=True),
        sa.Column("to_game_id", sa.UUID(), nullable=True),
        sa.Column("to_umbrella_id", sa.UUID(), nullable=True),
        sa.Column("display", sa.String(length=20), server_default="default", nullable=False),
        sa.Column("origin", sa.String(length=20), server_default="user", nullable=False),
        sa.Column("decision_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("ended_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "(kind = 'custom' AND btrim(label) <> '') OR (kind <> 'custom' AND label = '')",
            name="ck_game_relationships_custom_label",
        ),
        sa.CheckConstraint("btrim(kind) <> ''", name="ck_game_relationships_kind"),
        sa.CheckConstraint(
            "kind <> 'same_game' OR (from_game_id IS NULL) = (to_game_id IS NULL)",
            name="ck_game_relationships_same_game_level",
        ),
        sa.CheckConstraint(
            "(from_game_id IS NULL) <> (from_umbrella_id IS NULL)",
            name="ck_game_relationships_one_from",
        ),
        sa.CheckConstraint(
            "(to_game_id IS NULL) <> (to_umbrella_id IS NULL)", name="ck_game_relationships_one_to"
        ),
        sa.CheckConstraint(
            "from_game_id <> to_game_id AND from_umbrella_id <> to_umbrella_id",
            name="ck_game_relationships_not_self",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["game_identity_decisions.id"],
            name="fk_game_relationships_decision",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "decision_id"],
            ["game_identity_decisions.user_id", "game_identity_decisions.id"],
            name="fk_game_relationships_decision_owner",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "from_game_id"],
            ["games.user_id", "games.id"],
            name="fk_game_relationships_from_game",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "from_umbrella_id"],
            ["game_umbrellas.user_id", "game_umbrellas.id"],
            name="fk_game_relationships_from_umbrella",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "to_game_id"],
            ["games.user_id", "games.id"],
            name="fk_game_relationships_to_game",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "to_umbrella_id"],
            ["game_umbrellas.user_id", "game_umbrellas.id"],
            name="fk_game_relationships_to_umbrella",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_index(
        "ix_game_relationships_decision_id",
        "game_relationships",
        ["decision_id"],
        postgresql_where=sa.text("decision_id IS NOT NULL"),
    )
    _create_index(
        "ix_game_relationships_from_game_id",
        "game_relationships",
        ["from_game_id"],
        postgresql_where=sa.text("from_game_id IS NOT NULL"),
    )
    _create_index(
        "ix_game_relationships_from_umbrella_id",
        "game_relationships",
        ["from_umbrella_id"],
        postgresql_where=sa.text("from_umbrella_id IS NOT NULL"),
    )
    _create_index(
        "ix_game_relationships_to_game_id",
        "game_relationships",
        ["to_game_id"],
        postgresql_where=sa.text("to_game_id IS NOT NULL"),
    )
    _create_index(
        "ix_game_relationships_to_umbrella_id",
        "game_relationships",
        ["to_umbrella_id"],
        postgresql_where=sa.text("to_umbrella_id IS NOT NULL"),
    )
    _create_index(
        "uq_game_relationships_active_edge",
        "game_relationships",
        [
            "user_id",
            "kind",
            "label",
            "from_game_id",
            "from_umbrella_id",
            "to_game_id",
            "to_umbrella_id",
        ],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    _create_index(
        "uq_game_relationships_one_same_game",
        "game_relationships",
        ["from_game_id", "from_umbrella_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("kind = 'same_game' AND ended_at IS NULL"),
    )
    h.create_table_if_missing(
        "game_source_observation_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("observation_id", sa.UUID(), nullable=False),
        sa.Column("game_id", sa.UUID(), nullable=False),
        sa.Column("origin", sa.String(length=20), server_default="ingest", nullable=False),
        sa.Column("decision_id", sa.UUID(), nullable=True),
        sa.Column("linked_at", sa.BigInteger(), nullable=False),
        sa.Column("unlinked_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "unlinked_at IS NULL OR unlinked_at >= linked_at",
            name="ck_game_source_observation_links_period",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["game_identity_decisions.id"],
            name="fk_game_source_observation_links_decision",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "decision_id"],
            ["game_identity_decisions.user_id", "game_identity_decisions.id"],
            name="fk_game_source_observation_links_decision_owner",
            initially="DEFERRED",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "game_id"],
            ["games.user_id", "games.id"],
            name="fk_game_source_observation_links_game",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "observation_id"],
            ["game_source_observations.user_id", "game_source_observations.id"],
            name="fk_game_source_observation_links_observation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_index(
        "ix_game_source_observation_links_decision_id",
        "game_source_observation_links",
        ["decision_id"],
        postgresql_where=sa.text("decision_id IS NOT NULL"),
    )
    _create_index(
        "ix_game_source_observation_links_game_id", "game_source_observation_links", ["game_id"]
    )
    _create_index(
        "ix_game_source_observation_links_observation_id",
        "game_source_observation_links",
        ["observation_id"],
    )
    _create_index(
        "uq_game_source_observation_links_open",
        "game_source_observation_links",
        ["observation_id"],
        unique=True,
        postgresql_where=sa.text("unlinked_at IS NULL"),
    )


def downgrade() -> None:
    # Once consumers write these tables they hold user decisions, source
    # history and manual tracking that exist nowhere else. Refuse to drop them
    # with data rather than lose it; an empty schema downgrades cleanly.
    bind = op.get_bind()
    populated = [
        table
        for table in NEW_TABLES
        if h.has_table(table)
        and bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar()
    ]
    if populated:
        raise RuntimeError(
            "Cannot downgrade the game identity model while it holds data: "
            + ", ".join(populated)
            + ". Restore a database dump taken before the upgrade instead."
        )
    for table in NEW_TABLES:
        if h.has_table(table):
            op.drop_table(table)
    if _has_games_user_key():
        op.drop_constraint(GAMES_USER_KEY, "games", type_="unique")
