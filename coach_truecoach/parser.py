from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import TrueCoachPaths


KNOWN_STATES = {"completed", "missed", "scheduled", "skipped"}


@dataclass(frozen=True)
class WorkoutRecord:
    source_file: str
    source_page: int | None
    source_id: int
    client_id: int | None
    due: str | None
    state: str | None
    rest_day: bool
    title: str | None
    program_id: int | None
    program_name: str | None
    short_description: str | None
    warmup: str | None
    cooldown: str | None
    workout_item_ids: list[int]
    comment_ids: list[int]
    created_at: str | None
    updated_at: str | None
    uuid: str | None


@dataclass(frozen=True)
class WorkoutItemRecord:
    source_file: str
    source_page: int | None
    source_id: int
    workout_source_id: int
    position: int | None
    name: str
    name_display: str
    info: str
    info_display: str
    result: str
    result_display: str
    state: str | None
    is_circuit: bool
    exercise_id: int | None
    selected_exercises: list[Any]
    linked: bool
    assessment_id: int | None
    request_video: bool
    attachment_count: int
    created_at: str | None


@dataclass(frozen=True)
class AttachmentRecord:
    source_file: str
    source_page: int | None
    workout_item_source_id: int
    workout_source_id: int
    name: str | None
    url: str | None
    mime_type: str | None
    size: int | None


@dataclass(frozen=True)
class AnomalyRecord:
    source_file: str
    source_page: int | None
    kind: str
    message: str
    source_id: int | None = None


@dataclass(frozen=True)
class ParseResult:
    workouts: list[WorkoutRecord]
    workout_items: list[WorkoutItemRecord]
    attachments: list[AttachmentRecord]
    anomalies: list[AnomalyRecord]


def parse_api_files(input_files: list[Path]) -> ParseResult:
    workouts: list[WorkoutRecord] = []
    workout_items: list[WorkoutItemRecord] = []
    attachments: list[AttachmentRecord] = []
    anomalies: list[AnomalyRecord] = []
    seen_workout_ids: set[int] = set()
    seen_item_ids: set[int] = set()

    for input_file in sorted(input_files):
        payload = _read_payload(input_file)
        page = _source_page(payload)
        source_name = str(input_file)
        _validate_payload_shape(payload, source_name, page, anomalies)

        raw_workouts = payload.get("workouts") or []
        raw_items = payload.get("workout_items") or []
        workout_ids = {_to_int(workout.get("id")) for workout in raw_workouts}
        workout_ids.discard(None)
        item_ids = {_to_int(item.get("id")) for item in raw_items}
        item_ids.discard(None)
        referenced_item_ids: set[int] = set()

        for raw_workout in raw_workouts:
            workout_id = _to_int(raw_workout.get("id"))
            if workout_id is None:
                anomalies.append(AnomalyRecord(source_name, page, "missing_workout_id", "Workout has no id"))
                continue
            if workout_id in seen_workout_ids:
                anomalies.append(
                    AnomalyRecord(source_name, page, "duplicate_workout_id", "Duplicate workout id", workout_id)
                )
            seen_workout_ids.add(workout_id)
            state = _optional_str(raw_workout.get("state"))
            _check_state(state, "workout", workout_id, source_name, page, anomalies)
            workout_item_ids = [_id for _id in (_to_int(value) for value in raw_workout.get("workout_item_ids") or []) if _id is not None]
            referenced_item_ids.update(workout_item_ids)
            for item_id in workout_item_ids:
                if item_id not in item_ids:
                    anomalies.append(
                        AnomalyRecord(
                            source_name,
                            page,
                            "missing_referenced_item",
                            f"Workout references missing item id {item_id}",
                            workout_id,
                        )
                    )
            workouts.append(
                WorkoutRecord(
                    source_file=source_name,
                    source_page=page,
                    source_id=workout_id,
                    client_id=_to_int(raw_workout.get("client_id")),
                    due=_optional_str(raw_workout.get("due")),
                    state=state,
                    rest_day=bool(raw_workout.get("rest_day")),
                    title=_optional_str(raw_workout.get("title")),
                    program_id=_to_int(raw_workout.get("program_id")),
                    program_name=_optional_str(raw_workout.get("program_name")),
                    short_description=_optional_str(raw_workout.get("short_description")),
                    warmup=_optional_str(raw_workout.get("warmup")),
                    cooldown=_optional_str(raw_workout.get("cooldown")),
                    workout_item_ids=workout_item_ids,
                    comment_ids=[
                        _id for _id in (_to_int(value) for value in raw_workout.get("comment_ids") or []) if _id is not None
                    ],
                    created_at=_optional_str(raw_workout.get("created_at")),
                    updated_at=_optional_str(raw_workout.get("updated_at")),
                    uuid=_optional_str(raw_workout.get("uuid")),
                )
            )

        for raw_item in raw_items:
            item_id = _to_int(raw_item.get("id"))
            workout_id = _to_int(raw_item.get("workout_id"))
            if item_id is None:
                anomalies.append(AnomalyRecord(source_name, page, "missing_item_id", "Workout item has no id"))
                continue
            if workout_id is None:
                anomalies.append(AnomalyRecord(source_name, page, "missing_item_workout_id", "Workout item has no workout_id", item_id))
                continue
            if item_id in seen_item_ids:
                anomalies.append(AnomalyRecord(source_name, page, "duplicate_item_id", "Duplicate workout item id", item_id))
            seen_item_ids.add(item_id)
            if workout_id not in workout_ids:
                anomalies.append(
                    AnomalyRecord(source_name, page, "orphan_item", "Workout item references missing workout", item_id)
                )
            if item_id not in referenced_item_ids:
                anomalies.append(
                    AnomalyRecord(source_name, page, "unreferenced_item", "Workout item is not listed in workout_item_ids", item_id)
                )
            state = _optional_str(raw_item.get("state"))
            _check_state(state, "workout item", item_id, source_name, page, anomalies)
            raw_attachments = raw_item.get("attachments") or []
            workout_items.append(
                WorkoutItemRecord(
                    source_file=source_name,
                    source_page=page,
                    source_id=item_id,
                    workout_source_id=workout_id,
                    position=_to_int(raw_item.get("position")),
                    name=_str(raw_item.get("name")),
                    name_display=_str(raw_item.get("name")).strip(),
                    info=_str(raw_item.get("info")),
                    info_display=_str(raw_item.get("info")).strip(),
                    result=_str(raw_item.get("result")),
                    result_display=_str(raw_item.get("result")).strip(),
                    state=state,
                    is_circuit=bool(raw_item.get("is_circuit")),
                    exercise_id=_to_int(raw_item.get("exercise_id")),
                    selected_exercises=raw_item.get("selected_exercises") or [],
                    linked=bool(raw_item.get("linked")),
                    assessment_id=_to_int(raw_item.get("assessment_id")),
                    request_video=bool(raw_item.get("request_video")),
                    attachment_count=len(raw_attachments),
                    created_at=_optional_str(raw_item.get("created_at")),
                )
            )
            for raw_attachment in raw_attachments:
                url = _optional_str(raw_attachment.get("attachmentUrl"))
                if not url:
                    anomalies.append(
                        AnomalyRecord(source_name, page, "attachment_missing_url", "Attachment has no URL", item_id)
                    )
                attachments.append(
                    AttachmentRecord(
                        source_file=source_name,
                        source_page=page,
                        workout_item_source_id=item_id,
                        workout_source_id=workout_id,
                        name=_optional_str(raw_attachment.get("name")),
                        url=url,
                        mime_type=_optional_str(raw_attachment.get("type")),
                        size=_to_int(raw_attachment.get("size")),
                    )
                )

    return ParseResult(
        workouts=workouts,
        workout_items=workout_items,
        attachments=attachments,
        anomalies=anomalies,
    )


