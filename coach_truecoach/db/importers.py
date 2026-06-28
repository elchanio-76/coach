from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .models import Exercise, Workout, WorkoutCategory, WorkoutItem, WorkoutItemAttachment, WorkoutItemExercise


DEFAULT_PARSED_DIR = Path("data/cache/truecoach/parsed")
DEFAULT_CATEGORIES_FILE = Path("workout_categories.json")


@dataclass(frozen=True)
class ImportSummary:
    workouts: int = 0
    workout_items: int = 0
    attachments: int = 0
    categories: int = 0
    exercises: int = 0
    workout_item_exercises: int = 0


def seed_workout_categories(
    session: Session,
    categories_file: Path = DEFAULT_CATEGORIES_FILE,
) -> int:
    payload = json.loads(categories_file.read_text(encoding="utf-8"))
    categories = payload.get("workout_categories")
    if not isinstance(categories, list):
        raise RuntimeError(f"Expected workout_categories list in {categories_file}")

    inserted = 0
    for category in categories:
        name = str(category.get("name", "")).strip()
        if not name:
            continue
        existing = session.execute(
            select(WorkoutCategory).where(WorkoutCategory.name.ilike(name))
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                WorkoutCategory(
                    name=name,
                    description=_optional_text(category.get("description")),
                    color_code=_optional_text(category.get("color_code")),
                )
            )
        else:
            existing.description = _optional_text(category.get("description"))
            existing.color_code = _optional_text(category.get("color_code"))
        inserted += 1
    return inserted


def import_parsed_data(
    session: Session,
    parsed_dir: Path = DEFAULT_PARSED_DIR,
) -> ImportSummary:
    workouts_path = parsed_dir / "workouts.jsonl"
    items_path = parsed_dir / "workout_items.jsonl"
    attachments_path = parsed_dir / "attachments.jsonl"

    workout_records = _read_jsonl(workouts_path)
    item_records = _read_jsonl(items_path)
    attachment_records = _read_jsonl(attachments_path)

    workouts = _upsert_workouts(session, workout_records)
    workout_lookup = _load_lookup(session, select(Workout.tc_workout_id, Workout.id))
    exercises, exercise_lookup = _upsert_exercises_from_truecoach(session, item_records)
    workout_items = _upsert_workout_items(session, item_records, workout_lookup, exercise_lookup)
    item_lookup = _load_lookup(session, select(WorkoutItem.tc_workout_item_id, WorkoutItem.id))
    attachments = _upsert_attachments(session, attachment_records, item_lookup)
    workout_item_exercises = _upsert_truecoach_item_exercises(session, item_records, item_lookup, exercise_lookup)

    return ImportSummary(
        workouts=workouts,
        workout_items=workout_items,
        attachments=attachments,
        exercises=exercises,
        workout_item_exercises=workout_item_exercises,
    )


def _upsert_workouts(session: Session, records: list[dict[str, Any]]) -> int:
    count = 0
    for record in records:
        values = {
            "tc_workout_id": int(record["source_id"]),
            "tc_uuid": _optional_text(record.get("uuid")),
            "tc_client_id": _optional_int(record.get("client_id")),
            "tc_source_file": _optional_text(record.get("source_file")),
            "tc_source_page": _optional_int(record.get("source_page")),
            "due_date": _optional_date(record.get("due")),
            "state": _optional_text(record.get("state")),
            "rest_day": bool(record.get("rest_day")),
            "title": _optional_text(record.get("title")),
            "program_id": _optional_int(record.get("program_id")),
            "program_name": _optional_text(record.get("program_name")),
            "short_description_html": _optional_text(record.get("short_description")),
            "tc_workout_item_ids": record.get("workout_item_ids") or [],
            "tc_comment_ids": record.get("comment_ids") or [],
            "tc_created_at": _optional_datetime(record.get("created_at")),
            "tc_updated_at": _optional_datetime(record.get("updated_at")),
        }
        session.execute(
            insert(Workout)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[Workout.tc_workout_id],
                set_={**values, "updated_at": datetime.now().astimezone()},
            )
        )
        count += 1
    return count


def _upsert_exercises_from_truecoach(session: Session, records: list[dict[str, Any]]) -> tuple[int, dict[int, int]]:
    count = 0
    lookup: dict[int, int] = {}
    seen: set[int] = set()
    for record in records:
        tc_exercise_id = _optional_int(record.get("exercise_id"))
        if tc_exercise_id is None or tc_exercise_id in seen:
            continue
        seen.add(tc_exercise_id)
        name = _preferred_exercise_name(record)
        normalized_name = name.strip().casefold()
        with session.no_autoflush:
            exercise = session.execute(
                select(Exercise).where(Exercise.tc_exercise_id == tc_exercise_id)
            ).scalar_one_or_none()
            if exercise is None:
                exercise = session.execute(
                    select(Exercise).where(Exercise.name.ilike(name))
                ).scalar_one_or_none()
        if exercise is None:
            exercise = Exercise(
                name=name,
                tc_exercise_id=tc_exercise_id,
                created_by_source="truecoach",
                review_status="approved",
            )
            session.add(exercise)
            session.flush()
        else:
            exercise.name = name
            exercise.review_status = "approved"
            if exercise.tc_exercise_id is None and exercise.name.strip().casefold() == normalized_name:
                exercise.tc_exercise_id = tc_exercise_id
            session.flush()
        lookup[tc_exercise_id] = exercise.id
        count += 1
    return count, lookup


