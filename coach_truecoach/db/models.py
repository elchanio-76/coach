from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


SOURCE_VALUES = ("truecoach", "ai", "user", "system")
REVIEW_STATUS_VALUES = ("pending", "approved", "rejected", "superseded")
WORKOUT_STATE_VALUES = ("completed", "missed", "scheduled", "skipped")


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UUIDMixin:
    uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        nullable=False,
        unique=True,
    )


class ReviewMixin(TimestampMixin, UUIDMixin):
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    model_name: Mapped[str | None] = mapped_column(String(255))
    model_version: Mapped[str | None] = mapped_column(String(255))
    rationale: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))


class Workout(TimestampMixin, UUIDMixin, Base):
    __tablename__ = "workouts"
    __table_args__ = (
        CheckConstraint(f"state IN {WORKOUT_STATE_VALUES}", name="ck_workouts_state"),
        Index("ix_workouts_due_date", "due_date"),
        Index("ix_workouts_state", "state"),
        UniqueConstraint("tc_uuid", name="uq_workouts_tc_uuid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tc_workout_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    tc_uuid: Mapped[str | None] = mapped_column(String(255))
    tc_client_id: Mapped[int | None] = mapped_column(Integer)
    tc_source_file: Mapped[str | None] = mapped_column(Text)
    tc_source_page: Mapped[int | None] = mapped_column(Integer)
    due_date: Mapped[date | None] = mapped_column(Date)
    state: Mapped[str | None] = mapped_column(String(32))
    rest_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    title: Mapped[str | None] = mapped_column(Text)
    program_id: Mapped[int | None] = mapped_column(Integer)
    program_name: Mapped[str | None] = mapped_column(Text)
    short_description_html: Mapped[str | None] = mapped_column(Text)
    tc_workout_item_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    tc_comment_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    tc_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tc_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    workout_items: Mapped[list["WorkoutItem"]] = relationship(back_populates="workout")


class Exercise(TimestampMixin, UUIDMixin, Base):
    __tablename__ = "exercises"
    __table_args__ = (
        CheckConstraint(f"created_by_source IN {SOURCE_VALUES}", name="ck_exercises_created_by_source"),
        CheckConstraint(f"review_status IN {REVIEW_STATUS_VALUES}", name="ck_exercises_review_status"),
        Index("ix_exercises_review_status", "review_status"),
        Index(
            "ix_exercises_name_normalized",
            func.lower(func.btrim(text("name"))),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by_source: Mapped[str] = mapped_column(String(32), nullable=False)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")


class ExerciseSourceAlias(TimestampMixin, UUIDMixin, Base):
    __tablename__ = "exercise_source_aliases"
    __table_args__ = (
        UniqueConstraint("source_system", "source_exercise_id", name="uq_exercise_source_aliases_source_key"),
        Index("ix_exercise_source_aliases_exercise_id", "exercise_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"), nullable=False)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_exercise_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_name: Mapped[str | None] = mapped_column(Text)


class WorkoutCategory(TimestampMixin, UUIDMixin, Base):
    __tablename__ = "workout_categories"
    __table_args__ = (
        Index(
            "ix_workout_categories_name_normalized",
            func.lower(func.btrim(text("name"))),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color_code: Mapped[str | None] = mapped_column(String(32))


class WorkoutItem(TimestampMixin, UUIDMixin, Base):
    __tablename__ = "workout_items"
    __table_args__ = (
        CheckConstraint(f"state IN {WORKOUT_STATE_VALUES}", name="ck_workout_items_state"),
        Index("ix_workout_items_workout_id_position", "workout_id", "position"),
        Index("ix_workout_items_tc_exercise_id", "tc_exercise_id"),
        Index("ix_workout_items_exercise_id", "exercise_id"),
        Index("ix_workout_items_state", "state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workout_id: Mapped[int] = mapped_column(ForeignKey("workouts.id"), nullable=False)
    tc_workout_item_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    tc_workout_id: Mapped[int | None] = mapped_column(Integer)
    tc_exercise_id: Mapped[int | None] = mapped_column(Integer)
    tc_source_file: Mapped[str | None] = mapped_column(Text)
    tc_source_page: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[int | None] = mapped_column(Integer)
    name_raw: Mapped[str] = mapped_column(Text, nullable=False, default="")
    name_display: Mapped[str] = mapped_column(Text, nullable=False, default="")
    info_raw: Mapped[str] = mapped_column(Text, nullable=False, default="")
    info_display: Mapped[str] = mapped_column(Text, nullable=False, default="")
    result_raw: Mapped[str] = mapped_column(Text, nullable=False, default="")
    result_display: Mapped[str] = mapped_column(Text, nullable=False, default="")
    state: Mapped[str | None] = mapped_column(String(32))
    is_circuit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    selected_exercises: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    linked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    assessment_id: Mapped[int | None] = mapped_column(Integer)
    request_video: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    attachment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    exercise_id: Mapped[int | None] = mapped_column(ForeignKey("exercises.id"))
    tc_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    workout: Mapped["Workout"] = relationship(back_populates="workout_items")
    attachments: Mapped[list["WorkoutItemAttachment"]] = relationship(back_populates="workout_item")


class WorkoutItemAttachment(TimestampMixin, UUIDMixin, Base):
    __tablename__ = "workout_item_attachments"
    __table_args__ = (
        UniqueConstraint("workout_item_id", "url", name="uq_workout_item_attachments_item_url"),
        Index("ix_workout_item_attachments_workout_item_id", "workout_item_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workout_item_id: Mapped[int] = mapped_column(ForeignKey("workout_items.id"), nullable=False)
    tc_workout_item_id: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    tc_source_file: Mapped[str | None] = mapped_column(Text)
    tc_source_page: Mapped[int | None] = mapped_column(Integer)

    workout_item: Mapped["WorkoutItem"] = relationship(back_populates="attachments")


class WorkoutItemExercise(ReviewMixin, Base):
    __tablename__ = "workout_item_exercises"
    __table_args__ = (
        CheckConstraint(f"source IN {SOURCE_VALUES}", name="ck_workout_item_exercises_source"),
        CheckConstraint(f"review_status IN {REVIEW_STATUS_VALUES}", name="ck_workout_item_exercises_review_status"),
        Index("ix_workout_item_exercises_workout_item_id", "workout_item_id"),
        Index("ix_workout_item_exercises_exercise_id", "exercise_id"),
        Index(
            "ix_workout_item_exercises_current_approved",
            "workout_item_id",
            "exercise_id",
            unique=True,
            postgresql_where=text("is_current = true AND review_status = 'approved'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workout_item_id: Mapped[int] = mapped_column(ForeignKey("workout_items.id"), nullable=False)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"), nullable=False)
    position: Mapped[int | None] = mapped_column(Integer)
    role: Mapped[str | None] = mapped_column(String(64))
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("workout_item_exercises.id"))


class WorkoutItemCategory(ReviewMixin, Base):
    __tablename__ = "workout_item_categories"
    __table_args__ = (
        CheckConstraint(f"source IN {SOURCE_VALUES}", name="ck_workout_item_categories_source"),
        CheckConstraint(f"review_status IN {REVIEW_STATUS_VALUES}", name="ck_workout_item_categories_review_status"),
        Index("ix_workout_item_categories_workout_item_id", "workout_item_id"),
        Index("ix_workout_item_categories_category_id", "category_id"),
        Index(
            "ix_workout_item_categories_current_approved",
            "workout_item_id",
            "category_id",
            unique=True,
            postgresql_where=text("is_current = true AND review_status = 'approved'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workout_item_id: Mapped[int] = mapped_column(ForeignKey("workout_items.id"), nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("workout_categories.id"), nullable=False)
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("workout_item_categories.id"))


class WorkoutItemMetric(ReviewMixin, Base):
    __tablename__ = "workout_item_metrics"
    __table_args__ = (
        CheckConstraint(f"source IN {SOURCE_VALUES}", name="ck_workout_item_metrics_source"),
        CheckConstraint(f"review_status IN {REVIEW_STATUS_VALUES}", name="ck_workout_item_metrics_review_status"),
        Index("ix_workout_item_metrics_workout_item_id", "workout_item_id"),
        Index("ix_workout_item_metrics_metric_type", "metric_type"),
        Index("ix_workout_item_metrics_occurred_on", "occurred_on"),
        Index(
            "ix_workout_item_metrics_current_approved",
            "workout_item_id",
            "metric_type",
            unique=False,
            postgresql_where=text("is_current = true AND review_status = 'approved'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workout_item_id: Mapped[int] = mapped_column(ForeignKey("workout_items.id"), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(128), nullable=False)
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(64))
    source_text: Mapped[str | None] = mapped_column(Text)
    occurred_on: Mapped[date | None] = mapped_column(Date)
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("workout_item_metrics.id"))