def parse_cached_workouts(
    *,
    paths: TrueCoachPaths | None = None,
    input_files: list[Path] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Path]:
    paths = paths or TrueCoachPaths()
    paths.ensure()
    inputs = input_files or sorted(paths.api_dir.glob("workouts-client-*-page-*.json"))
    if not inputs:
        raise RuntimeError(f"No workout API files found in {paths.api_dir}. Run `coach fetch-workouts` first.")
    result = parse_api_files(inputs)
    destination = output_dir or paths.parsed_dir
    destination.mkdir(parents=True, exist_ok=True)
    outputs = {
        "workouts": destination / "workouts.jsonl",
        "workout_items": destination / "workout_items.jsonl",
        "attachments": destination / "attachments.jsonl",
        "anomalies": destination / "anomalies.jsonl",
        "summary": destination / "summary.json",
    }
    _write_jsonl(outputs["workouts"], result.workouts)
    _write_jsonl(outputs["workout_items"], result.workout_items)
    _write_jsonl(outputs["attachments"], result.attachments)
    _write_jsonl(outputs["anomalies"], result.anomalies)
    outputs["summary"].write_text(
        json.dumps(_summary(result, inputs), indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return outputs


def _read_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object in {path}")
    return payload


def _validate_payload_shape(
    payload: dict[str, Any],
    source_file: str,
    page: int | None,
    anomalies: list[AnomalyRecord],
) -> None:
    for key in ("meta", "workouts", "workout_items"):
        if key not in payload:
            anomalies.append(AnomalyRecord(source_file, page, "missing_payload_key", f"Missing top-level key: {key}"))
    if not isinstance(payload.get("workouts", []), list):
        raise RuntimeError(f"Expected workouts list in {source_file}")
    if not isinstance(payload.get("workout_items", []), list):
        raise RuntimeError(f"Expected workout_items list in {source_file}")


def _source_page(payload: dict[str, Any]) -> int | None:
    meta = payload.get("meta") or {}
    return _to_int(meta.get("page"))


def _check_state(
    state: str | None,
    label: str,
    source_id: int,
    source_file: str,
    page: int | None,
    anomalies: list[AnomalyRecord],
) -> None:
    if state and state not in KNOWN_STATES:
        anomalies.append(
            AnomalyRecord(source_file, page, "unknown_state", f"Unknown {label} state: {state}", source_id)
        )


def _summary(result: ParseResult, input_files: list[Path]) -> dict[str, Any]:
    return {
        "input_files": [str(path) for path in sorted(input_files)],
        "workouts": len(result.workouts),
        "workout_items": len(result.workout_items),
        "attachments": len(result.attachments),
        "anomalies": len(result.anomalies),
        "rest_days": sum(1 for workout in result.workouts if workout.rest_day),
        "items_with_truecoach_exercise_id": sum(1 for item in result.workout_items if item.exercise_id is not None),
        "items_without_truecoach_exercise_id": sum(1 for item in result.workout_items if item.exercise_id is None),
        "workout_states": _counts(record.state for record in result.workouts),
        "item_states": _counts(record.state for record in result.workout_items),
    }


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _write_jsonl(path: Path, records: list[Any]) -> None:
    with path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(asdict(record), sort_keys=True, ensure_ascii=False))
            output.write("\n")


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
