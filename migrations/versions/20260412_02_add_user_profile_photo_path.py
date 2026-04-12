"""Add profile_photo_path to users table.

Revision ID: 20260412_02a
Revises: 20260412_02
Create Date: 2026-04-12 00:20:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260412_02a"
down_revision = "20260412_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("profile_photo_path", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "profile_photo_path")
