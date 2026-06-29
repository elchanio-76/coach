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
    _parse_model_response,
    build_category_assignment_selection_statement,
    load_ai_settings,
    run_category_assignment_dry_run,
)
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
    def test_default_selection_uses_not_exists_for_approved_categories(self) -> None:
        statement = build_category_assignment_selection_statement()
        sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

        self.assertIn("NOT (EXISTS", sql)
        self.assertIn("workout_item_categories.review_status = 'approved'", sql)
        self.assertIn("workout_item_categories.is_current IS true", sql)

    def test_explicit_ids_bypass_default_selector(self) -> None:
        statement = build_category_assignment_selection_statement(workout_item_ids=[11, 22], limit=5)
        sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

        self.assertIn("workout_items.id IN (11, 22)", sql)
        self.assertNotIn("NOT (EXISTS", sql)
        self.assertIn("LIMIT 5", sql)


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
            manifest = json.loads(summary.manifest_path.read_text(encoding="utf-8"))
            records = [
                json.loads(line)
                for line in summary.proposals_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(manifest["total_selected"], 1)
            self.assertEqual(records[0]["parse_status"], "ok")
            self.assertEqual(records[0]["proposal"]["category_name"], "Strength")
            self.assertIn("raw_response", records[0])
            self.assertIn("prompt_context", records[0])

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


class CliSmokeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
