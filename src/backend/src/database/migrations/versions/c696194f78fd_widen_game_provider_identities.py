"""widen game provider identities

Revision ID: c696194f78fd
Revises: 0037e34ac954
Create Date: 2026-10-11 01:20:51.610645

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c696194f78fd"
down_revision: Union[str, None] = "0037e34ac954"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Generated with Compose/Alembic; unrelated historical note tables preserved.
    op.alter_column(
        "games",
        "external_id",
        existing_type=sa.String(length=64),
        type_=sa.String(length=256),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Provider identities must never be truncated or silently reassigned.
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM games WHERE length(external_id) > 64)")
    ):
        raise RuntimeError("Cannot narrow game identities while values exceed 64 characters")
    op.alter_column(
        "games",
        "external_id",
        existing_type=sa.String(length=256),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
