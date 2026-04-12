"""Add must_change_password and invitation tracking columns to users.

Revision ID: 20260412_02
Revises: 20260412_01
Create Date: 2026-04-12 00:10:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260412_02"
down_revision = "20260412_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("invited_by_user_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("invitation_sent_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("invitation_accepted_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("temporary_password_hash", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("account_source", sa.String(length=50), nullable=False, server_default="manual_invite"))
        batch_op.add_column(sa.Column("superadmin_notes", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("job_title", sa.String(length=100), nullable=True))
        batch_op.create_index("ix_users_invited_by_user_id", ["invited_by_user_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_users_invited_by_user_id_users",
            "users",
            ["invited_by_user_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("fk_users_invited_by_user_id_users", type_="foreignkey")
        batch_op.drop_index("ix_users_invited_by_user_id")
        batch_op.drop_column("job_title")
        batch_op.drop_column("superadmin_notes")
        batch_op.drop_column("account_source")
        batch_op.drop_column("temporary_password_hash")
        batch_op.drop_column("invitation_accepted_at")
        batch_op.drop_column("invitation_sent_at")
        batch_op.drop_column("invited_by_user_id")
        batch_op.drop_column("must_change_password")
