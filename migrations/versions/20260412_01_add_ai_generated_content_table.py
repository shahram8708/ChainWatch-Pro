"""Add ai_generated_content table for Gemini response caching.

Revision ID: 20260412_01
Revises:
Create Date: 2026-04-12 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260412_01"
down_revision = None
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _index_exists(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if _table_exists("ai_generated_content"):
        return

    structured_data_type = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

    op.create_table(
        "ai_generated_content",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organisation_id", sa.String(length=36), nullable=False),
        sa.Column(
            "content_type",
            sa.Enum(
                "carrier_commentary",
                "shipment_disruption_summary",
                "simulation_narrative",
                "executive_brief",
                "alert_description",
                "route_event_risk",
                "port_congestion_analysis",
                name="ai_generated_content_type_enum",
            ),
            nullable=False,
        ),
        sa.Column("content_key", sa.String(length=255), nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=False),
        sa.Column("formatted_response", sa.Text(), nullable=True),
        sa.Column("structured_data", structured_data_type, nullable=True),
        sa.Column(
            "response_format",
            sa.Enum("markdown", "json", "plain_text", name="ai_response_format_enum"),
            nullable=False,
            server_default="markdown",
        ),
        sa.Column("prompt_used", sa.Text(), nullable=True),
        sa.Column("model_used", sa.String(length=50), nullable=False, server_default="gemini-2.5-flash"),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("generation_duration_ms", sa.Integer(), nullable=True),
        sa.Column("is_stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("regeneration_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_regenerated_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["last_regenerated_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organisation_id",
            "content_type",
            "content_key",
            name="uq_ai_generated_content_org_type_key",
        ),
    )

    op.create_index(
        "ix_ai_generated_content_org_type_key",
        "ai_generated_content",
        ["organisation_id", "content_type", "content_key"],
    )
    op.create_index("ix_ai_generated_content_organisation_id", "ai_generated_content", ["organisation_id"])
    op.create_index("ix_ai_generated_content_content_type", "ai_generated_content", ["content_type"])
    op.create_index("ix_ai_generated_content_content_key", "ai_generated_content", ["content_key"])
    op.create_index(
        "ix_ai_generated_content_type_updated_at",
        "ai_generated_content",
        ["content_type", "updated_at"],
    )


def downgrade() -> None:
    if not _table_exists("ai_generated_content"):
        return

    if _index_exists("ai_generated_content", "ix_ai_generated_content_type_updated_at"):
        op.drop_index("ix_ai_generated_content_type_updated_at", table_name="ai_generated_content")
    if _index_exists("ai_generated_content", "ix_ai_generated_content_content_key"):
        op.drop_index("ix_ai_generated_content_content_key", table_name="ai_generated_content")
    if _index_exists("ai_generated_content", "ix_ai_generated_content_content_type"):
        op.drop_index("ix_ai_generated_content_content_type", table_name="ai_generated_content")
    if _index_exists("ai_generated_content", "ix_ai_generated_content_organisation_id"):
        op.drop_index("ix_ai_generated_content_organisation_id", table_name="ai_generated_content")
    if _index_exists("ai_generated_content", "ix_ai_generated_content_org_type_key"):
        op.drop_index("ix_ai_generated_content_org_type_key", table_name="ai_generated_content")

    op.drop_table("ai_generated_content")

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        sa.Enum(name="ai_response_format_enum").drop(bind, checkfirst=True)
        sa.Enum(name="ai_generated_content_type_enum").drop(bind, checkfirst=True)
