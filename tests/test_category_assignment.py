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

from coach_truecoach.ai.category_assignment import (
    CategoryAssignmentInput,
    CategoryAssignmentProposal,
    CategoryAssignmentRunSummary,
    CategoryOption,
    archive_category_assignment_run,
    _write_category_assignment_assertion,
    _parse_model_response,
    build_category_assignment_selection_statement,
    load_ai_settings,
    run_category_assignment_dry_run,
    run_category_assignment_write,
)
from coach_truecoach.db.models import WorkoutItemCategory
from coach_truecoach.cli import main
from coach_truecoach.config import TrueCoachPaths


class AISettingsTests(unittest.TestCase):
    def test_load_ai_settings_ollama_from_env(self) -> None:
        with patch.dict(
            os.environ,
            {"AI_PROVIDER": "ollama", "MODEL": "llama3.1", "AI_URL": "http://localhost:11434"},
            clear=False,
        ):
            settings = load_ai_settings()

        self.assertEqual(settings.provider, "ollama")
        self.assertEqual(settings.model, "llama3.1")
        self.assertEqual(settings.url, "http://localhost:11434")
        self.assertIsNone(settings.openai_api_key)

    def test_load_ai_settings_openai_requires_key(self) -> None:
        with patch.dict(
            os.environ,
            {"AI_PROVIDER": "openai", "MODEL": "gpt-4.1", "OPENAI_API_KEY": "test-key"},
            clear=False,
        ):
            settings = load_ai_settings()

        self.assertEqual(settings.provider, "openai")
        self.assertEqual(settings.model, "gpt-4.1")
        self.assertEqual(settings.openai_api_key, "test-key")

    def test_cli_overrides_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AI_PROVIDER": "ollama",
                "MODEL": "llama3.1",
                "AI_URL": "http://localhost:11434",
                "OPENAI_API_KEY": "test-key",
            },
            clear=False,
        ):
            settings = load_ai_settings(provider="openai", model="gpt-4.1")

        self.assertEqual(settings.provider, "openai")
        self.assertEqual(settings.model, "gpt-4.1")

    def test_openai_without_key_raises(self) -> None:
        with patch.dict(
            os.environ,
            {"AI_PROVIDER": "openai", "MODEL": "gpt-4.1"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                load_ai_settings()


class SelectionStatementTests(unittest.TestCase):
    def test_default_selection_skips_current_pending_and_approved_categories(self) -> None:
        statement = build_category_assignment_selection_statement()
        sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

        self.assertIn("NOT (EXISTS", sql)
        self.assertIn("workout_item_categories.review_status IN ('pending', 'approved')", sql)
        self.assertIn("workout_item_categories.is_current IS true", sql)

    def test_explicit_ids_are_unioned_with_window_filter(self) -> None:
        statement = build_category_assignment_selection_statement(
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

    def test_explicit_ids_without_window_select_only_explicit_rows(self) -> None:
        statement = build_category_assignment_selection_statement(workout_item_ids=[11, 22])
        sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

        self.assertIn("workout_items.id IN (11, 22)", sql)
        self.assertNotIn("OR NOT (EXISTS", sql)


class ResponseValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.categories = [
            CategoryOption(id=1, name="Strength", description=None, color_code=None),
            CategoryOption(id=2, name="Cardio", description=None, color_code=None),
        ]

    def test_parse_model_response_accepts_valid_payload(self) -> None:
        proposal = _parse_model_response(
            json.dumps(
                {
                    "workout_item_id": 10,
                    "category_id": 1,
                    "category_name": "Strength",
                    "confidence": 0.93,
                    "rationale": "Heavy barbell work dominates the item.",
                }
            ),
            self.categories,
            expected_workout_item_id=10,
        )

        self.assertEqual(
            proposal,
            CategoryAssignmentProposal(
                workout_item_id=10,
                category_id=1,
                category_name="Strength",
                confidence=Decimal("0.930"),
                rationale="Heavy barbell work dominates the item.",
            ),
        )

    def test_parse_model_response_rejects_unknown_category(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "seeded taxonomy"):
            _parse_model_response(
                json.dumps(
                    {
                        "workout_item_id": 10,
                        "category_id": 9,
                        "category_name": "Unknown",
                        "confidence": 0.5,
                        "rationale": "Guessing.",
                    }
                ),
                self.categories,
                expected_workout_item_id=10,
            )

    def test_parse_model_response_rejects_malformed_payload(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "valid JSON"):
            _parse_model_response("not json", self.categories, expected_workout_item_id=10)


@dataclass
class _StubResult:
    categories: list[CategoryOption]
    items: list[CategoryAssignmentInput]


class _StubAssigner:
    def __init__(self, responses: dict[int, str]) -> None:
        self.responses = responses

    def assign(self, item: CategoryAssignmentInput, categories: list[CategoryOption]) -> str:
        return self.responses[item.workout_item_id]


class DryRunArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.categories = [
            CategoryOption(id=1, name="Strength", description="Resistance work", color_code="#fff"),
        ]
        self.items = [
            CategoryAssignmentInput(
                workout_item_id=7,
                tc_workout_item_id=700,
                workout_id=70,
                workout_due_date="2026-06-01",
                workout_state="completed",
                workout_title="Monday",
                workout_program_name="Cycle A",
                name_raw="Back squat",
                name_display="Back squat",
                info_raw="5x5 heavy",
                info_display="5x5 heavy",
                result_raw="140 kg",
                result_display="140 kg",
                state="completed",
                is_circuit=False,
                selected_exercises=[],
                linked=False,
                attachment_count=0,
            )
        ]

    def test_dry_run_writes_manifest_and_proposal_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = TrueCoachPaths(cache_dir=Path(tmpdir) / "cache")
            with patch("coach_truecoach.ai.category_assignment._load_categories", return_value=self.categories), patch(
                "coach_truecoach.ai.category_assignment.select_category_assignment_inputs",
                return_value=self.items,
            ), patch.dict(
                os.environ,
                {"AI_PROVIDER": "ollama", "MODEL": "llama3.1", "AI_URL": "http://localhost:11434"},
                clear=False,
            ):
                summary = run_category_assignment_dry_run(
                    session=object(),  # type: ignore[arg-type]
                    paths=paths,
                    assigner=_StubAssigner(
                        {
                            7: json.dumps(
                                {
                                    "workout_item_id": 7,
                                    "category_id": 1,
                                    "category_name": "Strength",
                                    "confidence": 0.91,
                                    "rationale": "Primary demand is heavy strength work.",
                                }
                            )
                        }
                    ),
                )

            self.assertEqual(summary.total_selected, 1)
            self.assertEqual(summary.success_count, 1)
            self.assertEqual(summary.failure_count, 0)
            self.assertEqual(summary.output_dir.parent, paths.category_assignment_active_dir)
            manifest = json.loads(summary.manifest_path.read_text(encoding="utf-8"))
            records = [
                json.loads(line)
                for line in summary.proposals_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(manifest["total_selected"], 1)
            self.assertEqual(manifest["selection_mode"], "uncategorized")
            self.assertEqual(records[0]["parse_status"], "ok")
            self.assertEqual(records[0]["proposal"]["category_name"], "Strength")
            self.assertIn("raw_response", records[0])
            self.assertIn("prompt_context", records[0])

    def test_dry_run_manifest_records_window_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = TrueCoachPaths(cache_dir=Path(tmpdir) / "cache")
            with patch("coach_truecoach.ai.category_assignment._load_categories", return_value=self.categories), patch(
                "coach_truecoach.ai.category_assignment.select_category_assignment_inputs",
                return_value=self.items,
            ), patch.dict(
                os.environ,
                {"AI_PROVIDER": "ollama", "MODEL": "llama3.1", "AI_URL": "http://localhost:11434"},
                clear=False,
            ):
                summary = run_category_assignment_dry_run(
                    session=object(),  # type: ignore[arg-type]
                    paths=paths,
                    min_workout_item_id=5,
                    max_workout_item_id=25,
                    assigner=_StubAssigner(
                        {
                            7: json.dumps(
                                {
                                    "workout_item_id": 7,
                                    "category_id": 1,
                                    "category_name": "Strength",
                                    "confidence": 0.91,
                                    "rationale": "Primary demand is heavy strength work.",
                                }
                            )
                        }
                    ),
                )

            manifest = json.loads(summary.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["selection_mode"], "window")
            self.assertEqual(manifest["filters"]["min_workout_item_id"], 5)
            self.assertEqual(manifest["filters"]["max_workout_item_id"], 25)

    def test_dry_run_keeps_raw_response_on_parse_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = TrueCoachPaths(cache_dir=Path(tmpdir) / "cache")
            with patch("coach_truecoach.ai.category_assignment._load_categories", return_value=self.categories), patch(
                "coach_truecoach.ai.category_assignment.select_category_assignment_inputs",
                return_value=self.items,
            ), patch.dict(
                os.environ,
                {"AI_PROVIDER": "ollama", "MODEL": "llama3.1", "AI_URL": "http://localhost:11434"},
                clear=False,
            ):
                summary = run_category_assignment_dry_run(
                    session=object(),  # type: ignore[arg-type]
                    paths=paths,
                    assigner=_StubAssigner({7: "not json"}),
                )

            records = [
                json.loads(line)
                for line in summary.proposals_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(summary.success_count, 0)
            self.assertEqual(summary.failure_count, 1)
            self.assertEqual(records[0]["parse_status"], "error")
            self.assertEqual(records[0]["raw_response"], "not json")
            self.assertIn("error", records[0])


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_count = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flush_count += 1
        for index, obj in enumerate(self.added, start=1):
            if getattr(obj, "id", None) is None:
                obj.id = 1000 + index


class WriteAssertionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = load_ai_settings(
            provider="ollama",
            model="llama3.1",
            url="http://localhost:11434",
        )
        self.proposal = CategoryAssignmentProposal(
            workout_item_id=7,
            category_id=1,
            category_name="Strength",
            confidence=Decimal("0.910"),
            rationale="Primary demand is heavy strength work.",
        )

    def test_write_assertion_inserts_pending_ai_row(self) -> None:
        session = _FakeSession()
        with patch("coach_truecoach.ai.category_assignment._load_current_pending_ai_categories", return_value=[]):
            status = _write_category_assignment_assertion(session, self.proposal, self.settings)

        self.assertEqual(status, "inserted")
        self.assertEqual(session.flush_count, 1)
        self.assertEqual(len(session.added), 1)
        row = session.added[0]
        self.assertIsInstance(row, WorkoutItemCategory)
        self.assertEqual(row.workout_item_id, 7)
        self.assertEqual(row.category_id, 1)
        self.assertEqual(row.source, "ai")
        self.assertEqual(row.review_status, "pending")
        self.assertTrue(row.is_current)
        self.assertEqual(row.model_name, "llama3.1")
        self.assertEqual(row.model_version, "ollama")

    def test_write_assertion_skips_identical_current_pending_row(self) -> None:
        existing = WorkoutItemCategory(
            id=10,
            workout_item_id=7,
            category_id=1,
            source="ai",
            confidence=Decimal("0.910"),
            review_status="pending",
            is_current=True,
            model_name="llama3.1",
            model_version="ollama",
            rationale="Primary demand is heavy strength work.",
        )
        session = _FakeSession()
        with patch("coach_truecoach.ai.category_assignment._load_current_pending_ai_categories", return_value=[existing]):
            status = _write_category_assignment_assertion(session, self.proposal, self.settings)

        self.assertEqual(status, "unchanged")
        self.assertEqual(session.flush_count, 0)
        self.assertEqual(session.added, [])
        self.assertEqual(existing.review_status, "pending")
        self.assertTrue(existing.is_current)

    def test_write_assertion_supersedes_old_pending_rows(self) -> None:
        existing = WorkoutItemCategory(
            id=10,
            workout_item_id=7,
            category_id=2,
            source="ai",
            confidence=Decimal("0.700"),
            review_status="pending",
            is_current=True,
            model_name="llama3.1",
            model_version="ollama",
            rationale="Previous guess.",
        )
        session = _FakeSession()
        with patch("coach_truecoach.ai.category_assignment._load_current_pending_ai_categories", return_value=[existing]):
            status = _write_category_assignment_assertion(session, self.proposal, self.settings)

        self.assertEqual(status, "inserted")
        self.assertEqual(session.flush_count, 1)
        self.assertEqual(existing.review_status, "superseded")
        self.assertFalse(existing.is_current)
        self.assertEqual(existing.superseded_by_id, session.added[0].id)


class WriteRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.categories = [
            CategoryOption(id=1, name="Strength", description="Resistance work", color_code="#fff"),
        ]
        self.items = [
            CategoryAssignmentInput(
                workout_item_id=7,
                tc_workout_item_id=700,
                workout_id=70,
                workout_due_date="2026-06-01",
                workout_state="completed",
                workout_title="Monday",
                workout_program_name="Cycle A",
                name_raw="Back squat",
                name_display="Back squat",
                info_raw="5x5 heavy",
                info_display="5x5 heavy",
                result_raw="140 kg",
                result_display="140 kg",
                state="completed",
                is_circuit=False,
                selected_exercises=[],
                linked=False,
                attachment_count=0,
            )
        ]

    def test_write_run_records_db_write_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = TrueCoachPaths(cache_dir=Path(tmpdir) / "cache")
            with patch("coach_truecoach.ai.category_assignment._load_categories", return_value=self.categories), patch(
                "coach_truecoach.ai.category_assignment.select_category_assignment_inputs",
                return_value=self.items,
            ), patch(
                "coach_truecoach.ai.category_assignment._write_category_assignment_assertion",
                return_value="inserted",
            ), patch.dict(
                os.environ,
                {"AI_PROVIDER": "ollama", "MODEL": "llama3.1", "AI_URL": "http://localhost:11434"},
                clear=False,
            ):
                summary = run_category_assignment_write(
                    session=object(),  # type: ignore[arg-type]
                    paths=paths,
                    assigner=_StubAssigner(
                        {
                            7: json.dumps(
                                {
                                    "workout_item_id": 7,
                                    "category_id": 1,
                                    "category_name": "Strength",
                                    "confidence": 0.91,
                                    "rationale": "Primary demand is heavy strength work.",
                                }
                            )
                        }
                    ),
                )

            self.assertEqual(summary.inserted_count, 1)
            self.assertEqual(summary.unchanged_count, 0)
            manifest = json.loads(summary.manifest_path.read_text(encoding="utf-8"))
            records = [
                json.loads(line)
                for line in summary.proposals_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(manifest["mode"], "write")
            self.assertEqual(manifest["inserted_count"], 1)
            self.assertEqual(records[0]["db_write_status"], "inserted")
            self.assertEqual(summary.output_dir.parent, paths.category_assignment_active_dir)


class ArchiveRunTests(unittest.TestCase):
    def test_archive_run_moves_directory_to_archived_area(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = TrueCoachPaths(cache_dir=Path(tmpdir) / "cache")
            paths.ensure()
            run_dir = paths.category_assignment_active_dir / "20260704T000000Z"
            run_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = run_dir / "manifest.json"
            manifest_path.write_text("{}", encoding="utf-8")

            archived_dir = archive_category_assignment_run(paths=paths, run_dir=run_dir)

            self.assertFalse(run_dir.exists())
            self.assertEqual(archived_dir.parent, paths.category_assignment_archived_dir)
            self.assertTrue((archived_dir / "manifest.json").exists())


class CliSmokeTests(unittest.TestCase):
    def test_cli_dry_run_passes_window_options(self) -> None:
        summary = CategoryAssignmentRunSummary(
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
            "coach_truecoach.cli.run_category_assignment_dry_run",
            return_value=summary,
        ) as run_mock, patch(
            "sys.argv",
            [
                "coach",
                "ai-category-assignment-dry-run",
                "--min-workout-item-id",
                "10",
                "--max-workout-item-id",
                "20",
            ],
        ), patch("sys.stdout", stdout):
            main()

        self.assertEqual(run_mock.call_args.kwargs["min_workout_item_id"], 10)
        self.assertEqual(run_mock.call_args.kwargs["max_workout_item_id"], 20)

    def test_cli_dry_run_prints_summary(self) -> None:
        summary = CategoryAssignmentRunSummary(
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
            "coach_truecoach.cli.run_category_assignment_dry_run",
            return_value=summary,
        ), patch(
            "sys.argv",
            ["coach", "ai-category-assignment-dry-run", "--limit", "2"],
        ), patch("sys.stdout", stdout):
            main()

        output = stdout.getvalue()
        self.assertIn("Selected workout items: 2", output)
        self.assertIn("Successful proposals: 1", output)
        self.assertIn("Failed proposals: 1", output)

    def test_cli_write_prints_summary(self) -> None:
        summary = CategoryAssignmentRunSummary(
            output_dir=Path("/tmp/output"),
            total_selected=2,
            success_count=2,
            failure_count=0,
            inserted_count=2,
            unchanged_count=0,
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
            "coach_truecoach.cli.run_category_assignment_write",
            return_value=summary,
        ), patch(
            "sys.argv",
            ["coach", "ai-category-assignment-write", "--limit", "2"],
        ), patch("sys.stdout", stdout):
            main()

        output = stdout.getvalue()
        self.assertIn("Selected workout items: 2", output)
        self.assertIn("Inserted assertions: 2", output)
        self.assertIn("Unchanged assertions: 0", output)

    def test_cli_archive_run_prints_destination(self) -> None:
        stdout = io.StringIO()
        archived_dir = Path("/tmp/output/archived/run-1")
        with patch(
            "coach_truecoach.cli.archive_category_assignment_run",
            return_value=archived_dir,
        ), patch(
            "sys.argv",
            ["coach", "ai-category-assignment-archive-run", "--run-dir", "/tmp/output/active/run-1"],
        ), patch("sys.stdout", stdout):
            main()

        output = stdout.getvalue()
        self.assertIn("Archived run: /tmp/output/archived/run-1", output)


if __name__ == "__main__":
    unittest.main()
