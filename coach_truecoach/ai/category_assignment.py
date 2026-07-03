from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv
from sqlalchemy import Select, and_, exists, select
from sqlalchemy.orm import Session

from ..config import TrueCoachPaths
from ..db.models import Workout, WorkoutCategory, WorkoutItem, WorkoutItemCategory


@dataclass(frozen=True)
class AISettings:
    provider: str
    model: str
    url: str | None = None
    openai_api_key: str | None = None


@dataclass(frozen=True)
class CategoryOption:
    id: int
    name: str
    description: str | None
    color_code: str | None


@dataclass(frozen=True)
class CategoryAssignmentInput:
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


@dataclass(frozen=True)
class CategoryAssignmentProposal:
    workout_item_id: int
    category_id: int
    category_name: str
    confidence: Decimal
    rationale: str


@dataclass(frozen=True)
class CategoryAssignmentRunSummary:
    output_dir: Path
    total_selected: int
    success_count: int
    failure_count: int
    manifest_path: Path
    proposals_path: Path
    inserted_count: int = 0
    unchanged_count: int = 0


class CategoryAssigner(Protocol):
    def assign(
        self,
        item: CategoryAssignmentInput,
        categories: list[CategoryOption],
    ) -> str:
        """Return the raw model response."""


class StrandsCategoryAssigner:
    def __init__(self, settings: AISettings) -> None:
        self._settings = settings

    def assign(
        self,
        item: CategoryAssignmentInput,
        categories: list[CategoryOption],
    ) -> str:
        agent_cls = _load_symbol("strands", "Agent")
        model = _build_model(self._settings)
        prompt_context = _build_prompt_context(item, categories)
        prompt = _build_prompt(prompt_context)
        agent = agent_cls(
            model=model,
            system_prompt=(
                "You classify workout items into exactly one existing category. "
                "Return only valid JSON matching the requested schema. "
                "Do not invent categories."
            ),
        )
        return str(agent(prompt)).strip()


def load_ai_settings(
    *,
    provider: str | None = None,
    model: str | None = None,
    url: str | None = None,
) -> AISettings:
    load_dotenv()
    resolved_provider = (provider or os.getenv("AI_PROVIDER") or "").strip().lower()
    resolved_model = (model or os.getenv("MODEL") or "").strip()
    resolved_url = (url or os.getenv("AI_URL") or "").strip() or None
    openai_api_key = (os.getenv("OPENAI_API_KEY") or "").strip() or None

    if resolved_provider not in {"ollama", "openai"}:
        raise RuntimeError("AI provider must be one of: ollama, openai")
    if not resolved_model:
        raise RuntimeError("MODEL is not set. Add it to .env, your shell, or pass --model.")
    if resolved_provider == "ollama" and not resolved_url:
        raise RuntimeError("AI_URL is required when provider is ollama.")
    if resolved_provider == "openai" and not openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required when provider is openai.")

    return AISettings(
        provider=resolved_provider,
        model=resolved_model,
        url=resolved_url,
        openai_api_key=openai_api_key,
    )


def run_category_assignment_dry_run(
    session: Session,
    *,
    paths: TrueCoachPaths | None = None,
    provider: str | None = None,
    model: str | None = None,
    url: str | None = None,
    limit: int | None = None,
    workout_item_ids: list[int] | None = None,
    output_dir: Path | None = None,
    assigner: CategoryAssigner | None = None,
) -> CategoryAssignmentRunSummary:
    return _run_category_assignment(
        session,
        paths=paths,
        provider=provider,
        model=model,
        url=url,
        limit=limit,
        workout_item_ids=workout_item_ids,
        output_dir=output_dir,
        assigner=assigner,
        persist=False,
    )


def run_category_assignment_write(
    session: Session,
    *,
    paths: TrueCoachPaths | None = None,
    provider: str | None = None,
    model: str | None = None,
    url: str | None = None,
    limit: int | None = None,
    workout_item_ids: list[int] | None = None,
    output_dir: Path | None = None,
    assigner: CategoryAssigner | None = None,
) -> CategoryAssignmentRunSummary:
    return _run_category_assignment(
        session,
        paths=paths,
        provider=provider,
        model=model,
        url=url,
        limit=limit,
        workout_item_ids=workout_item_ids,
        output_dir=output_dir,
        assigner=assigner,
        persist=True,
    )


