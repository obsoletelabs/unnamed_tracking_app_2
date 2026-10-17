"""Canonical games, tracked identities, source observations, relationships and decisions.

Phase 1 of the game identity model (#93). These tables sit beside `games`; no
existing consumer reads or writes them yet. A `games` row stays the tracked
identity (an edition, platform version or owned copy) and keeps its ID, folder
and children. A source observation is evidence about a game from one source
record; attaching it to a tracked identity is a separate, movable link.
See wiki/docs/development/game-identity-model.md for the rules.

Tenancy: every table carries `user_id`, and every reference to another
user-owned row is a composite foreign key that includes `user_id`, so the
database rejects a row that points at another user's game, umbrella,
observation or decision. Referenced tables therefore expose a unique
`(user_id, id)` key.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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

# Repeated column declarations keep each table's schema contract explicit.
# pylint: disable=duplicate-code


class UmbrellaKind(StrEnum):
    """Closed set: tracked identities may only belong to a `game` umbrella."""

    GAME = "game"
    SERIES = "series"
    COLLECTION = "collection"


class IdentityKind(StrEnum):
    """What a tracked identity is within its umbrella. Validated in code."""

    STANDARD = "standard"
    EDITION = "edition"
    PLATFORM = "platform"
    VERSION = "version"
    COPY = "copy"
    CUSTOM = "custom"


class IdentityPresentation(StrEnum):
    """Library presentation of one identity. Validated in code."""

    GROUPED = "grouped"
    SEPARATE = "separate"


class RelationshipKind(StrEnum):
    """Predefined relationship kinds plus `custom`. Validated in code, except
    that the database knows `same_game` and `custom` have structural rules."""

    SAME_GAME = "same_game"
    VERSION_OF = "version_of"
    REMAKE_OF = "remake_of"
    REMASTER_OF = "remaster_of"
    PORT_OF = "port_of"
    DLC_OF = "dlc_of"
    EXPANSION_OF = "expansion_of"
    STANDALONE_EXPANSION_OF = "standalone_expansion_of"
    MOD_OF = "mod_of"
    MODPACK_OF = "modpack_of"
    TOTAL_CONVERSION_OF = "total_conversion_of"
    SERIES_MEMBER = "series_member"
    COLLECTION_MEMBER = "collection_member"
    RELATED_TO = "related_to"
    CUSTOM = "custom"


class RelationshipDisplay(StrEnum):
    """How a relationship affects the library. Validated in code."""

    DEFAULT = "default"
    LINK = "link"
    NEST = "nest"
    HIDDEN = "hidden"


class RecordOrigin(StrEnum):
    """Who created a structural row. Validated in code."""

    USER = "user"
    INGEST = "ingest"
    AUTOMATIC = "automatic"
    MIGRATION = "migration"


class DecisionSubject(StrEnum):
    """Closed set: decides which subject columns a decision uses."""

    IDENTITY_PAIR = "identity_pair"
    OBSERVATION_IDENTITY = "observation_identity"
    IDENTITY_UMBRELLA = "identity_umbrella"
    UMBRELLA_PAIR = "umbrella_pair"


class DecisionOutcome(StrEnum):
    """Closed set of identity decision states."""

    UNRESOLVED = "unresolved"
    CONFIRMED_SAME = "confirmed_same"
    CONFIRMED_SEPARATE = "confirmed_separate"
    AUTOMATIC_SAME = "automatic_same"


class DecisionActor(StrEnum):
    """Closed set: only users (or a migration of a user's earlier choice) confirm."""

    USER = "user"
    AUTOMATIC = "automatic"
    MIGRATION = "migration"
    SOURCE = "source"


# Scope for records that predate per-account identity (Steam, PlayStation and
# RetroAchievements rows, and Playnite GUIDs without a library) and for the
# user's own manual entries. Never re-key a legacy record by guessing a scope.
LEGACY_SOURCE_SCOPE = "legacy"
MANUAL_SOURCE_SCOPE = "local"


class SourceKind(StrEnum):
    """Closed set: it partitions source names so a plugin can never claim a host source."""

    HOST = "host"
    PLUGIN = "plugin"
    MANUAL = "manual"
    METADATA = "metadata"


class HostSource(StrEnum):
    """Source names for `SourceKind.HOST`. Plugin sources use their plugin ID."""

    STEAM = "steam"
    PLAYNITE = "playnite"
    RETROACHIEVEMENTS = "retroachievements"
    PLAYSTATION = "playstation"


class ObservationAvailability(StrEnum):
    """Closed set. Only a complete inventory or an explicit report may set `unavailable`."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


def sql_values(values: type[StrEnum]) -> str:
    """Render a closed vocabulary for a CHECK constraint."""
    return ", ".join(f"'{member.value}'" for member in values)


class GameUmbrella(Base):
    """The logical game shown once by default (Minecraft), or a series/collection
    that games join through relationships. Archived, never deleted while any
    identity, relationship, decision or tracking fact refers to it."""

    __tablename__ = "game_umbrellas"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_game_umbrellas_user_id_id"),
        # target of game_identities' kind-checked reference
        UniqueConstraint("user_id", "id", "kind", name="uq_game_umbrellas_user_id_id_kind"),
        CheckConstraint(f"kind IN ({sql_values(UmbrellaKind)})", name="ck_game_umbrellas_kind"),
        CheckConstraint("btrim(title) <> ''", name="ck_game_umbrellas_title"),
        Index(
            "ix_game_umbrellas_user_sort_title_active",
            "user_id",
            "sort_title",
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UmbrellaKind.GAME.value, server_default="game"
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    sort_title: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=unix_timestamp, onupdate=unix_timestamp
    )
    archived_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class GameIdentity(Base):
    """Places one existing `games` row (the tracked identity) in a game umbrella.

    The primary key is the games row's ID, so every existing `games.id` is also
    the identity's ID. A games row without a placement is a standalone legacy
    identity until the library migration places it. Purging the games row
    removes the placement; the umbrella stays."""

    __tablename__ = "game_identities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "game_id"],
            ["games.user_id", "games.id"],
            ondelete="CASCADE",
            name="fk_game_identities_game",
        ),
        # Including the umbrella kind makes "only game umbrellas hold identities"
        # a foreign-key rule instead of an application convention.
        ForeignKeyConstraint(
            ["user_id", "umbrella_id", "umbrella_kind"],
            ["game_umbrellas.user_id", "game_umbrellas.id", "game_umbrellas.kind"],
            name="fk_game_identities_umbrella",
        ),
        CheckConstraint("umbrella_kind = 'game'", name="ck_game_identities_umbrella_kind"),
        CheckConstraint("position >= 0", name="ck_game_identities_position"),
        Index("ix_game_identities_umbrella_id", "umbrella_id"),
        Index(
            "uq_game_identities_one_primary",
            "umbrella_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    game_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    umbrella_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    umbrella_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UmbrellaKind.GAME.value, server_default="game"
    )
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default=IdentityKind.STANDARD.value, server_default="standard"
    )
    # "Java Edition", "Mobile"; NULL falls back to the games row's title/platform
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # normalized edition token used for matching and for separate presentation
    edition: Mapped[str | None] = mapped_column(String(100), nullable=True)
    presentation: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=IdentityPresentation.GROUPED.value,
        server_default="grouped",
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=unix_timestamp, onupdate=unix_timestamp
    )


class GameIdentityDecision(Base):
    """One judgement about whether two records are the same, with provenance.

    Rows are append-only: a new decision about the same subject supersedes the
    current one (`superseded_at`), so undo restores from `prior_state` and the
    full history stays. At most one current decision exists per subject."""

    __tablename__ = "game_identity_decisions"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_game_identity_decisions_user_id_id"),
        ForeignKeyConstraint(
            ["user_id", "first_game_id"],
            ["games.user_id", "games.id"],
            ondelete="CASCADE",
            name="fk_game_identity_decisions_first_game",
        ),
        ForeignKeyConstraint(
            ["user_id", "second_game_id"],
            ["games.user_id", "games.id"],
            ondelete="CASCADE",
            name="fk_game_identity_decisions_second_game",
        ),
        ForeignKeyConstraint(
            ["user_id", "observation_id"],
            ["game_source_observations.user_id", "game_source_observations.id"],
            ondelete="CASCADE",
            name="fk_game_identity_decisions_observation",
        ),
        ForeignKeyConstraint(
            ["user_id", "first_umbrella_id"],
            ["game_umbrellas.user_id", "game_umbrellas.id"],
            name="fk_game_identity_decisions_first_umbrella",
        ),
        ForeignKeyConstraint(
            ["user_id", "second_umbrella_id"],
            ["game_umbrellas.user_id", "game_umbrellas.id"],
            name="fk_game_identity_decisions_second_umbrella",
        ),
        CheckConstraint(
            f"subject_kind IN ({sql_values(DecisionSubject)})",
            name="ck_game_identity_decisions_subject_kind",
        ),
        # each subject kind uses exactly its own columns; pairs are ordered so a
        # pair has one key whichever record the user started from
        CheckConstraint(
            "(subject_kind = 'identity_pair' AND first_game_id IS NOT NULL"
            " AND second_game_id IS NOT NULL AND first_game_id < second_game_id"
            " AND observation_id IS NULL AND first_umbrella_id IS NULL"
            " AND second_umbrella_id IS NULL)"
            " OR (subject_kind = 'observation_identity' AND observation_id IS NOT NULL"
            " AND second_game_id IS NOT NULL AND first_game_id IS NULL"
            " AND first_umbrella_id IS NULL AND second_umbrella_id IS NULL)"
            " OR (subject_kind = 'identity_umbrella' AND first_game_id IS NOT NULL"
            " AND second_umbrella_id IS NOT NULL AND second_game_id IS NULL"
            " AND observation_id IS NULL AND first_umbrella_id IS NULL)"
            " OR (subject_kind = 'umbrella_pair' AND first_umbrella_id IS NOT NULL"
            " AND second_umbrella_id IS NOT NULL AND first_umbrella_id < second_umbrella_id"
            " AND first_game_id IS NULL AND second_game_id IS NULL AND observation_id IS NULL)",
            name="ck_game_identity_decisions_subject",
        ),
        CheckConstraint(
            f"outcome IN ({sql_values(DecisionOutcome)})",
            name="ck_game_identity_decisions_outcome",
        ),
        CheckConstraint(
            f"actor_kind IN ({sql_values(DecisionActor)})",
            name="ck_game_identity_decisions_actor_kind",
        ),
        # sources and automatic matching never confirm; only a person does (or a
        # migration carrying a person's earlier choice forward)
        CheckConstraint(
            "outcome NOT IN ('confirmed_same', 'confirmed_separate')"
            " OR actor_kind IN ('user', 'migration')",
            name="ck_game_identity_decisions_confirmed_by_user",
        ),
        CheckConstraint(
            "outcome <> 'automatic_same' OR (actor_kind = 'automatic'"
            " AND reason IS NOT NULL AND rule_version IS NOT NULL AND confidence IS NOT NULL)",
            name="ck_game_identity_decisions_automatic_explained",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_game_identity_decisions_confidence",
        ),
        Index(
            "uq_game_identity_decisions_current_subject",
            "user_id",
            "subject_kind",
            "first_game_id",
            "second_game_id",
            "observation_id",
            "first_umbrella_id",
            "second_umbrella_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        Index(
            "ix_game_identity_decisions_current_outcome",
            "user_id",
            "outcome",
            postgresql_where=text("superseded_at IS NULL"),
        ),
        Index(
            "ix_game_identity_decisions_first_game_id",
            "first_game_id",
            postgresql_where=text("first_game_id IS NOT NULL"),
        ),
        Index(
            "ix_game_identity_decisions_second_game_id",
            "second_game_id",
            postgresql_where=text("second_game_id IS NOT NULL"),
        ),
        Index(
            "ix_game_identity_decisions_observation_id",
            "observation_id",
            postgresql_where=text("observation_id IS NOT NULL"),
        ),
        Index(
            "ix_game_identity_decisions_first_umbrella_id",
            "first_umbrella_id",
            postgresql_where=text("first_umbrella_id IS NOT NULL"),
        ),
        Index(
            "ix_game_identity_decisions_second_umbrella_id",
            "second_umbrella_id",
            postgresql_where=text("second_umbrella_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subject_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    first_game_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    second_game_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    observation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    first_umbrella_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    second_umbrella_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # credential kind, plugin ID or rule name; never a secret
    actor_detail: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    evidence: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # what the decision changed, so undo can restore it exactly
    prior_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    resulting_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    superseded_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class GameRelationship(Base):
    """A typed, directed edge between tracked identities and/or umbrellas.

    Edges are ended (`ended_at`), not deleted, so history stays. Only
    `same_game` is structural: an identity (or umbrella) with an active outgoing
    `same_game` edge is shown through its target. Every other kind, including
    user-defined `custom` edges, only links or nests entries for display and
    never changes membership, counters or aggregation."""

    __tablename__ = "game_relationships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "from_game_id"],
            ["games.user_id", "games.id"],
            ondelete="CASCADE",
            name="fk_game_relationships_from_game",
        ),
        ForeignKeyConstraint(
            ["user_id", "to_game_id"],
            ["games.user_id", "games.id"],
            ondelete="CASCADE",
            name="fk_game_relationships_to_game",
        ),
        ForeignKeyConstraint(
            ["user_id", "from_umbrella_id"],
            ["game_umbrellas.user_id", "game_umbrellas.id"],
            name="fk_game_relationships_from_umbrella",
        ),
        ForeignKeyConstraint(
            ["user_id", "to_umbrella_id"],
            ["game_umbrellas.user_id", "game_umbrellas.id"],
            name="fk_game_relationships_to_umbrella",
        ),
        # PostgreSQL cannot null one column of a composite key, so the plain key
        # clears the pointer when a decision is purged with its game and the
        # deferred composite key proves the decision belongs to the same user.
        ForeignKeyConstraint(
            ["decision_id"],
            ["game_identity_decisions.id"],
            ondelete="SET NULL",
            name="fk_game_relationships_decision",
        ),
        ForeignKeyConstraint(
            ["user_id", "decision_id"],
            ["game_identity_decisions.user_id", "game_identity_decisions.id"],
            deferrable=True,
            initially="DEFERRED",
            name="fk_game_relationships_decision_owner",
        ),
        CheckConstraint(
            "(from_game_id IS NULL) <> (from_umbrella_id IS NULL)",
            name="ck_game_relationships_one_from",
        ),
        CheckConstraint(
            "(to_game_id IS NULL) <> (to_umbrella_id IS NULL)",
            name="ck_game_relationships_one_to",
        ),
        CheckConstraint(
            "from_game_id <> to_game_id AND from_umbrella_id <> to_umbrella_id",
            name="ck_game_relationships_not_self",
        ),
        CheckConstraint(
            "kind <> 'same_game' OR (from_game_id IS NULL) = (to_game_id IS NULL)",
            name="ck_game_relationships_same_game_level",
        ),
        CheckConstraint(
            "(kind = 'custom' AND btrim(label) <> '') OR (kind <> 'custom' AND label = '')",
            name="ck_game_relationships_custom_label",
        ),
        CheckConstraint("btrim(kind) <> ''", name="ck_game_relationships_kind"),
        Index(
            "uq_game_relationships_active_edge",
            "user_id",
            "kind",
            "label",
            "from_game_id",
            "from_umbrella_id",
            "to_game_id",
            "to_umbrella_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        # an identity or umbrella is absorbed into at most one target at a time
        Index(
            "uq_game_relationships_one_same_game",
            "from_game_id",
            "from_umbrella_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("kind = 'same_game' AND ended_at IS NULL"),
        ),
        Index(
            "ix_game_relationships_from_game_id",
            "from_game_id",
            postgresql_where=text("from_game_id IS NOT NULL"),
        ),
        Index(
            "ix_game_relationships_to_game_id",
            "to_game_id",
            postgresql_where=text("to_game_id IS NOT NULL"),
        ),
        Index(
            "ix_game_relationships_from_umbrella_id",
            "from_umbrella_id",
            postgresql_where=text("from_umbrella_id IS NOT NULL"),
        ),
        Index(
            "ix_game_relationships_to_umbrella_id",
            "to_umbrella_id",
            postgresql_where=text("to_umbrella_id IS NOT NULL"),
        ),
        Index(
            "ix_game_relationships_decision_id",
            "decision_id",
            postgresql_where=text("decision_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # user-defined label/tag; required for `custom`, empty for predefined kinds
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    from_game_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    from_umbrella_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    to_game_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    to_umbrella_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    display: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=RelationshipDisplay.DEFAULT.value,
        server_default="default",
    )
    origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RecordOrigin.USER.value, server_default="user"
    )
    decision_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=unix_timestamp, onupdate=unix_timestamp
    )
    ended_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class GameSourceConnection(Base):
    """One account, library or installation a source reports through.

    Disconnecting sets `disconnected_at`; it never touches observations. The
    natural key makes reconnecting the same account reuse the same row."""

    __tablename__ = "game_source_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_game_source_connections_user_id_id"),
        UniqueConstraint(
            "user_id",
            "source_kind",
            "source",
            "source_scope",
            name="uq_game_source_connections_identity",
        ),
        CheckConstraint(
            f"source_kind IN ({sql_values(SourceKind)})",
            name="ck_game_source_connections_source_kind",
        ),
        CheckConstraint(
            "source_kind <> 'manual' OR source = 'manual'",
            name="ck_game_source_connections_manual_source",
        ),
        CheckConstraint(
            "btrim(source) <> '' AND btrim(source_scope) <> ''",
            name="ck_game_source_connections_nonblank",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # host source name, plugin ID, "manual" or metadata provider ID
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    # account ID, Playnite library ID, LEGACY_SOURCE_SCOPE or MANUAL_SOURCE_SCOPE
    source_scope: Mapped[str] = mapped_column(String(512), nullable=False)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    connected_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    disconnected_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # the last time the source delivered a complete inventory; removal may only
    # be inferred from an inventory at least this complete
    last_complete_inventory_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=unix_timestamp, onupdate=unix_timestamp
    )


class GameSourceObservation(Base):
    """The latest facts one source record reported, keyed for idempotent ingestion.

    `(connection_id, external_id)` is the stable source identity; a rename or a
    changed payload updates this row and appends a revision, never a new row.
    Only fields the source actually reported appear in `observed_facts` and
    `field_observed_at`, so an omitted field is unknown rather than reset."""

    __tablename__ = "game_source_observations"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_game_source_observations_user_id_id"),
        UniqueConstraint("connection_id", "external_id", name="uq_game_source_observations_record"),
        ForeignKeyConstraint(
            ["user_id", "connection_id"],
            ["game_source_connections.user_id", "game_source_connections.id"],
            name="fk_game_source_observations_connection",
        ),
        CheckConstraint(
            f"availability IN ({sql_values(ObservationAvailability)})",
            name="ck_game_source_observations_availability",
        ),
        CheckConstraint(
            "unavailable_since IS NULL OR availability = 'unavailable'",
            name="ck_game_source_observations_unavailable_since",
        ),
        CheckConstraint("btrim(external_id) <> ''", name="ck_game_source_observations_external_id"),
        CheckConstraint("revision >= 1", name="ck_game_source_observations_revision"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # Steam appid, Playnite GUID, plugin external ID, manual entry ID, provider ID
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    # identity evidence, kept raw: the name exactly as the source reported it
    observed_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    observed_platform: Mapped[str | None] = mapped_column(String(50), nullable=True)
    observed_edition: Mapped[str | None] = mapped_column(String(100), nullable=True)
    observed_release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    observed_provider_ids: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # every other reported field: playtime_seconds, last_played_at, status,
    # owned, installed, achievements_total, achievements_unlocked, ...
    observed_facts: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # field name -> epoch seconds it was last reported, so recency is judged
    # per field (a newer name does not make this source win playtime)
    field_observed_at: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    source_updated_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    availability: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ObservationAvailability.AVAILABLE.value,
        server_default="available",
    )
    unavailable_since: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # digest of the last applied normalized payload; an identical replay is a no-op
    payload_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    first_observed_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=unix_timestamp
    )
    last_observed_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=unix_timestamp
    )
    archived_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=unix_timestamp, onupdate=unix_timestamp
    )


class GameSourceObservationRevision(Base):
    """An append-only record of what changed in one observation, for audit and undo."""

    __tablename__ = "game_source_observation_revisions"
    __table_args__ = (
        UniqueConstraint(
            "observation_id", "revision", name="uq_game_source_observation_revisions_number"
        ),
        ForeignKeyConstraint(
            ["user_id", "observation_id"],
            ["game_source_observations.user_id", "game_source_observations.id"],
            ondelete="CASCADE",
            name="fk_game_source_observation_revisions_observation",
        ),
        CheckConstraint("revision >= 1", name="ck_game_source_observation_revisions_number"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    observation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # only the fields this revision reported: {field: value}
    changes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    observed_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)


class GameSourceObservationLink(Base):
    """Attaches an observation to a tracked identity (`games` row), with history.

    Moving an observation ends the current link and opens a new one; the
    observation and its external ID never change. At most one link per
    observation is open. Purging a games row removes its links and leaves the
    observation detached, so its source identity is still known."""

    __tablename__ = "game_source_observation_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "observation_id"],
            ["game_source_observations.user_id", "game_source_observations.id"],
            ondelete="CASCADE",
            name="fk_game_source_observation_links_observation",
        ),
        ForeignKeyConstraint(
            ["user_id", "game_id"],
            ["games.user_id", "games.id"],
            ondelete="CASCADE",
            name="fk_game_source_observation_links_game",
        ),
        # see GameRelationship: plain key nulls the pointer, deferred key checks the owner
        ForeignKeyConstraint(
            ["decision_id"],
            ["game_identity_decisions.id"],
            ondelete="SET NULL",
            name="fk_game_source_observation_links_decision",
        ),
        ForeignKeyConstraint(
            ["user_id", "decision_id"],
            ["game_identity_decisions.user_id", "game_identity_decisions.id"],
            deferrable=True,
            initially="DEFERRED",
            name="fk_game_source_observation_links_decision_owner",
        ),
        CheckConstraint(
            "unlinked_at IS NULL OR unlinked_at >= linked_at",
            name="ck_game_source_observation_links_period",
        ),
        Index(
            "uq_game_source_observation_links_open",
            "observation_id",
            unique=True,
            postgresql_where=text("unlinked_at IS NULL"),
        ),
        Index("ix_game_source_observation_links_observation_id", "observation_id"),
        Index("ix_game_source_observation_links_game_id", "game_id"),
        Index(
            "ix_game_source_observation_links_decision_id",
            "decision_id",
            postgresql_where=text("decision_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    observation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    game_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RecordOrigin.INGEST.value, server_default="ingest"
    )
    decision_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    linked_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    unlinked_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
