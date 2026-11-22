"""document chunks: each chunk's text and vector, so a cold start reads vectors instead of computing them

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-21 20:05:59.747947

Additive: one new table, no change to existing tables. The code deployed before P0.3b doesn't
know the table and keeps working. Existing documents get their rows from backfill_vectors.py.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create document_chunks."""
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Deleting a document (only the clean-up after a failed upload does) deletes its chunks with it
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_id", sa.String(255), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        # An array of 4-byte floats: exactly what the embedding model outputs, about 1.5 KB per chunk
        sa.Column("embedding", postgresql.ARRAY(sa.REAL()), nullable=False),
        sa.Column("embedding_model", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # A vector belongs to one chunk's text and one model: a new model adds rows rather than colliding
        sa.UniqueConstraint("chunk_id", "embedding_model", name="document_chunks_chunk_id_embedding_model_key"),
    )
    # Startup reads the vectors one document at a time
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])


def downgrade() -> None:
    """Refused: dropping the table would delete every stored vector, and no rollback needs it."""
    raise NotImplementedError("additive migration: to roll back, redeploy the previous code, which ignores "
                              "this table; dropping it would delete every stored vector")
