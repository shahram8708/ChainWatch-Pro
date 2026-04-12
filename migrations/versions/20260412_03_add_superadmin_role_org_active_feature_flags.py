"""Add superadmin role to users table and create feature_flags.

Revision ID: 20260412_03
Revises: 20260412_02a
Create Date: 2026-04-12 00:20:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260412_03"
down_revision = "20260412_02a"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _table_exists(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    inspector = sa.inspect(op.get_bind())
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _index_exists(table_name: str, index_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    inspector = sa.inspect(op.get_bind())
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _drop_table_if_exists(table_name: str) -> None:
    if _table_exists(table_name):
        op.drop_table(table_name)


def _sqlite_role_check_contains_superadmin() -> bool:
    inspector = sa.inspect(op.get_bind())
    role_checks = [
        (check.get("sqltext") or "").lower()
        for check in inspector.get_check_constraints("users")
        if "role" in (check.get("sqltext") or "").lower()
    ]
    if not role_checks:
        return False
    return any("superadmin" in check for check in role_checks)


def upgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "sqlite":
        # Clean up leftovers from interrupted previous batch migrations.
        _drop_table_if_exists("_alembic_tmp_users")
        _drop_table_if_exists("_alembic_tmp_organisations")

    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE user_role_enum ADD VALUE IF NOT EXISTS 'superadmin'")
    elif not _sqlite_role_check_contains_superadmin():
        with op.batch_alter_table("users") as batch_op:
            batch_op.alter_column(
                "role",
                existing_type=sa.Enum("admin", "manager", "viewer", name="user_role_enum"),
                type_=sa.Enum("superadmin", "admin", "manager", "viewer", name="user_role_enum"),
                existing_nullable=False,
                existing_server_default="manager",
            )

    if not _column_exists("organisations", "is_active"):
        with op.batch_alter_table("organisations") as batch_op:
            batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))

    if not _table_exists("feature_flags"):
        op.create_table(
            "feature_flags",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("flag_name", sa.String(length=100), nullable=False),
            sa.Column("is_enabled_globally", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("enabled_for_plans", _json_type(), nullable=False),
            sa.Column("enabled_for_org_ids", _json_type(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("flag_name", name="uq_feature_flags_flag_name"),
        )

    if _table_exists("feature_flags") and not _index_exists("feature_flags", "ix_feature_flags_flag_name"):
        op.create_index("ix_feature_flags_flag_name", "feature_flags", ["flag_name"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()

    if _table_exists("feature_flags") and _index_exists("feature_flags", "ix_feature_flags_flag_name"):
        op.drop_index("ix_feature_flags_flag_name", table_name="feature_flags")
    if _table_exists("feature_flags"):
        op.drop_table("feature_flags")

    if _column_exists("organisations", "is_active"):
        with op.batch_alter_table("organisations") as batch_op:
            batch_op.drop_column("is_active")

    if bind.dialect.name == "postgresql":
        op.execute("UPDATE users SET role = 'admin' WHERE role = 'superadmin'")
        op.execute("ALTER TYPE user_role_enum RENAME TO user_role_enum_old")
        op.execute("CREATE TYPE user_role_enum AS ENUM ('admin', 'manager', 'viewer')")
        op.execute(
            "ALTER TABLE users ALTER COLUMN role TYPE user_role_enum USING role::text::user_role_enum"
        )
        op.execute("DROP TYPE user_role_enum_old")
    elif _sqlite_role_check_contains_superadmin():
        with op.batch_alter_table("users") as batch_op:
            batch_op.alter_column(
                "role",
                existing_type=sa.Enum("superadmin", "admin", "manager", "viewer", name="user_role_enum"),
                type_=sa.Enum("admin", "manager", "viewer", name="user_role_enum"),
                existing_nullable=False,
                existing_server_default="manager",
            )
