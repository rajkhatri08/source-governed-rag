"""baseline: the schema as it was before Alembic, audit_log and documents (including retired)

Revision ID: 0001
Revises:
Create Date: 2026-11-20 19:03:56.642528

A database created before Alembic already has these tables. It joins with `alembic stamp 0001`,
after `print_schema.py` has shown its schema matches migrations/baseline_schema.txt. A new
database gets the tables from `alembic upgrade head`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create audit_log and documents exactly as db.py defined them before P0.2."""
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.DateTime()),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("tier", sa.String(10), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("top_distance", sa.Float()),
        sa.Column("sources", sa.Text()),
        sa.Column("warnings", sa.Text()),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(200), nullable=False, unique=True),
        sa.Column("owner", sa.String(100)),
        sa.Column("version", sa.String(50)),
        sa.Column("approved", sa.Boolean()),
        sa.Column("retired", sa.Boolean()),
        sa.Column("review_date", sa.String(20)),
    )


def downgrade() -> None:
    """Refused: undoing the baseline would drop every table and all the data in them."""
    raise NotImplementedError("the baseline is never downgraded: it would drop every table")
