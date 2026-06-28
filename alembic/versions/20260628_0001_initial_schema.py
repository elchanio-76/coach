from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260628_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tc_workout_id", sa.Integer(), nullable=False),
        sa.Column("tc_uuid", sa.String(length=255)),
        sa.Column("tc_client_id", sa.Integer()),
        sa.Column("tc_source_file", sa.Text()),
        sa.Column("tc_source_page", sa.Integer()),
        sa.Column("due_date", sa.Date()),
        sa.Column("state", sa.String(length=32)),
        sa.Column("rest_day", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("title", sa.Text()),
        sa.Column("program_id", sa.Integer()),
        sa.Column("program_name", sa.Text()),
        sa.Column("short_description_html", sa.Text()),
        sa.Column("tc_workout_item_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tc_comment_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tc_created_at", sa.DateTime(timezone=True)),
        sa.Column("tc_updated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("state IN ('completed', 'missed', 'scheduled', 'skipped')", name="ck_workouts_state"),
        sa.UniqueConstraint("tc_workout_id"),
        sa.UniqueConstraint("tc_uuid", name="uq_workouts_tc_uuid"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_workouts_due_date", "workouts", ["due_date"])
    op.create_index("ix_workouts_state", "workouts", ["state"])

    op.create_table(
        "exercises",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("tc_exercise_id", sa.Integer()),
        sa.Column("created_by_source", sa.String(length=32), nullable=False),
        sa.Column("review_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("created_by_source IN ('truecoach', 'ai', 'user', 'system')", name="ck_exercises_created_by_source"),
        sa.CheckConstraint("review_status IN ('pending', 'approved', 'rejected', 'superseded')", name="ck_exercises_review_status"),
        sa.UniqueConstraint("tc_exercise_id", name="uq_exercises_tc_exercise_id"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_exercises_review_status", "exercises", ["review_status"])
    op.create_index("ix_exercises_name_normalized", "exercises", [sa.text("lower(btrim(name))")], unique=True, postgresql_where=sa.text("deleted_at IS NULL"))

    op.create_table(
        "workout_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("color_code", sa.String(length=32)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_workout_categories_name_normalized", "workout_categories", [sa.text("lower(btrim(name))")], unique=True, postgresql_where=sa.text("deleted_at IS NULL"))

    op.create_table(
        "workout_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workout_id", sa.Integer(), sa.ForeignKey("workouts.id"), nullable=False),
        sa.Column("tc_workout_item_id", sa.Integer(), nullable=False),
        sa.Column("tc_workout_id", sa.Integer()),
        sa.Column("tc_exercise_id", sa.Integer()),
        sa.Column("tc_source_file", sa.Text()),
        sa.Column("tc_source_page", sa.Integer()),
        sa.Column("position", sa.Integer()),
        sa.Column("name_raw", sa.Text(), nullable=False),
        sa.Column("name_display", sa.Text(), nullable=False),
        sa.Column("info_raw", sa.Text(), nullable=False),
        sa.Column("info_display", sa.Text(), nullable=False),
        sa.Column("result_raw", sa.Text(), nullable=False),
        sa.Column("result_display", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32)),
        sa.Column("is_circuit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("selected_exercises", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("linked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("assessment_id", sa.Integer()),
        sa.Column("request_video", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("attachment_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("exercise_id", sa.Integer(), sa.ForeignKey("exercises.id")),
        sa.Column("tc_created_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("state IN ('completed', 'missed', 'scheduled', 'skipped')", name="ck_workout_items_state"),
        sa.UniqueConstraint("tc_workout_item_id"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_workout_items_workout_id_position", "workout_items", ["workout_id", "position"])
    op.create_index("ix_workout_items_tc_exercise_id", "workout_items", ["tc_exercise_id"])
    op.create_index("ix_workout_items_exercise_id", "workout_items", ["exercise_id"])
    op.create_index("ix_workout_items_state", "workout_items", ["state"])

    op.create_table(
        "workout_item_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workout_item_id", sa.Integer(), sa.ForeignKey("workout_items.id"), nullable=False),
        sa.Column("tc_workout_item_id", sa.Integer()),
        sa.Column("name", sa.Text()),
        sa.Column("url", sa.Text()),
        sa.Column("mime_type", sa.String(length=255)),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("tc_source_file", sa.Text()),
        sa.Column("tc_source_page", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("uuid"),
        sa.UniqueConstraint("workout_item_id", "url", name="uq_workout_item_attachments_item_url"),
    )
    op.create_index("ix_workout_item_attachments_workout_item_id", "workout_item_attachments", ["workout_item_id"])

    op.create_table(
        "workout_item_exercises",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workout_item_id", sa.Integer(), sa.ForeignKey("workout_items.id"), nullable=False),
        sa.Column("exercise_id", sa.Integer(), sa.ForeignKey("exercises.id"), nullable=False),
        sa.Column("position", sa.Integer()),
        sa.Column("role", sa.String(length=64)),
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
        sa.Column("superseded_by_id", sa.Integer(), sa.ForeignKey("workout_item_exercises.id")),
        sa.CheckConstraint("source IN ('truecoach', 'ai', 'user', 'system')", name="ck_workout_item_exercises_source"),
        sa.CheckConstraint("review_status IN ('pending', 'approved', 'rejected', 'superseded')", name="ck_workout_item_exercises_review_status"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_workout_item_exercises_workout_item_id", "workout_item_exercises", ["workout_item_id"])
    op.create_index("ix_workout_item_exercises_exercise_id", "workout_item_exercises", ["exercise_id"])
    op.create_index("ix_workout_item_exercises_current_approved", "workout_item_exercises", ["workout_item_id", "exercise_id"], unique=True, postgresql_where=sa.text("is_current = true AND review_status = 'approved'"))

    op.create_table(
        "workout_item_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workout_item_id", sa.Integer(), sa.ForeignKey("workout_items.id"), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("workout_categories.id"), nullable=False),
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
        sa.Column("superseded_by_id", sa.Integer(), sa.ForeignKey("workout_item_categories.id")),
        sa.CheckConstraint("source IN ('truecoach', 'ai', 'user', 'system')", name="ck_workout_item_categories_source"),
        sa.CheckConstraint("review_status IN ('pending', 'approved', 'rejected', 'superseded')", name="ck_workout_item_categories_review_status"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_workout_item_categories_workout_item_id", "workout_item_categories", ["workout_item_id"])
    op.create_index("ix_workout_item_categories_category_id", "workout_item_categories", ["category_id"])
    op.create_index("ix_workout_item_categories_current_approved", "workout_item_categories", ["workout_item_id", "category_id"], unique=True, postgresql_where=sa.text("is_current = true AND review_status = 'approved'"))

    op.create_table(
        "workout_item_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workout_item_id", sa.Integer(), sa.ForeignKey("workout_items.id"), nullable=False),
        sa.Column("metric_type", sa.String(length=128), nullable=False),
        sa.Column("value_numeric", sa.Numeric(14, 4)),
        sa.Column("value_text", sa.Text()),
        sa.Column("unit", sa.String(length=64)),
        sa.Column("source_text", sa.Text()),
        sa.Column("occurred_on", sa.Date()),
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
        sa.Column("superseded_by_id", sa.Integer(), sa.ForeignKey("workout_item_metrics.id")),
        sa.CheckConstraint("source IN ('truecoach', 'ai', 'user', 'system')", name="ck_workout_item_metrics_source"),
        sa.CheckConstraint("review_status IN ('pending', 'approved', 'rejected', 'superseded')", name="ck_workout_item_metrics_review_status"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_workout_item_metrics_workout_item_id", "workout_item_metrics", ["workout_item_id"])
    op.create_index("ix_workout_item_metrics_metric_type", "workout_item_metrics", ["metric_type"])
    op.create_index("ix_workout_item_metrics_occurred_on", "workout_item_metrics", ["occurred_on"])
    op.create_index("ix_workout_item_metrics_current_approved", "workout_item_metrics", ["workout_item_id", "metric_type"], postgresql_where=sa.text("is_current = true AND review_status = 'approved'"))


def downgrade() -> None:
    op.drop_index("ix_workout_item_metrics_current_approved", table_name="workout_item_metrics")
    op.drop_index("ix_workout_item_metrics_occurred_on", table_name="workout_item_metrics")
    op.drop_index("ix_workout_item_metrics_metric_type", table_name="workout_item_metrics")
    op.drop_index("ix_workout_item_metrics_workout_item_id", table_name="workout_item_metrics")
    op.drop_table("workout_item_metrics")
    op.drop_index("ix_workout_item_categories_current_approved", table_name="workout_item_categories")
    op.drop_index("ix_workout_item_categories_category_id", table_name="workout_item_categories")
    op.drop_index("ix_workout_item_categories_workout_item_id", table_name="workout_item_categories")
    op.drop_table("workout_item_categories")
    op.drop_index("ix_workout_item_exercises_current_approved", table_name="workout_item_exercises")
    op.drop_index("ix_workout_item_exercises_exercise_id", table_name="workout_item_exercises")
    op.drop_index("ix_workout_item_exercises_workout_item_id", table_name="workout_item_exercises")
    op.drop_table("workout_item_exercises")
    op.drop_index("ix_workout_item_attachments_workout_item_id", table_name="workout_item_attachments")
    op.drop_table("workout_item_attachments")
    op.drop_index("ix_workout_items_state", table_name="workout_items")
    op.drop_index("ix_workout_items_exercise_id", table_name="workout_items")
    op.drop_index("ix_workout_items_tc_exercise_id", table_name="workout_items")
    op.drop_index("ix_workout_items_workout_id_position", table_name="workout_items")
    op.drop_table("workout_items")
    op.drop_index("ix_workout_categories_name_normalized", table_name="workout_categories")
    op.drop_table("workout_categories")
    op.drop_index("ix_exercises_name_normalized", table_name="exercises")
    op.drop_index("ix_exercises_review_status", table_name="exercises")
    op.drop_table("exercises")
    op.drop_index("ix_workouts_state", table_name="workouts")
    op.drop_index("ix_workouts_due_date", table_name="workouts")
    op.drop_table("workouts")
