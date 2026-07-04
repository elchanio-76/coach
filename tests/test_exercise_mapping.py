from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.dialects import postgresql

from coach_truecoach.ai.category_assignment import load_ai_settings
from coach_truecoach.ai.exercise_mapping import (
    CurrentExerciseMapping,
    ExerciseMappingInput,
    ExerciseMappingProposal,
    ExerciseMappingRunSummary,
    ExerciseOption,
    ProposedExercise,
    _parse_model_response,
    _write_exercise_mapping_assertions,
    archive_exercise_mapping_run,
    build_exercise_mapping_selection_statement,
    run_exercise_mapping_dry_run,
    run_exercise_mapping_write,
)
from coach_truecoach.cli import main
from coach_truecoach.config import TrueCoachPaths
from coach_truecoach.db.importers import seed_exercise_abbreviations
from coach_truecoach.db.models import Exercise, ExerciseAbbreviation, ExerciseNameAlias, WorkoutItemExercise


class SelectionStatementTests(unittest.TestCase):
    def test_default_selection_skips_current_ai_mappings_only(self) -> None:
        statement = build_exercise_mapping_selection_statement()
        sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

        self.assertIn("NOT (EXISTS", sql)
        self.assertIn("workout_item_exercises.source = 'ai'", sql)
        self.assertIn("workout_item_exercises.review_status IN ('pending', 'approved')", sql)
        self.assertIn("workout_item_exercises.is_current IS true", sql)

    def test_explicit_ids_are_unioned_with_window_filter(self) -> None:
        statement = build_exercise_mapping_selection_statement(
            workout_item_ids=[11, 22],
            min_workout_item_id=100,
            max_workout_item_id=200,
            limit=5,
        )
        sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

        self.assertIn("workout_items.id IN (11, 22)", sql)
        self.assertIn("workout_items.id >= 100", sql)
        self.assertIn("workout_items.id <= 200", sql)
        self.assertIn("OR NOT (EXISTS", sql)
        self.assertIn("LIMIT 5", sql)


class ResponseValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.item = _exercise_input()

    def test_parse_model_response_accepts_existing_and_new_exercises(self) -> None:
        proposal = _parse_model_response(
            json.dumps(
                {
                    "workout_item_id": 7,
                    "exercises": [
                        {
                            "position": 1,
                            "source_phrase": "push-ups",
                            "canonical_exercise_id": 1,
                            "canonical_name": "Push-up",
                            "match_type": "alias",
                            "confidence": 0.93,
                            "rationale": "Press-up wording refers to push-ups.",
                        },
                        {
                            "position": 2,
                            "source_phrase": "wall walks",
                            "canonical_exercise_id": None,
                            "canonical_name": "Wall walk",
                            "match_type": "new",
                            "confidence": 0.81,
                            "rationale": "Distinct bodyweight movement listed separately.",
                        },
                    ],
                }
            ),
            self.item,
            expected_workout_item_id=7,
        )

        self.assertEqual(proposal.workout_item_id, 7)
        self.assertEqual(len(proposal.exercises), 2)
        self.assertEqual(proposal.exercises[0].confidence, Decimal("0.930"))
        self.assertIsNone(proposal.exercises[1].canonical_exercise_id)

    def test_parse_model_response_rejects_unknown_existing_id(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Unknown canonical_exercise_id"):
            _parse_model_response(
                json.dumps(
                    {
                        "workout_item_id": 7,
                        "exercises": [
                            {
                                "position": 1,
                                "source_phrase": "unknown",
                                "canonical_exercise_id": 999,
                                "canonical_name": "Unknown",
                                "match_type": "exact",
                                "confidence": 0.5,
                                "rationale": "Bad ID.",
                            }
                        ],
                    }
                ),
                self.item,
                expected_workout_item_id=7,
            )

    def test_parse_model_response_rejects_wrong_abbreviation_expansion(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Abbreviation 'hsw'"):
            _parse_model_response(
                json.dumps(
                    {
                        "workout_item_id": 7,
                        "exercises": [
                            {
                                "position": 1,
                                "source_phrase": "hsw",
                                "canonical_exercise_id": None,
                                "canonical_name": "Handstand hold",
                                "match_type": "new",
                                "confidence": 0.5,
                                "rationale": "Unsupported expansion.",
                            }
                        ],
                    }
                ),
                self.item,
                expected_workout_item_id=7,
            )

    def test_parse_model_response_converts_zero_based_positions_to_list_order(self) -> None:
        proposal = _parse_model_response(
            json.dumps(
                {
                    "workout_item_id": 7,
                    "exercises": [
                        {
                            "position": 0,
                            "source_phrase": "6 weighted chin ups",
                            "canonical_exercise_id": None,
                            "canonical_name": "Weighted chin-up",
                            "match_type": "new",
                            "confidence": 1.0,
                            "rationale": "Distinct weighted chin-up movement.",
                        },
                        {
                            "position": 1,
                            "source_phrase": "6 bent over reverse flies",
                            "canonical_exercise_id": None,
                            "canonical_name": "Bent over reverse fly",
                            "match_type": "new",
                            "confidence": 1.0,
                            "rationale": "Distinct reverse fly movement.",
                        },
                    ],
                }
            ),
            self.item,
            expected_workout_item_id=7,
        )

        self.assertEqual([exercise.position for exercise in proposal.exercises], [1, 2])

    def test_parse_model_response_rejects_malformed_payload(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "valid JSON"):
            _parse_model_response("not json", self.item, expected_workout_item_id=7)


class SeedAbbreviationTests(unittest.TestCase):
    def test_seed_exercise_abbreviations_inserts_and_updates_active_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "abbreviations.json"
            path.write_text(json.dumps({"hsw": "handstand walk", "db": "dumbbell"}), encoding="utf-8")
            session = _FakeSession()

            count = seed_exercise_abbreviations(session, path)

        self.assertEqual(count, 2)
        self.assertEqual(len(session.added), 2)
        self.assertIsInstance(session.added[0], ExerciseAbbreviation)
        self.assertEqual(session.added[0].source, "user")


class WriteAssertionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = load_ai_settings(provider="ollama", model="llama3.1", url="http://localhost:11434")
        self.item = _exercise_input()
        self.proposal = ExerciseMappingProposal(
            workout_item_id=7,
            exercises=[
                ProposedExercise(
                    position=1,
                    source_phrase="press-ups",
                    canonical_exercise_id=1,
                    canonical_name="Push-up",
                    match_type="alias",
                    confidence=Decimal("0.910"),
                    rationale="Press-ups are synonymous with push-ups.",
                )
            ],
        )

    def test_write_assertions_insert_pending_ai_row_and_alias(self) -> None:
        session = _FakeSession()
        with patch("coach_truecoach.ai.exercise_mapping._load_current_pending_ai_exercises", return_value=[]):
            summary = _write_exercise_mapping_assertions(session, self.proposal, self.item, self.settings)

        self.assertEqual(summary["status"], "inserted")
        self.assertEqual(summary["inserted_count"], 1)
        self.assertEqual(summary["alias_inserted_count"], 1)
        self.assertTrue(any(isinstance(row, WorkoutItemExercise) for row in session.added))
        self.assertTrue(any(isinstance(row, ExerciseNameAlias) for row in session.added))

    def test_write_assertions_create_pending_exercise_for_new_name(self) -> None:
        proposal = ExerciseMappingProposal(
            workout_item_id=7,
            exercises=[
                ProposedExercise(
                    position=1,
                    source_phrase="wall walks",
                    canonical_exercise_id=None,
                    canonical_name="Wall walk",
                    match_type="new",
                    confidence=Decimal("0.810"),
                    rationale="A distinct exercise not in the candidate list.",
                )
            ],
        )
        session = _FakeSession()
        with patch("coach_truecoach.ai.exercise_mapping._load_current_pending_ai_exercises", return_value=[]):
            summary = _write_exercise_mapping_assertions(session, proposal, self.item, self.settings)

        self.assertEqual(summary["created_exercise_count"], 1)
        self.assertTrue(any(isinstance(row, Exercise) and row.review_status == "pending" for row in session.added))

    def test_write_assertions_do_not_duplicate_existing_truecoach_mapping(self) -> None:
        item = _exercise_input(
            current_exercises=[
                CurrentExerciseMapping(
                    exercise_id=1,
                    exercise_name="Push-up",
                    source="truecoach",
                    review_status="approved",
                    position=1,
                    role="primary",
                )
            ]
        )
        session = _FakeSession()
        with patch("coach_truecoach.ai.exercise_mapping._load_current_pending_ai_exercises", return_value=[]):
            summary = _write_exercise_mapping_assertions(session, self.proposal, item, self.settings)

        self.assertEqual(summary["inserted_count"], 0)
        self.assertFalse(any(isinstance(row, WorkoutItemExercise) for row in session.added))

    def test_write_assertions_supersede_old_pending_rows(self) -> None:
        existing = WorkoutItemExercise(
            id=10,
            workout_item_id=7,
            exercise_id=2,
            position=1,
            role="primary",
            source="ai",
            confidence=Decimal("0.700"),
            review_status="pending",
            is_current=True,
            model_name="llama3.1",
            model_version="ollama",
            rationale="Previous guess.",
        )
        session = _FakeSession()
        with patch("coach_truecoach.ai.exercise_mapping._load_current_pending_ai_exercises", return_value=[existing]):
            summary = _write_exercise_mapping_assertions(session, self.proposal, self.item, self.settings)

        self.assertEqual(summary["inserted_count"], 1)
        self.assertEqual(existing.review_status, "superseded")
        self.assertFalse(existing.is_current)
        self.assertIsNotNone(existing.superseded_by_id)


@dataclass
class _StubMapper:
    responses: dict[int, str]

    def map_exercises(self, item: ExerciseMappingInput) -> str:
        return self.responses[item.workout_item_id]


class RunArtifactTests(unittest.TestCase):
    def test_dry_run_writes_manifest_and_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = TrueCoachPaths(cache_dir=Path(tmpdir) / "cache")
            with patch("coach_truecoach.ai.exercise_mapping.load_active_exercise_abbreviations", return_value={}), patch(
                "coach_truecoach.ai.exercise_mapping.select_exercise_mapping_inputs",
                return_value=[_exercise_input()],
            ), patch.dict(
                os.environ,
                {"AI_PROVIDER": "ollama", "MODEL": "llama3.1", "AI_URL": "http://localhost:11434"},
                clear=False,
            ):
                summary = run_exercise_mapping_dry_run(
                    session=object(),  # type: ignore[arg-type]
                    paths=paths,
                    mapper=_StubMapper(
                        {
                            7: json.dumps(
                                {
                                    "workout_item_id": 7,
                                    "exercises": [
                                        {
                                            "position": 1,
                                            "source_phrase": "push-up",
                                            "canonical_exercise_id": 1,
                                            "canonical_name": "Push-up",
                                            "match_type": "exact",
                                            "confidence": 0.91,
                                            "rationale": "Direct name match.",
                                        }
                                    ],
                                }
                            )
                        }
                    ),
                )

            manifest = json.loads(summary.manifest_path.read_text(encoding="utf-8"))
            records = [json.loads(line) for line in summary.proposals_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(summary.total_selected, 1)
            self.assertEqual(manifest["selection_mode"], "unmapped_by_ai")
            self.assertEqual(records[0]["parse_status"], "ok")
            self.assertEqual(summary.output_dir.parent, paths.exercise_mapping_active_dir)

    def test_write_run_records_db_write_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = TrueCoachPaths(cache_dir=Path(tmpdir) / "cache")
            with patch("coach_truecoach.ai.exercise_mapping.load_active_exercise_abbreviations", return_value={}), patch(
                "coach_truecoach.ai.exercise_mapping.select_exercise_mapping_inputs",
                return_value=[_exercise_input()],
            ), patch(
                "coach_truecoach.ai.exercise_mapping._write_exercise_mapping_assertions",
                return_value={
                    "status": "inserted",
                    "inserted_count": 1,
                    "unchanged_count": 0,
                    "created_exercise_count": 0,
                    "alias_inserted_count": 1,
                },
            ), patch.dict(
                os.environ,
                {"AI_PROVIDER": "ollama", "MODEL": "llama3.1", "AI_URL": "http://localhost:11434"},
                clear=False,
            ):
                summary = run_exercise_mapping_write(
                    session=object(),  # type: ignore[arg-type]
                    paths=paths,
                    mapper=_StubMapper(
                        {
                            7: json.dumps(
                                {
                                    "workout_item_id": 7,
                                    "exercises": [
                                        {
                                            "position": 1,
                                            "source_phrase": "push-up",
                                            "canonical_exercise_id": 1,
                                            "canonical_name": "Push-up",
                                            "match_type": "exact",
                                            "confidence": 0.91,
                                            "rationale": "Direct name match.",
                                        }
                                    ],
                                }
                            )
                        }
                    ),
                )

            records = [json.loads(line) for line in summary.proposals_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(summary.inserted_count, 1)
            self.assertEqual(summary.alias_inserted_count, 1)
            self.assertEqual(records[0]["db_write_status"], "inserted")


class ArchiveRunTests(unittest.TestCase):
    def test_archive_run_moves_directory_to_archived_area(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = TrueCoachPaths(cache_dir=Path(tmpdir) / "cache")
            paths.ensure()
            run_dir = paths.exercise_mapping_active_dir / "20260704T000000Z"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "manifest.json").write_text("{}", encoding="utf-8")

            archived_dir = archive_exercise_mapping_run(paths=paths, run_dir=run_dir)

            self.assertFalse(run_dir.exists())
            self.assertEqual(archived_dir.parent, paths.exercise_mapping_archived_dir)
            self.assertTrue((archived_dir / "manifest.json").exists())


class CliSmokeTests(unittest.TestCase):
    def test_cli_exercise_dry_run_passes_window_options(self) -> None:
        summary = ExerciseMappingRunSummary(
            output_dir=Path("/tmp/output"),
            total_selected=2,
            success_count=1,
            failure_count=1,
            manifest_path=Path("/tmp/output/manifest.json"),
            proposals_path=Path("/tmp/output/proposals.jsonl"),
        )

        @contextlib.contextmanager
        def _fake_session_scope(engine: object):
            yield object()

        stdout = io.StringIO()
        with patch("coach_truecoach.cli.create_engine", return_value=object()), patch(
            "coach_truecoach.cli.session_scope",
            side_effect=_fake_session_scope,
        ), patch(
            "coach_truecoach.cli.run_exercise_mapping_dry_run",
            return_value=summary,
        ) as run_mock, patch(
            "sys.argv",
            [
                "coach",
                "ai-exercise-mapping-dry-run",
                "--min-workout-item-id",
                "10",
                "--max-workout-item-id",
                "20",
            ],
        ), patch("sys.stdout", stdout):
            main()

        self.assertEqual(run_mock.call_args.kwargs["min_workout_item_id"], 10)
        self.assertEqual(run_mock.call_args.kwargs["max_workout_item_id"], 20)
        self.assertIn("Selected workout items: 2", stdout.getvalue())

    def test_cli_exercise_write_prints_summary(self) -> None:
        summary = ExerciseMappingRunSummary(
            output_dir=Path("/tmp/output"),
            total_selected=2,
            success_count=2,
            failure_count=0,
            inserted_count=2,
            unchanged_count=0,
            created_exercise_count=1,
            alias_inserted_count=1,
            manifest_path=Path("/tmp/output/manifest.json"),
            proposals_path=Path("/tmp/output/proposals.jsonl"),
        )

        @contextlib.contextmanager
        def _fake_session_scope(engine: object):
            yield object()

        stdout = io.StringIO()
        with patch("coach_truecoach.cli.create_engine", return_value=object()), patch(
            "coach_truecoach.cli.session_scope",
            side_effect=_fake_session_scope,
        ), patch(
            "coach_truecoach.cli.run_exercise_mapping_write",
            return_value=summary,
        ), patch(
            "sys.argv",
            ["coach", "ai-exercise-mapping-write", "--limit", "2"],
        ), patch("sys.stdout", stdout):
            main()

        output = stdout.getvalue()
        self.assertIn("Inserted assertions: 2", output)
        self.assertIn("Created exercises: 1", output)
        self.assertIn("Inserted aliases: 1", output)


class _FakeScalarResult:
    def __init__(self, value: object | None = None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object | None:
        return self.value


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_count = 0

    def execute(self, statement: object) -> _FakeScalarResult:
        return _FakeScalarResult()

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flush_count += 1
        for index, obj in enumerate(self.added, start=1):
            if getattr(obj, "id", None) is None:
                obj.id = 1000 + index


def _exercise_input(
    *,
    current_exercises: list[CurrentExerciseMapping] | None = None,
) -> ExerciseMappingInput:
    return ExerciseMappingInput(
        workout_item_id=7,
        tc_workout_item_id=700,
        workout_id=70,
        workout_due_date="2026-06-01",
        workout_state="completed",
        workout_title="Monday",
        workout_program_name="Cycle A",
        name_raw="push-ups and hsw",
        name_display="push-ups and hsw",
        info_raw="10 push-ups then 20 ft hsw",
        info_display="10 push-ups then 20 ft hsw",
        result_raw="done",
        result_display="done",
        state="completed",
        is_circuit=False,
        selected_exercises=[],
        linked=False,
        attachment_count=0,
        current_exercises=current_exercises or [],
        candidate_exercises=[
            ExerciseOption(
                id=1,
                name="Push-up",
                description=None,
                review_status="approved",
                match_type="exact",
                matched_text="push-ups",
            )
        ],
        abbreviations={"hsw": "handstand walk"},
    )
