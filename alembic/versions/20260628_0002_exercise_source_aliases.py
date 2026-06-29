from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260628_0002"
down_revision = "20260628_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exercise_source_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exercise_id", sa.Integer(), sa.ForeignKey("exercises.id"), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_exercise_id", sa.Integer(), nullable=False),
        sa.Column("source_name", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("source_system", "source_exercise_id", name="uq_exercise_source_aliases_source_key"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_exercise_source_aliases_exercise_id", "exercise_source_aliases", ["exercise_id"])

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT id, tc_exercise_id, name
            FROM exercises
            WHERE tc_exercise_id IS NOT NULL
            """
        )
    )
    alias_table = sa.table(
        "exercise_source_aliases",
        sa.column("uuid", postgresql.UUID(as_uuid=True)),
        sa.column("exercise_id", sa.Integer()),
        sa.column("source_system", sa.String(length=64)),
        sa.column("source_exercise_id", sa.Integer()),
        sa.column("source_name", sa.Text()),
    )
    op.bulk_insert(
        alias_table,
        [
            {
                "uuid": uuid.uuid4(),
                "exercise_id": row.id,
                "source_system": "truecoach",
                "source_exercise_id": row.tc_exercise_id,
                "source_name": row.name,
            }
            for row in rows
        ],
    )

    op.drop_column("exercises", "tc_exercise_id")


def downgrade() -> None:
    op.add_column("exercises", sa.Column("tc_exercise_id", sa.Integer(), nullable=True))
    op.execute(
        """
        WITH ranked_aliases AS (
            SELECT
                exercise_id,
                source_exercise_id,
                ROW_NUMBER() OVER (
                    PARTITION BY exercise_id
                    ORDER BY source_exercise_id
                ) AS rank_order
            FROM exercise_source_aliases
            WHERE source_system = 'truecoach'
              AND deleted_at IS NULL
        )
        UPDATE exercises
        SET tc_exercise_id = ranked_aliases.source_exercise_id
        FROM ranked_aliases
        WHERE exercises.id = ranked_aliases.exercise_id
          AND ranked_aliases.rank_order = 1
        """
    )
    op.create_unique_constraint("uq_exercises_tc_exercise_id", "exercises", ["tc_exercise_id"])
    op.drop_index("ix_exercise_source_aliases_exercise_id", table_name="exercise_source_aliases")
    op.drop_table("exercise_source_aliases")
