"""document content: each document keeps its extracted text, so a restart no longer needs the file

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-20 23:09:50.121394

Additive: three nullable columns, no default, no change to existing rows. The code deployed
before P0.3 doesn't know these columns and keeps working. Existing rows get their content from
backfill_content.py; their created_at stays null, because when they were created isn't known.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add content, content_sha256 and created_at to documents."""
    # The extracted, cleaned text: what is chunked and indexed at startup and on upload
    op.add_column("documents", sa.Column("content", sa.Text()))
    op.add_column("documents", sa.Column("content_sha256", sa.String(64)))
    # When the upload was stored, as an exact instant (timestamptz) in UTC
    op.add_column("documents", sa.Column("created_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    """Refused: dropping these columns would delete every document's stored text."""
    raise NotImplementedError("additive migration: to roll back, redeploy the previous code, which ignores "
                              "these columns; dropping them would delete every document's stored text")
