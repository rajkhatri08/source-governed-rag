"""audit answer and chunks: each audit row records the answer, the chunks it used and a UTC time

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20 19:03:57.112102

Additive: three nullable columns, no default, no change to existing rows. The code deployed
before P0.2 doesn't know these columns and keeps working, so the database can be migrated
before the new code is deployed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add answer, chunks and created_at to audit_log."""
    op.add_column("audit_log", sa.Column("answer", sa.Text()))
    # JSON list of {chunk_id, source, content_hash, distance}, best first
    op.add_column("audit_log", sa.Column("chunks", sa.Text()))
    # An exact instant (timestamptz), written in UTC. The old timestamp column has no zone.
    op.add_column("audit_log", sa.Column("created_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    """Refused: it would delete recorded answers from an append-only log, and no rollback needs it."""
    raise NotImplementedError("additive migration: to roll back, redeploy the previous code, which ignores "
                              "these columns; dropping them would delete recorded answers")
