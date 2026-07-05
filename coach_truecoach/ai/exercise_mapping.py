from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import Select, and_, exists, func, select
from sqlalchemy.orm import Session

from ..config import TrueCoachPaths
from ..db import load_active_exercise_abbreviations
from ..db.models import Exercise, ExerciseNameAlias, Workout, WorkoutItem, WorkoutItemExercise
from .category_assignment import AISettings, _build_model, load_ai_settings


@dataclass(frozen=True)
class ExerciseOption:
    id: int
    name: str
    description: str | None
    review_status: str
    match_type: str
    matched_text: str | None


@dataclass(frozen=True)
class CurrentExerciseMapping:
    exercise_id: int
    exercise_name: str
    source: str
    review_status: str
    position: int | None
    role: str | None


@dataclass(frozen=True)
class ExerciseMappingInput:
    workout_item_id: int
    tc_workout_item_id: int
    workout_id: int
    workout_due_date: str | None
    workout_state: str | None
    workout_title: str | None
    workout_program_name: str | None
    name_raw: str
    name_display: str
    info_raw: str
    info_display: str
    result_raw: str
    result_display: str
    state: str | None
    is_circuit: bool
    selected_exercises: list[Any]
    linked: bool
    attachment_count: int
    current_exercises: list[CurrentExerciseMapping]
    candidate_exercises: list[ExerciseOption]
    abbreviations: dict[str, str]


@dataclass(frozen=True)
class ProposedExercise:
    position: int
    source_phrase: str
    canonical_exercise_id: int | None
    canonical_name: str
    match_type: str
    confidence: Decimal
    rationale: str


@dataclass(frozen=True)
class ExerciseMappingProposal:
    workout_item_id: int
    exercises: list[ProposedExercise]


@dataclass(frozen=True)
class ExerciseMappingRunSummary:
    output_dir: Path
    total_selected: int
    success_count: int
    failure_count: int
    manifest_path: Path
    proposals_path: Path
    inserted_count: int = 0
    unchanged_count: int = 0
    created_exercise_count: int = 0
    alias_inserted_count: int = 0


class ExerciseMapper(Protocol):
    def map_exercises(self, item: ExerciseMappingInput) -> str:
        """Return the raw model response."""


class StrandsExerciseMapper:
    def __init__(self, settings: AISettings) -> None:
        self._settings = settings

    def map_exercises(self, item: ExerciseMappingInput) -> str:
        agent_cls = _load_agent_class()
        model = _build_model(self._settings)
        prompt_context = _build_prompt_context(item)
        prompt = _build_prompt(prompt_context)
        agent = agent_cls(
            model=model,
            system_prompt=(
                "You map workout items to zero or more atomic canonical exercises. "
                "Return only valid JSON matching the requested schema. "
                "Use provided abbreviation expansions when interpreting abbreviations."
            ),
        )
        return str(agent(prompt)).strip()


def run_exercise_mapping_dry_run(
    session: Session,
    *,
    paths: TrueCoachPaths | None = None,
    provider: str | None = None,
    model: str | None = None,
    url: str | None = None,
    limit: int | None = None,
    workout_item_ids: list[int] | None = None,
    min_workout_item_id: int | None = None,
    max_workout_item_id: int | None = None,
    output_dir: Path | None = None,
    mapper: ExerciseMapper | None = None,
) -> ExerciseMappingRunSummary:
    return _run_exercise_mapping(
        session,
        paths=paths,
        provider=provider,
        model=model,
        url=url,
        limit=limit,
        workout_item_ids=workout_item_ids,
        min_workout_item_id=min_workout_item_id,
        max_workout_item_id=max_workout_item_id,
        output_dir=output_dir,
        mapper=mapper,
        persist=False,
    )