def _upsert_workout_items(
    session: Session,
    records: list[dict[str, Any]],
    workout_lookup: dict[int, int],
    exercise_lookup: dict[int, int],
) -> int:
    count = 0
    for record in records:
        tc_workout_id = _optional_int(record.get("workout_source_id"))
        if tc_workout_id is None or tc_workout_id not in workout_lookup:
            raise RuntimeError(f"Missing workout for workout item {record.get('source_id')}")
        tc_exercise_id = _optional_int(record.get("exercise_id"))
        values = {
            "workout_id": workout_lookup[tc_workout_id],
            "tc_workout_item_id": int(record["source_id"]),
            "tc_workout_id": tc_workout_id,
            "tc_exercise_id": tc_exercise_id,
            "tc_source_file": _optional_text(record.get("source_file")),
            "tc_source_page": _optional_int(record.get("source_page")),
            "position": _optional_int(record.get("position")),
            "name_raw": str(record.get("name", "")),
            "name_display": str(record.get("name_display", "")),
            "info_raw": str(record.get("info", "")),
            "info_display": str(record.get("info_display", "")),
            "result_raw": str(record.get("result", "")),
            "result_display": str(record.get("result_display", "")),
            "state": _optional_text(record.get("state")),
            "is_circuit": bool(record.get("is_circuit")),
            "selected_exercises": record.get("selected_exercises") or [],
            "linked": bool(record.get("linked")),
            "assessment_id": _optional_int(record.get("assessment_id")),
            "request_video": bool(record.get("request_video")),
            "attachment_count": int(record.get("attachment_count") or 0),
            "exercise_id": exercise_lookup.get(tc_exercise_id) if tc_exercise_id is not None else None,
            "tc_created_at": _optional_datetime(record.get("created_at")),
        }
        session.execute(
            insert(WorkoutItem)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[WorkoutItem.tc_workout_item_id],
                set_={**values, "updated_at": datetime.now().astimezone()},
            )
        )
        count += 1
    return count


def _upsert_attachments(session: Session, records: list[dict[str, Any]], item_lookup: dict[int, int]) -> int:
    count = 0
    for record in records:
        tc_workout_item_id = _optional_int(record.get("workout_item_source_id"))
        if tc_workout_item_id is None or tc_workout_item_id not in item_lookup:
            raise RuntimeError(f"Missing workout item for attachment {record.get('url')}")
        values = {
            "workout_item_id": item_lookup[tc_workout_item_id],
            "tc_workout_item_id": tc_workout_item_id,
            "name": _optional_text(record.get("name")),
            "url": _optional_text(record.get("url")),
            "mime_type": _optional_text(record.get("mime_type")),
            "size_bytes": _optional_int(record.get("size")),
            "tc_source_file": _optional_text(record.get("source_file")),
            "tc_source_page": _optional_int(record.get("source_page")),
        }
        session.execute(
            insert(WorkoutItemAttachment)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_workout_item_attachments_item_url",
                set_={**values, "updated_at": datetime.now().astimezone()},
            )
        )
        count += 1
    return count


def _upsert_truecoach_item_exercises(
    session: Session,
    records: list[dict[str, Any]],
    item_lookup: dict[int, int],
    exercise_lookup: dict[int, int],
) -> int:
    count = 0
    for record in records:
        tc_exercise_id = _optional_int(record.get("exercise_id"))
        tc_workout_item_id = _optional_int(record.get("source_id"))
        if tc_exercise_id is None or tc_workout_item_id is None:
            continue
        workout_item_id = item_lookup.get(tc_workout_item_id)
        exercise_id = exercise_lookup.get(tc_exercise_id)
        if workout_item_id is None or exercise_id is None:
            continue
        existing = session.execute(
            select(WorkoutItemExercise).where(
                WorkoutItemExercise.workout_item_id == workout_item_id,
                WorkoutItemExercise.exercise_id == exercise_id,
                WorkoutItemExercise.source == "truecoach",
                WorkoutItemExercise.review_status == "approved",
                WorkoutItemExercise.is_current.is_(True),
            )
        )
        if existing.scalar_one_or_none() is None:
            session.add(
                WorkoutItemExercise(
                    workout_item_id=workout_item_id,
                    exercise_id=exercise_id,
                    position=1,
                    role="primary",
                    source="truecoach",
                    review_status="approved",
                    is_current=True,
                )
            )
        count += 1
    return count


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"Missing parsed input file: {path}")
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _load_lookup(session: Session, stmt: Select[tuple[int, int]]) -> dict[int, int]:
    return {key: value for key, value in session.execute(stmt)}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text != "" else None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_date(value: Any) -> date | None:
    text = _optional_text(value)
    return date.fromisoformat(text) if text else None


def _optional_datetime(value: Any) -> datetime | None:
    text = _optional_text(value)
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _preferred_exercise_name(record: dict[str, Any]) -> str:
    for key in ("name_display", "name"):
        value = _optional_text(record.get(key))
        if value:
            return value.strip()
    return f"TrueCoach Exercise {record['exercise_id']}"
