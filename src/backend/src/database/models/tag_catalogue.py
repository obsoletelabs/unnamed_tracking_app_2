"""Tag catalogue: labels and aliases layered over the raw `tags` arrays (#267).

The arrays on games and media stay the source of truth for what a record carries. A catalogue tag
gives a raw spelling a display label; an alias maps another raw spelling onto a catalogue tag. Both
are applied when tags are read and never rewrite stored tags. Private (per-user) rows and
deployment defaults live in separate tables; a user can suppress a deployment default for one
spelling.

See wiki/docs/development/design/tag-catalogue.md.
"""

from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp

TAG_LABEL_LENGTH = 40
TAG_KEY_LENGTH = 160
TAG_NAMESPACE_LENGTH = 40

# The private and default tables repeat the same column declarations on purpose.
# pylint: disable=duplicate-code


class TagKind(StrEnum):
    """`user`: created in the catalogue; `source`: discovered from tags already on records."""

    USER = "user"
    SOURCE = "source"


class UserTag(Base):
    """A private catalogue tag. `key` is the folded form of the raw spelling it was created from
    and does not change on rename (a rename changes only `label`)."""

    __tablename__ = "user_tags"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_user_tags_user_key"),
        UniqueConstraint("user_id", "id", name="uq_user_tags_user_id_id"),
        CheckConstraint("btrim(label) <> ''", name="ck_user_tags_label"),
        CheckConstraint("btrim(key) <> ''", name="ck_user_tags_key"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(TAG_LABEL_LENGTH), nullable=False)
    key: Mapped[str] = mapped_column(String(TAG_KEY_LENGTH), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default=TagKind.USER.value)
    namespace: Mapped[str | None] = mapped_column(String(TAG_NAMESPACE_LENGTH), nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    archived_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class UserTagAlias(Base):
    """A user-authored "treat this raw spelling as that catalogue tag"."""

    __tablename__ = "user_tag_aliases"
    __table_args__ = (
        UniqueConstraint("user_id", "alias_key", name="uq_user_tag_aliases_user_alias"),
        CheckConstraint("btrim(alias_key) <> ''", name="ck_user_tag_aliases_key"),
        # The composite key keeps an alias on its owner's tag.
        ForeignKeyConstraint(
            ["user_id", "tag_id"],
            ["user_tags.user_id", "user_tags.id"],
            ondelete="CASCADE",
            name="fk_user_tag_aliases_tag",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tag_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    alias_key: Mapped[str] = mapped_column(String(TAG_KEY_LENGTH), nullable=False)
    alias_label: Mapped[str] = mapped_column(String(TAG_LABEL_LENGTH), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)


class UserTagSuppression(Base):
    """The user chose to see this raw spelling as it is instead of a deployment default."""

    __tablename__ = "user_tag_suppressions"
    __table_args__ = (
        UniqueConstraint("user_id", "tag_key", name="uq_user_tag_suppressions_user_key"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tag_key: Mapped[str] = mapped_column(String(TAG_KEY_LENGTH), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)


class DefaultTag(Base):
    """A deployment-wide catalogue tag, managed by administrators."""

    __tablename__ = "default_tags"
    __table_args__ = (
        UniqueConstraint("key", name="uq_default_tags_key"),
        CheckConstraint("btrim(label) <> ''", name="ck_default_tags_label"),
        CheckConstraint("btrim(key) <> ''", name="ck_default_tags_key"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    label: Mapped[str] = mapped_column(String(TAG_LABEL_LENGTH), nullable=False)
    key: Mapped[str] = mapped_column(String(TAG_KEY_LENGTH), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    archived_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class DefaultTagAlias(Base):
    """A deployment-wide alias onto a default tag."""

    __tablename__ = "default_tag_aliases"
    __table_args__ = (
        UniqueConstraint("alias_key", name="uq_default_tag_aliases_alias"),
        CheckConstraint("btrim(alias_key) <> ''", name="ck_default_tag_aliases_key"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tag_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("default_tags.id", ondelete="CASCADE"), nullable=False
    )
    alias_key: Mapped[str] = mapped_column(String(TAG_KEY_LENGTH), nullable=False)
    alias_label: Mapped[str] = mapped_column(String(TAG_LABEL_LENGTH), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