def _run_category_assignment(
    session: Session,
    *,
    paths: TrueCoachPaths | None = None,
    provider: str | None = None,
    model: str | None = None,
    url: str | None = None,
    limit: int | None = None,
    workout_item_ids: list[int] | None = None,
    output_dir: Path | None = None,
    assigner: CategoryAssigner | None = None,
    persist: bool,
) -> CategoryAssignmentRunSummary:
    if limit is not None and limit < 1:
        raise RuntimeError("--limit must be at least 1")

    paths = paths or TrueCoachPaths()
    paths.ensure()
    settings = load_ai_settings(provider=provider, model=model, url=url)
    categories = _load_categories(session)
    items = select_category_assignment_inputs(
        session,
        workout_item_ids=workout_item_ids,
        limit=limit,
    )
    run_dir = _resolve_output_dir(paths=paths, output_dir=output_dir)
    manifest_path = run_dir / "manifest.json"
    proposals_path = run_dir / "proposals.jsonl"
    classifier = assigner or StrandsCategoryAssigner(settings)

    success_count = 0
    failure_count = 0
    inserted_count = 0
    unchanged_count = 0
    with proposals_path.open("w", encoding="utf-8") as handle:
        for item in items:
            prompt_context = _build_prompt_context(item, categories)
            record: dict[str, Any] = {
                "workout_item_id": item.workout_item_id,
                "tc_workout_item_id": item.tc_workout_item_id,
                "workout_id": item.workout_id,
                "provider": settings.provider,
                "model": settings.model,
                "url": settings.url,
                "categories": [asdict(category) for category in categories],
                "prompt_context": prompt_context,
                "parse_status": "error",
            }
            try:
                raw_response = classifier.assign(item, categories)
                record["raw_response"] = raw_response
                proposal = _parse_model_response(raw_response, categories, expected_workout_item_id=item.workout_item_id)
                record["proposal"] = _proposal_to_record(proposal)
                record["parse_status"] = "ok"
                if persist:
                    write_status = _write_category_assignment_assertion(session, proposal, settings)
                    record["db_write_status"] = write_status
                    if write_status == "inserted":
                        inserted_count += 1
                    else:
                        unchanged_count += 1
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
        "selection_mode": "explicit_ids" if workout_item_ids else "uncategorized",
        "filters": {
            "limit": limit,
            "workout_item_ids": workout_item_ids or [],
        },
        "total_selected": len(items),
        "success_count": success_count,
        "failure_count": failure_count,
        "inserted_count": inserted_count,
        "unchanged_count": unchanged_count,
        "proposals_path": str(proposals_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return CategoryAssignmentRunSummary(
        output_dir=run_dir,
        total_selected=len(items),
        success_count=success_count,
        failure_count=failure_count,
        manifest_path=manifest_path,
        proposals_path=proposals_path,
        inserted_count=inserted_count,
        unchanged_count=unchanged_count,
    )


def select_category_assignment_inputs(
    session: Session,
    *,
    workout_item_ids: list[int] | None = None,
    limit: int | None = None,
) -> list[CategoryAssignmentInput]:
    statement = build_category_assignment_selection_statement(
        workout_item_ids=workout_item_ids,
        limit=limit,
    )
    rows = session.execute(statement).all()
    return [
        CategoryAssignmentInput(
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
        )
        for item, workout in rows
    ]


def build_category_assignment_selection_statement(
    *,
    workout_item_ids: list[int] | None = None,
    limit: int | None = None,
) -> Select[tuple[WorkoutItem, Workout]]:
    statement: Select[tuple[WorkoutItem, Workout]] = (
        select(WorkoutItem, Workout)
        .join(Workout, Workout.id == WorkoutItem.workout_id)
        .order_by(Workout.due_date.asc().nullsfirst(), WorkoutItem.id.asc())
    )

    if workout_item_ids:
        statement = statement.where(WorkoutItem.id.in_(workout_item_ids))
    else:
        approved_exists = exists(
            select(1).where(
                and_(
                    WorkoutItemCategory.workout_item_id == WorkoutItem.id,
                    WorkoutItemCategory.review_status == "approved",
                    WorkoutItemCategory.is_current.is_(True),
                )
            )
        )
        statement = statement.where(~approved_exists)

    if limit is not None:
        statement = statement.limit(limit)

    return statement


def _load_categories(session: Session) -> list[CategoryOption]:
    rows = session.execute(select(WorkoutCategory).order_by(WorkoutCategory.id.asc())).scalars().all()
    if not rows:
        raise RuntimeError("No workout categories found. Run `coach db-seed-categories` first.")
    return [
        CategoryOption(
            id=row.id,
            name=row.name,
            description=row.description,
            color_code=row.color_code,
        )
        for row in rows
    ]


def _resolve_output_dir(*, paths: TrueCoachPaths, output_dir: Path | None) -> Path:
    if output_dir is None:
        timestamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
        resolved = paths.category_assignment_dir / timestamp
    else:
        resolved = output_dir
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _build_prompt_context(item: CategoryAssignmentInput, categories: list[CategoryOption]) -> dict[str, Any]:
    return {
        "workout_item": asdict(item),
        "categories": [asdict(category) for category in categories],
    }


def _build_prompt(context: dict[str, Any]) -> str:
    return (
        "Classify the workout item into exactly one category from the provided category list.\n"
        "Return JSON only with keys: workout_item_id, category_id, category_name, confidence, rationale.\n"
        "Rules:\n"
        "- category_id and category_name must match one provided category exactly.\n"
        "- confidence must be a number from 0 to 1.\n"
        "- rationale must be concise and grounded in the item text.\n"
        "- Do not output markdown.\n\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def _build_model(settings: AISettings) -> Any:
    if settings.provider == "ollama":
        model_cls = _load_symbol("strands.models.ollama", "OllamaModel")
        return model_cls(host=settings.url, model_id=settings.model)
    if settings.provider == "openai":
        model_cls = _load_symbol("strands.models.openai", "OpenAIModel")
        client_args: dict[str, Any] = {"api_key": settings.openai_api_key}
        if settings.url:
            client_args["base_url"] = settings.url
        return model_cls(model_id=settings.model, client_args=client_args)
    raise RuntimeError(f"Unsupported provider: {settings.provider}")


def _load_symbol(module_name: str, symbol_name: str) -> Any:
    try:
        module = import_module(module_name)
    except ImportError as exc:
        raise RuntimeError(
            "Missing AI dependency. Run `uv sync` to install the project dependencies, "
            "including `strands-agents`."
        ) from exc
    try:
        return getattr(module, symbol_name)
    except AttributeError as exc:
        raise RuntimeError(f"Could not find {symbol_name} in {module_name}.") from exc


def _parse_model_response(
    raw_response: str,
    categories: list[CategoryOption],
    *,
    expected_workout_item_id: int,
) -> CategoryAssignmentProposal:
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

    category = _match_category(payload, categories)
    confidence = _parse_confidence(payload.get("confidence"))
    rationale = str(payload.get("rationale", "")).strip()
    if not rationale:
        raise RuntimeError("Model response rationale was empty.")

    return CategoryAssignmentProposal(
        workout_item_id=expected_workout_item_id,
        category_id=category.id,
        category_name=category.name,
        confidence=confidence,
        rationale=rationale,
    )


def _match_category(payload: dict[str, Any], categories: list[CategoryOption]) -> CategoryOption:
    raw_category_id = payload.get("category_id")
    raw_category_name = str(payload.get("category_name", "")).strip()
    if not raw_category_name:
        raise RuntimeError("Model response category_name was empty.")

    category_by_id = {category.id: category for category in categories}
    category_by_name = {category.name.casefold(): category for category in categories}

    matched_by_id = None
    if raw_category_id is not None:
        try:
            matched_by_id = category_by_id.get(int(raw_category_id))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Model response category_id was invalid: {raw_category_id!r}") from exc

    matched_by_name = category_by_name.get(raw_category_name.casefold())
    if matched_by_id is None and matched_by_name is None:
        raise RuntimeError(f"Model response category was not in the seeded taxonomy: {raw_category_name!r}.")
    if matched_by_id is not None and matched_by_name is not None and matched_by_id.id != matched_by_name.id:
        raise RuntimeError("Model response category_id and category_name referred to different categories.")
    matched = matched_by_id or matched_by_name
    if matched is None:
        raise RuntimeError("Model response category could not be resolved.")
    return matched


def _parse_confidence(value: Any) -> Decimal:
    try:
        confidence = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError(f"Model response confidence was invalid: {value!r}") from exc
    if confidence < 0 or confidence > 1:
        raise RuntimeError(f"Model response confidence must be between 0 and 1: {value!r}")
    return confidence.quantize(Decimal("0.001"))


def _proposal_to_record(proposal: CategoryAssignmentProposal) -> dict[str, Any]:
    record = asdict(proposal)
    record["confidence"] = str(proposal.confidence)
    return record


def _write_category_assignment_assertion(
    session: Session,
    proposal: CategoryAssignmentProposal,
    settings: AISettings,
) -> str:
    current_rows = _load_current_pending_ai_categories(session, proposal.workout_item_id)
    for row in current_rows:
        if _matches_existing_pending_assertion(row, proposal, settings):
            return "unchanged"

    new_row = WorkoutItemCategory(
        workout_item_id=proposal.workout_item_id,
        category_id=proposal.category_id,
        source="ai",
        confidence=proposal.confidence,
        review_status="pending",
        is_current=True,
        model_name=settings.model,
        model_version=settings.provider,
        rationale=proposal.rationale,
    )
    session.add(new_row)
    session.flush()

    for row in current_rows:
        row.review_status = "superseded"
        row.is_current = False
        row.superseded_by_id = new_row.id

    return "inserted"


def _load_current_pending_ai_categories(session: Session, workout_item_id: int) -> list[WorkoutItemCategory]:
    return (
        session.execute(
            select(WorkoutItemCategory).where(
                WorkoutItemCategory.workout_item_id == workout_item_id,
                WorkoutItemCategory.source == "ai",
                WorkoutItemCategory.review_status == "pending",
                WorkoutItemCategory.is_current.is_(True),
            )
        )
        .scalars()
        .all()
    )


def _matches_existing_pending_assertion(
    row: WorkoutItemCategory,
    proposal: CategoryAssignmentProposal,
    settings: AISettings,
) -> bool:
    return (
        row.category_id == proposal.category_id
        and row.confidence == proposal.confidence
        and (row.rationale or "").strip() == proposal.rationale
        and (row.model_name or "").strip() == settings.model
        and (row.model_version or "").strip() == settings.provider
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)
