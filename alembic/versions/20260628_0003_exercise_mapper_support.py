from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260628_0003"
down_revision = "20260628_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exercise_abbreviations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("abbreviation", sa.Text(), nullable=False),
        sa.Column("expansion", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("source IN ('truecoach', 'ai', 'user', 'system')", name="ck_exercise_abbreviations_source"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index(
        "ix_exercise_abbreviations_abbreviation_current",
        "exercise_abbreviations",
        [sa.text("lower(btrim(abbreviation))")],
        unique=True,
        postgresql_where=sa.text("is_active = true AND deleted_at IS NULL"),
    )

    op.create_table(
        "exercise_name_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exercise_id", sa.Integer(), sa.ForeignKey("exercises.id"), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("review_status", sa.String(length=32), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("model_name", sa.String(length=255)),
        sa.Column("model_version", sa.String(length=255)),
        sa.Column("rationale", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(length=255)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("superseded_by_id", sa.Integer(), sa.ForeignKey("exercise_name_aliases.id")),
        sa.CheckConstraint("source IN ('truecoach', 'ai', 'user', 'system')", name="ck_exercise_name_aliases_source"),
        sa.CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected', 'superseded')",
            name="ck_exercise_name_aliases_review_status",
        ),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_exercise_name_aliases_exercise_id", "exercise_name_aliases", ["exercise_id"])
    op.create_index(
        "ix_exercise_name_aliases_alias_current",
        "exercise_name_aliases",
        [sa.text("lower(btrim(alias))")],
        unique=True,
        postgresql_where=sa.text(
            "is_current = true AND review_status IN ('pending', 'approved') AND deleted_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_exercise_name_aliases_alias_current", table_name="exercise_name_aliases")
    op.drop_index("ix_exercise_name_aliases_exercise_id", table_name="exercise_name_aliases")
    op.drop_table("exercise_name_aliases")
    op.drop_index("ix_exercise_abbreviations_abbreviation_current", table_name="exercise_abbreviations")
    op.drop_table("exercise_abbreviations")