def run_exercise_mapping_write(
    session: Session,
    *,
    paths: TrueCoachPaths | None = None,
    provider: str | None = None,
    model: str | None = None,
    url: str | None = None,
    limit: int | None = None,
    workout_item_ids: list[int] | None = None,
    min_workout_item_id: int | None = None,
    max_workout_item_id: int | None = None,
    output_dir: Path | None = None,
    mapper: ExerciseMapper | None = None,
) -> ExerciseMappingRunSummary:
    return _run_exercise_mapping(
        session,
        paths=paths,
        provider=provider,
        model=model,
        url=url,
        limit=limit,
        workout_item_ids=workout_item_ids,
        min_workout_item_id=min_workout_item_id,
        max_workout_item_id=max_workout_item_id,
        output_dir=output_dir,
        mapper=mapper,
        persist=True,
    )


def _run_exercise_mapping(
    session: Session,
    *,
    paths: TrueCoachPaths | None,
    provider: str | None,
    model: str | None,
    url: str | None,
    limit: int | None,
    workout_item_ids: list[int] | None,
    min_workout_item_id: int | None,
    max_workout_item_id: int | None,
    output_dir: Path | None,
    mapper: ExerciseMapper | None,
    persist: bool,
) -> ExerciseMappingRunSummary:
    _validate_selection_args(
        limit=limit,
        min_workout_item_id=min_workout_item_id,
        max_workout_item_id=max_workout_item_id,
    )
    paths = paths or TrueCoachPaths()
    paths.ensure()
    settings = load_ai_settings(provider=provider, model=model, url=url)
    abbreviations = load_active_exercise_abbreviations(session)
    items = select_exercise_mapping_inputs(
        session,
        workout_item_ids=workout_item_ids,
        min_workout_item_id=min_workout_item_id,
        max_workout_item_id=max_workout_item_id,
        limit=limit,
        abbreviations=abbreviations,
    )
    run_dir = _resolve_output_dir(paths=paths, output_dir=output_dir)
    manifest_path = run_dir / "manifest.json"
    proposals_path = run_dir / "proposals.jsonl"
    exercise_mapper = mapper or StrandsExerciseMapper(settings)

    success_count = 0
    failure_count = 0
    inserted_count = 0
    unchanged_count = 0
    created_exercise_count = 0
    alias_inserted_count = 0
    with proposals_path.open("w", encoding="utf-8") as handle:
        for item in items:
            prompt_context = _build_prompt_context(item)
            record: dict[str, Any] = {
                "workout_item_id": item.workout_item_id,
                "tc_workout_item_id": item.tc_workout_item_id,
                "workout_id": item.workout_id,
                "provider": settings.provider,
                "model": settings.model,
                "url": settings.url,
                "prompt_context": prompt_context,
                "parse_status": "error",
            }
            try:
                raw_response = exercise_mapper.map_exercises(item)
                record["raw_response"] = raw_response
                proposal = _parse_model_response(
                    raw_response,
                    item,
                    expected_workout_item_id=item.workout_item_id,
                )
                record["proposal"] = _proposal_to_record(proposal)
                record["parse_status"] = "ok"
                if persist:
                    write_summary = _write_exercise_mapping_assertions(session, proposal, item, settings)
                    record["db_write_status"] = write_summary["status"]
                    record["db_write_summary"] = write_summary
                    inserted_count += int(write_summary["inserted_count"])
                    unchanged_count += int(write_summary["unchanged_count"])
                    created_exercise_count += int(write_summary["created_exercise_count"])
                    alias_inserted_count += int(write_summary["alias_inserted_count"])
                success_count += 1
            except Exception as exc:
                record["error"] = str(exc)
                failure_count += 1
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    manifest = {
        "run_timestamp": _utc_now().isoformat(),
        "provider": settings.provider,
        "model": settings.model,
        "url": settings.url,
        "mode": "write" if persist else "dry_run",
        "selection_mode": _selection_mode(
            workout_item_ids=workout_item_ids,
            min_workout_item_id=min_workout_item_id,
            max_workout_item_id=max_workout_item_id,
        ),
        "filters": {
            "limit": limit,
            "workout_item_ids": workout_item_ids or [],
            "min_workout_item_id": min_workout_item_id,
            "max_workout_item_id": max_workout_item_id,
        },
        "total_selected": len(items),
        "success_count": success_count,
        "failure_count": failure_count,
        "inserted_count": inserted_count,
        "unchanged_count": unchanged_count,
        "created_exercise_count": created_exercise_count,
        "alias_inserted_count": alias_inserted_count,
        "proposals_path": str(proposals_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return ExerciseMappingRunSummary(
        output_dir=run_dir,
        total_selected=len(items),
        success_count=success_count,
        failure_count=failure_count,
        manifest_path=manifest_path,
        proposals_path=proposals_path,
        inserted_count=inserted_count,
        unchanged_count=unchanged_count,
        created_exercise_count=created_exercise_count,
        alias_inserted_count=alias_inserted_count,
    )


def select_exercise_mapping_inputs(
    session: Session,
    *,
    workout_item_ids: list[int] | None = None,
    min_workout_item_id: int | None = None,
    max_workout_item_id: int | None = None,
    limit: int | None = None,
    abbreviations: dict[str, str] | None = None,
) -> list[ExerciseMappingInput]:
    statement = build_exercise_mapping_selection_statement(
        workout_item_ids=workout_item_ids,
        min_workout_item_id=min_workout_item_id,
        max_workout_item_id=max_workout_item_id,
        limit=limit,
    )
    rows = session.execute(statement).all()
    active_abbreviations = abbreviations if abbreviations is not None else load_active_exercise_abbreviations(session)
    return [
        ExerciseMappingInput(
            workout_item_id=item.id,
            tc_workout_item_id=item.tc_workout_item_id,
            workout_id=item.workout_id,
            workout_due_date=workout.due_date.isoformat() if workout.due_date else None,
            workout_state=workout.state,
            workout_title=workout.title,
            workout_program_name=workout.program_name,
            name_raw=item.name_raw,
            name_display=item.name_display,
            info_raw=item.info_raw,
            info_display=item.info_display,
            result_raw=item.result_raw,
            result_display=item.result_display,
            state=item.state,
            is_circuit=item.is_circuit,
            selected_exercises=item.selected_exercises,
            linked=item.linked,
            attachment_count=item.attachment_count,
            current_exercises=_load_current_exercise_mappings(session, item.id),
            candidate_exercises=find_exercise_candidates(session, item, active_abbreviations),
            abbreviations=active_abbreviations,
        )
        for item, workout in rows
    ]


def build_exercise_mapping_selection_statement(
    *,
    workout_item_ids: list[int] | None = None,
    min_workout_item_id: int | None = None,
    max_workout_item_id: int | None = None,
    limit: int | None = None,
) -> Select[tuple[WorkoutItem, Workout]]:
    statement: Select[tuple[WorkoutItem, Workout]] = (
        select(WorkoutItem, Workout)
        .join(Workout, Workout.id == WorkoutItem.workout_id)
        .order_by(Workout.due_date.asc().nullsfirst(), WorkoutItem.id.asc())
    )

    current_ai_mapping_exists = exists(
        select(1).where(
            and_(
                WorkoutItemExercise.workout_item_id == WorkoutItem.id,
                WorkoutItemExercise.source == "ai",
                WorkoutItemExercise.is_current.is_(True),
                WorkoutItemExercise.review_status.in_(("pending", "approved")),
            )
        )
    )

    eligible_window_filters: list[Any] = [~current_ai_mapping_exists]
    if min_workout_item_id is not None:
        eligible_window_filters.append(WorkoutItem.id >= min_workout_item_id)
    if max_workout_item_id is not None:
        eligible_window_filters.append(WorkoutItem.id <= max_workout_item_id)

    if workout_item_ids:
        explicit_ids = WorkoutItem.id.in_(workout_item_ids)
        if min_workout_item_id is not None or max_workout_item_id is not None:
            statement = statement.where(explicit_ids | and_(*eligible_window_filters))
        else:
            statement = statement.where(explicit_ids)
    else:
        statement = statement.where(and_(*eligible_window_filters))

    if limit is not None:
        statement = statement.limit(limit)

    return statement


def find_exercise_candidates(
    session: Session,
    item: WorkoutItem,
    abbreviations: dict[str, str],
    *,
    limit: int = 25,
) -> list[ExerciseOption]:
    text_values = [
        item.name_display,
        item.name_raw,
        item.info_display,
        item.info_raw,
        " ".join(str(value) for value in item.selected_exercises or []),
    ]
    text_blob = " ".join(value for value in text_values if value)
    normalized_blob = _normalize_text(text_blob)
    expanded_blob = _normalize_text(_expand_abbreviations(text_blob, abbreviations))
    phrases = {_normalize_text(value) for value in text_values if value}
    phrases.discard("")
    phrases.add(expanded_blob)

    candidates: dict[int, ExerciseOption] = {}
    exercises = (
        session.execute(select(Exercise).where(Exercise.deleted_at.is_(None)).order_by(Exercise.name.asc()))
        .scalars()
        .all()
    )
    for exercise in exercises:
        name_norm = _normalize_text(exercise.name)
        match_type: str | None = None
        matched_text: str | None = None
        if name_norm in phrases:
            match_type = "exact"
            matched_text = exercise.name
        elif name_norm and (name_norm in normalized_blob or name_norm in expanded_blob):
            match_type = "substring"
            matched_text = exercise.name
        elif _token_overlap(name_norm, expanded_blob) >= 2:
            match_type = "token"
            matched_text = exercise.name
        if match_type:
            candidates[exercise.id] = ExerciseOption(
                id=exercise.id,
                name=exercise.name,
                description=exercise.description,
                review_status=exercise.review_status,
                match_type=match_type,
                matched_text=matched_text,
            )

    aliases = (
        session.execute(
            select(ExerciseNameAlias, Exercise)
            .join(Exercise, Exercise.id == ExerciseNameAlias.exercise_id)
            .where(
                ExerciseNameAlias.is_current.is_(True),
                ExerciseNameAlias.review_status.in_(("pending", "approved")),
                ExerciseNameAlias.deleted_at.is_(None),
                Exercise.deleted_at.is_(None),
            )
            .order_by(ExerciseNameAlias.alias.asc())
        )
        .all()
    )
    for alias, exercise in aliases:
        alias_norm = _normalize_text(alias.alias)
        if alias_norm and (alias_norm in normalized_blob or alias_norm in expanded_blob):
            candidates[exercise.id] = ExerciseOption(
                id=exercise.id,
                name=exercise.name,
                description=exercise.description,
                review_status=exercise.review_status,
                match_type="alias",
                matched_text=alias.alias,
            )

    priority = {"exact": 0, "alias": 1, "substring": 2, "token": 3}
    return sorted(candidates.values(), key=lambda candidate: (priority[candidate.match_type], candidate.name))[:limit]


def archive_exercise_mapping_run(
    *,
    paths: TrueCoachPaths | None = None,
    run_dir: Path,
    archived_dir: Path | None = None,
) -> Path:
    paths = paths or TrueCoachPaths()
    paths.ensure()
    source_dir = run_dir.resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        raise RuntimeError(f"Run directory does not exist: {run_dir}")
    destination_root = (archived_dir or paths.exercise_mapping_archived_dir).resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = destination_root / source_dir.name
    if destination.exists():
        raise RuntimeError(f"Archived run directory already exists: {destination}")
    shutil.move(str(source_dir), str(destination))
    return destination


def _parse_model_response(
    raw_response: str,
    item: ExerciseMappingInput,
    *,
    expected_workout_item_id: int,
) -> ExerciseMappingProposal:
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Model response was not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Model response must be a JSON object.")
    raw_item_id = payload.get("workout_item_id")
    if int(raw_item_id) != expected_workout_item_id:
        raise RuntimeError(
            f"Model response workout_item_id {raw_item_id!r} did not match expected {expected_workout_item_id}."
        )

    raw_exercises = payload.get("exercises")
    if not isinstance(raw_exercises, list):
        raise RuntimeError("Model response exercises must be a list.")

    known_exercise_ids = {candidate.id for candidate in item.candidate_exercises}
    known_exercise_ids.update(mapping.exercise_id for mapping in item.current_exercises)
    seen_existing_ids: set[int] = set()
    seen_new_names: set[str] = set()
    proposals: list[ProposedExercise] = []
    zero_based_positions = any(
        _is_zero_position(raw_exercise.get("position"))
        for raw_exercise in raw_exercises
        if isinstance(raw_exercise, dict)
    )
    for index, raw_exercise in enumerate(raw_exercises, start=1):
        if not isinstance(raw_exercise, dict):
            raise RuntimeError("Each proposed exercise must be an object.")
        position = index if zero_based_positions else _parse_position(raw_exercise.get("position"), index)
        source_phrase = str(raw_exercise.get("source_phrase", "")).strip()
        if not source_phrase:
            raise RuntimeError("Proposed exercise source_phrase was empty.")
        canonical_name = _parse_canonical_name(raw_exercise.get("canonical_name"))
        match_type = str(raw_exercise.get("match_type", "")).strip() or "new"
        if match_type == "none":
            raise RuntimeError("Model response included a non-exercise placeholder entry.")
        raw_id = raw_exercise.get("canonical_exercise_id")
        canonical_exercise_id = None if raw_id in (None, "") else int(raw_id)
        if canonical_exercise_id is not None:
            if canonical_exercise_id not in known_exercise_ids:
                raise RuntimeError(f"Unknown canonical_exercise_id: {canonical_exercise_id}")
            if canonical_exercise_id in seen_existing_ids:
                raise RuntimeError(f"Duplicate canonical_exercise_id: {canonical_exercise_id}")
            seen_existing_ids.add(canonical_exercise_id)
        else:
            name_key = _normalize_text(canonical_name)
            if name_key in seen_new_names:
                raise RuntimeError(f"Duplicate new canonical exercise name: {canonical_name!r}")
            seen_new_names.add(name_key)
        _validate_abbreviation_claims(source_phrase, canonical_name, item.abbreviations)
        proposals.append(
            ProposedExercise(
                position=position,
                source_phrase=source_phrase,
                canonical_exercise_id=canonical_exercise_id,
                canonical_name=canonical_name,
                match_type=match_type,
                confidence=_parse_confidence(raw_exercise.get("confidence")),
                rationale=_parse_required_text(raw_exercise.get("rationale"), "rationale"),
            )
        )

    return ExerciseMappingProposal(workout_item_id=expected_workout_item_id, exercises=proposals)


def _write_exercise_mapping_assertions(
    session: Session,
    proposal: ExerciseMappingProposal,
    item: ExerciseMappingInput,
    settings: AISettings,
) -> dict[str, int | str]:
    current_pending_ai_rows = _load_current_pending_ai_exercises(session, proposal.workout_item_id)
    desired_rows: list[tuple[ProposedExercise, int, bool]] = []
    created_exercise_count = 0
    alias_inserted_count = 0
    for proposed in proposal.exercises:
        exercise_id, created = _resolve_or_create_exercise(session, proposed)
        if created:
            created_exercise_count += 1
        desired_rows.append((proposed, exercise_id, created))

    current_non_ai_exercise_ids = {
        mapping.exercise_id
        for mapping in item.current_exercises
        if mapping.source != "ai" and mapping.review_status in {"pending", "approved"}
    }
    desired_rows_to_insert = [
        (proposed, exercise_id)
        for proposed, exercise_id, _created in desired_rows
        if exercise_id not in current_non_ai_exercise_ids
    ]

    for proposed, exercise_id, created in desired_rows:
        if not created and _should_create_alias(proposed, exercise_id):
            if _create_pending_alias_if_missing(session, proposed, exercise_id, settings):
                alias_inserted_count += 1

    if _matches_existing_pending_assertions(current_pending_ai_rows, desired_rows_to_insert, settings):
        status = "inserted" if alias_inserted_count or created_exercise_count else "unchanged"
        return {
            "status": status,
            "inserted_count": 0,
            "unchanged_count": len(current_pending_ai_rows),
            "created_exercise_count": created_exercise_count,
            "alias_inserted_count": alias_inserted_count,
        }

    inserted_count = 0
    new_rows: list[WorkoutItemExercise] = []
    for proposed, exercise_id in desired_rows_to_insert:
        new_row = WorkoutItemExercise(
            workout_item_id=proposal.workout_item_id,
            exercise_id=exercise_id,
            position=proposed.position,
            role="primary",
            source="ai",
            confidence=proposed.confidence,
            review_status="pending",
            is_current=True,
            model_name=settings.model,
            model_version=settings.provider,
            rationale=proposed.rationale,
        )
        session.add(new_row)
        new_rows.append(new_row)
        inserted_count += 1
    session.flush()

    superseded_by_id = new_rows[0].id if new_rows else None
    for row in current_pending_ai_rows:
        row.review_status = "superseded"
        row.is_current = False
        row.superseded_by_id = superseded_by_id

    status = "inserted" if inserted_count or alias_inserted_count or created_exercise_count else "unchanged"
    return {
        "status": status,
        "inserted_count": inserted_count,
        "unchanged_count": 0 if status == "inserted" else len(current_pending_ai_rows),
        "created_exercise_count": created_exercise_count,
        "alias_inserted_count": alias_inserted_count,
    }


def _load_current_exercise_mappings(session: Session, workout_item_id: int) -> list[CurrentExerciseMapping]:
    rows = (
        session.execute(
            select(WorkoutItemExercise, Exercise)
            .join(Exercise, Exercise.id == WorkoutItemExercise.exercise_id)
            .where(
                WorkoutItemExercise.workout_item_id == workout_item_id,
                WorkoutItemExercise.is_current.is_(True),
                WorkoutItemExercise.review_status.in_(("pending", "approved")),
            )
            .order_by(WorkoutItemExercise.position.asc().nulls_last(), WorkoutItemExercise.id.asc())
        )
        .all()
    )
    return [
        CurrentExerciseMapping(
            exercise_id=exercise.id,
            exercise_name=exercise.name,
            source=row.source,
            review_status=row.review_status,
            position=row.position,
            role=row.role,
        )
        for row, exercise in rows
    ]


def _resolve_or_create_exercise(session: Session, proposed: ProposedExercise) -> tuple[int, bool]:
    if proposed.canonical_exercise_id is not None:
        return proposed.canonical_exercise_id, False
    existing = session.execute(
        select(Exercise).where(
            func.lower(func.btrim(Exercise.name)) == _normalize_text(proposed.canonical_name),
            Exercise.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id, False
    exercise = Exercise(
        name=_normalize_canonical_exercise_name(proposed.canonical_name),
        created_by_source="ai",
        review_status="pending",
    )
    session.add(exercise)
    session.flush()
    return exercise.id, True


def _load_current_pending_ai_exercises(session: Session, workout_item_id: int) -> list[WorkoutItemExercise]:
    return (
        session.execute(
            select(WorkoutItemExercise).where(
                WorkoutItemExercise.workout_item_id == workout_item_id,
                WorkoutItemExercise.source == "ai",
                WorkoutItemExercise.review_status == "pending",
                WorkoutItemExercise.is_current.is_(True),
            )
        )
        .scalars()
        .all()
    )


def _matches_existing_pending_assertions(
    rows: list[WorkoutItemExercise],
    proposed_rows: list[tuple[ProposedExercise, int]],
    settings: AISettings,
) -> bool:
    if len(rows) != len(proposed_rows):
        return False
    existing = sorted(rows, key=lambda row: (row.position or 0, row.exercise_id))
    proposed = sorted(proposed_rows, key=lambda pair: (pair[0].position, pair[1]))
    for row, (proposed_exercise, exercise_id) in zip(existing, proposed, strict=True):
        if (
            row.exercise_id != exercise_id
            or row.position != proposed_exercise.position
            or (row.role or "") != "primary"
            or row.confidence != proposed_exercise.confidence
            or (row.rationale or "").strip() != proposed_exercise.rationale
            or (row.model_name or "").strip() != settings.model
            or (row.model_version or "").strip() != settings.provider
        ):
            return False
    return True


def _should_create_alias(proposed: ProposedExercise, exercise_id: int) -> bool:
    phrase = _normalize_text(proposed.source_phrase)
    canonical = _normalize_text(proposed.canonical_name)
    return bool(phrase and canonical and phrase != canonical and exercise_id)


def _create_pending_alias_if_missing(
    session: Session,
    proposed: ProposedExercise,
    exercise_id: int,
    settings: AISettings,
) -> bool:
    alias_key = _normalize_text(proposed.source_phrase)
    existing = session.execute(
        select(ExerciseNameAlias).where(
            func.lower(func.btrim(ExerciseNameAlias.alias)) == alias_key,
            ExerciseNameAlias.is_current.is_(True),
            ExerciseNameAlias.review_status.in_(("pending", "approved")),
            ExerciseNameAlias.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    session.add(
        ExerciseNameAlias(
            exercise_id=exercise_id,
            alias=proposed.source_phrase,
            source="ai",
            confidence=proposed.confidence,
            review_status="pending",
            is_current=True,
            model_name=settings.model,
            model_version=settings.provider,
            rationale=proposed.rationale,
        )
    )
    return True


def _proposal_to_record(proposal: ExerciseMappingProposal) -> dict[str, Any]:
    return {
        "workout_item_id": proposal.workout_item_id,
        "exercises": [
            {
                **asdict(exercise),
                "confidence": str(exercise.confidence),
            }
            for exercise in proposal.exercises
        ],
    }


def _build_prompt_context(item: ExerciseMappingInput) -> dict[str, Any]:
    return {
        "workout_item": {
            key: value
            for key, value in asdict(item).items()
            if key not in {"candidate_exercises", "abbreviations", "current_exercises"}
        },
        "current_exercises": [asdict(mapping) for mapping in item.current_exercises],
        "candidate_exercises": [asdict(candidate) for candidate in item.candidate_exercises],
        "abbreviations": item.abbreviations,
    }


def _build_prompt(context: dict[str, Any]) -> str:
    return (
        "Map the workout item to zero or more atomic canonical exercises.\n"
        "Return JSON only with keys: workout_item_id, exercises.\n"
        "Each exercise object must have keys: position, source_phrase, canonical_exercise_id, "
        "canonical_name, match_type, confidence, rationale.\n"
        "Rules:\n"
        "- Use candidate canonical_exercise_id values when the candidate is fundamentally the same movement.\n"
        "- Use null canonical_exercise_id only for a genuinely new atomic movement.\n"
        "- Omit non-exercise text entirely, such as pacing notes, rep schemes, quality cues, or instructions like "
        "'unbroken every round'. Do not emit placeholder rows for them.\n"
        "- One workout item can contain multiple exercises; preserve their order from the text.\n"
        "- Treat all mapped exercises as primary movements for v1.\n"
        "- Abbreviations must only use the provided abbreviation map; do not invent unsupported expansions.\n"
        "- Do not map equipment-only abbreviations such as dumbbell or barbell as standalone exercises.\n"
        "- Never use placeholder names such as null, none, n/a, or similar values for canonical_name.\n"
        "- confidence must be a number from 0 to 1.\n"
        "- rationale must be concise and grounded in the item text.\n"
        "- Do not output markdown.\n\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def _validate_selection_args(
    *,
    limit: int | None,
    min_workout_item_id: int | None,
    max_workout_item_id: int | None,
) -> None:
    if limit is not None and limit < 1:
        raise RuntimeError("--limit must be at least 1")
    if min_workout_item_id is not None and min_workout_item_id < 1:
        raise RuntimeError("--min-workout-item-id must be at least 1")
    if max_workout_item_id is not None and max_workout_item_id < 1:
        raise RuntimeError("--max-workout-item-id must be at least 1")
    if (
        min_workout_item_id is not None
        and max_workout_item_id is not None
        and min_workout_item_id > max_workout_item_id
    ):
        raise RuntimeError("--min-workout-item-id cannot be greater than --max-workout-item-id")


def _resolve_output_dir(*, paths: TrueCoachPaths, output_dir: Path | None) -> Path:
    if output_dir is None:
        timestamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
        resolved = paths.exercise_mapping_active_dir / timestamp
    else:
        resolved = output_dir
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _selection_mode(
    *,
    workout_item_ids: list[int] | None,
    min_workout_item_id: int | None,
    max_workout_item_id: int | None,
) -> str:
    if workout_item_ids and (min_workout_item_id is not None or max_workout_item_id is not None):
        return "window_plus_explicit_ids"
    if workout_item_ids:
        return "explicit_ids"
    if min_workout_item_id is not None or max_workout_item_id is not None:
        return "window"
    return "unmapped_by_ai"


def _parse_position(value: Any, fallback: int) -> int:
    if value in (None, ""):
        return fallback
    position = int(value)
    if position < 1:
        raise RuntimeError(f"Exercise position must be at least 1: {value!r}")
    return position


def _is_zero_position(value: Any) -> bool:
    try:
        return int(value) == 0
    except (TypeError, ValueError):
        return False


def _parse_confidence(value: Any) -> Decimal:
    try:
        confidence = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError(f"Model response confidence was invalid: {value!r}") from exc
    if confidence < 0 or confidence > 1:
        raise RuntimeError(f"Model response confidence must be between 0 and 1: {value!r}")
    return confidence.quantize(Decimal("0.001"))


def _parse_required_text(value: Any, key: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"Model response {key} was empty.")
    return text


def _parse_canonical_name(value: Any) -> str:
    if value is None:
        raise RuntimeError("Proposed exercise canonical_name was empty.")
    text = str(value).strip()
    if not text:
        raise RuntimeError("Proposed exercise canonical_name was empty.")
    if _normalize_text(text) in {"none", "null", "n a", "na"}:
        raise RuntimeError(f"Proposed exercise canonical_name was not a valid exercise name: {text!r}")
    return text


def _validate_abbreviation_claims(source_phrase: str, canonical_name: str, abbreviations: dict[str, str]) -> None:
    source_tokens = set(_normalize_text(source_phrase).split())
    canonical_norm = _normalize_text(canonical_name)
    for abbreviation, expansion in abbreviations.items():
        abbreviation_norm = _normalize_text(abbreviation)
        expansion_norm = _normalize_text(expansion)
        if abbreviation_norm in source_tokens and expansion_norm not in canonical_norm:
            if expansion_norm in {"dumbbell", "kettlebell", "barbell"}:
                continue
            raise RuntimeError(
                f"Abbreviation {abbreviation!r} must map to provided expansion {expansion!r}, "
                f"not {canonical_name!r}."
            )


def _expand_abbreviations(text: str, abbreviations: dict[str, str]) -> str:
    expanded = text
    for abbreviation, expansion in sorted(abbreviations.items(), key=lambda pair: len(pair[0]), reverse=True):
        expanded = re.sub(
            rf"\b{re.escape(abbreviation)}\b",
            expansion,
            expanded,
            flags=re.IGNORECASE,
        )
    return expanded


def _token_overlap(left: str, right: str) -> int:
    left_tokens = {token for token in left.split() if len(token) > 2}
    right_tokens = {token for token in right.split() if len(token) > 2}
    return len(left_tokens & right_tokens)


def _normalize_text(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _normalize_canonical_exercise_name(value: str) -> str:
    text = " ".join(value.split())
    if not text:
        return text
    if any(character.isupper() for character in text[1:]):
        return text[0].upper() + text[1:]
    tokens = []
    for token in text.split(" "):
        if not token:
            continue
        if "-" in token:
            parts = [part[:1].upper() + part[1:] if part else part for part in token.split("-")]
            tokens.append("-".join(parts))
        else:
            tokens.append(token[:1].upper() + token[1:])
    return " ".join(tokens)


def _load_agent_class() -> Any:
    from importlib import import_module

    try:
        module = import_module("strands")
    except ImportError as exc:
        raise RuntimeError(
            "Missing AI dependency. Run `uv sync` to install the project dependencies, "
            "including `strands-agents`."
        ) from exc
    try:
        return getattr(module, "Agent")
    except AttributeError as exc:
        raise RuntimeError("Could not find Agent in strands.") from exc


def _utc_now() -> datetime:
    return datetime.now(UTC)
