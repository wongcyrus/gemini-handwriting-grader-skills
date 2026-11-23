"""Unit tests for ADK telemetry, progress reporting, and cost calculation."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from handwriting_grader.cli import main

from handwriting_grader.telemetry import (
    DEFAULT_RATES,
    CallTrace,
    TelemetryTracker,
    get_active_tracker,
    get_thinking_config,
    set_active_tracker,
)


class TestTelemetryTracker(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.temp_dir.name)
        self.tracker = TelemetryTracker(log_dir=self.log_dir)

    def tearDown(self):
        set_active_tracker(None)
        self.temp_dir.cleanup()

    def test_calculate_cost_flash(self):
        # gemini-3.8-flash: input=0.075, output=0.30, cached=0.01875 per 1M
        # 1,000,000 prompt tokens (non-cached) -> $0.075
        # 1,000,000 candidate tokens -> $0.30
        cost = self.tracker.calculate_cost(
            model="gemini-3.8-flash",
            prompt_tokens=1_000_000,
            candidate_tokens=1_000_000,
            cached_tokens=0,
        )
        self.assertAlmostEqual(cost, 0.375, places=5)

    def test_retry_cli_uses_manifest_pricing_and_selected_child_telemetry(self):
        model_config = self.log_dir / "models.yaml"
        model_config.write_text("pricing:\n  gemini-3.8-flash:\n    input: 2\n    output: 8\n")
        target = self.log_dir / "output" / "runs" / "child" / "grading"
        info = {
            "target": target, "failed_marks": [{}], "unmoderated_questions": [],
            "regions_updated": False, "target_question": None, "estimated_requests": 1,
            "manifest": {"project": "test-project", "location": "global",
                         "models": {"grading": "gemini-3.8-flash"}, "model_config_path": str(model_config)},
        }

        def retry(*args, **kwargs):
            get_active_tracker().record_call(
                "grading", "Q1", "gemini-3.8-flash",
                SimpleNamespace(prompt_token_count=1000, candidates_token_count=1000), 1,
            )
            return target

        with patch("handwriting_grader.cli.load_config", return_value={"exam": {"output_dir": "output"}}), \
                patch("handwriting_grader.cli.check_exam", return_value=([], {})), \
                patch("handwriting_grader.grading.inspect_retry_grading", return_value=info), \
                patch("handwriting_grader.grading.retry_grading", side_effect=retry):
            self.assertEqual(main([
                "retry-grading", "--exam", str(self.log_dir / "exam.yaml"), "--run", "child", "--confirm-cost",
            ]), 0)
        trace = json.loads((target / "telemetry" / "gemini_usage.jsonl").read_text())
        self.assertEqual(trace["cost_usd"], 0.01)
        self.assertEqual(json.loads((target / "cost_summary.json").read_text())["total_cost_usd"], 0.01)
        self.assertFalse((self.log_dir / "output" / "grading" / "telemetry").exists())

    def test_infographic_cli_uses_pricing_from_analysis_manifest(self):
        model_config = self.log_dir / "models.yaml"
        model_config.write_text("pricing:\n  gemini-3-pro-image:\n    input: 2\n    output: 8\n")
        analysis = self.log_dir / "ai-analysis"
        analysis.mkdir()
        (analysis / "manifest.json").write_text(json.dumps({"model_config_path": str(model_config)}))

        def report(*args, **kwargs):
            get_active_tracker().record_call(
                "infographics", "Class", "gemini-3-pro-image",
                SimpleNamespace(prompt_token_count=1000, candidates_token_count=1000), 1,
            )
            return analysis / "reports"

        with patch("handwriting_grader.cli.load_config", return_value={"exam": {"output_dir": "output"}}), \
                patch("handwriting_grader.cli.check_exam", return_value=([], {})), \
                patch("handwriting_grader.run_context.run_directory", return_value=self.log_dir), \
                patch("handwriting_grader.advanced.create_advanced_report", side_effect=report):
            self.assertEqual(main(["advanced-report", "--exam", str(self.log_dir / "exam.yaml")]), 0)
        trace = json.loads((analysis / "telemetry" / "gemini_usage.jsonl").read_text())
        self.assertEqual(trace["cost_usd"], 0.01)
        self.assertEqual(json.loads((analysis / "cost_summary.json").read_text())["total_cost_usd"], 0.01)

    def test_calculate_cost_cached_tokens(self):
        # 500,000 cached, 500,000 regular input
        # cached: 0.5 * 0.01875 = 0.009375
        # non-cached: 0.5 * 0.075 = 0.0375
        # output: 100,000 -> 0.1 * 0.30 = 0.030
        cost = self.tracker.calculate_cost(
            model="gemini-3.8-flash",
            prompt_tokens=1_000_000,
            candidate_tokens=100_000,
            cached_tokens=500_000,
        )
        expected = 0.009375 + 0.0375 + 0.030
        self.assertAlmostEqual(cost, round(expected, 6), places=5)

    def test_calculate_cost_tier_fallback(self):
        cost = self.tracker.calculate_cost(
            model="gemini-4-hydrogen",
            prompt_tokens=1_000_000,
            candidate_tokens=100_000,
            cached_tokens=0,
        )
        self.assertAlmostEqual(cost, 1.25 + 0.50, places=4)

    def test_record_call_and_summary(self):
        usage = MagicMock()
        usage.prompt_token_count = 1000
        usage.candidates_token_count = 200
        usage.cached_content_token_count = 100
        usage.total_token_count = 1200
        usage.thoughts_token_count = 50

        trace = self.tracker.record_call(
            phase="grading",
            step_label="Q1 Script #1",
            model="gemini-3.8-flash",
            usage_metadata=usage,
            duration_sec=1.5,
        )
        self.assertEqual(len(self.tracker.traces), 1)
        self.assertEqual(trace.phase, "grading")
        self.assertEqual(trace.prompt_tokens, 1000)
        self.assertEqual(trace.candidate_tokens, 200)
        self.assertEqual(trace.cached_tokens, 100)
        self.assertGreater(trace.cost_usd, 0.0)

        # Check log file written
        jsonl = self.log_dir / "gemini_usage.jsonl"
        self.assertTrue(jsonl.exists())

        summary = self.tracker.get_summary()
        self.assertEqual(summary["total_calls"], 1)
        self.assertEqual(summary["total_input_tokens"], 1000)
        self.assertEqual(summary["total_output_tokens"], 200)
        self.assertEqual(summary["total_cached_tokens"], 100)
        self.assertIn("grading", summary["by_phase"])
        self.assertIn("gemini-3.8-flash", summary["by_model"])

    def test_write_cost_report_excel_and_json(self):
        usage = MagicMock()
        usage.prompt_token_count = 500
        usage.candidates_token_count = 150
        usage.cached_content_token_count = 0
        usage.total_token_count = 650
        usage.thoughts_token_count = 0

        self.tracker.record_call(
            phase="ocr",
            step_label="Script #1 ID",
            model="gemini-3.8-flash",
            usage_metadata=usage,
            duration_sec=0.8,
        )
        xlsx_path = self.tracker.write_cost_report(self.log_dir)
        self.assertTrue(xlsx_path.exists())
        self.assertEqual(xlsx_path.name, "cost_report.xlsx")

        summary_json = self.log_dir / "cost_summary.json"
        self.assertTrue(summary_json.exists())
        with summary_json.open(encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["total_calls"], 1)

    def test_show_progress_formatting(self):
        output = self.tracker.show_progress(
            phase="grading",
            current=2,
            total=10,
            item_label="Question 2 Script 1",
            duration_sec=1.2,
        )
        self.assertIn("[ADK Progress]", output)
        self.assertIn("20.0%", output)
        self.assertIn("Question 2 Script 1", output)

    def test_active_tracker_context(self):
        self.assertIsNone(get_active_tracker())
        set_active_tracker(self.tracker)
        self.assertIs(get_active_tracker(), self.tracker)
        set_active_tracker(None)
        self.assertIsNone(get_active_tracker())

    def test_get_thinking_config_flash_and_reasoning(self):
        # Gemini 3.8 Flash on reasoning task -> dynamic thinking (-1)
        cfg_38_reasoning = get_thinking_config("gemini-3.8-flash", is_reasoning=True)
        self.assertIsNotNone(cfg_38_reasoning)
        self.assertEqual(cfg_38_reasoning.thinking_budget, -1)

        # Gemini 3.8 Flash on perception/OCR task -> thinking budget 0
        cfg_38_ocr = get_thinking_config("gemini-3.8-flash", is_reasoning=False)
        self.assertIsNotNone(cfg_38_ocr)
        self.assertEqual(cfg_38_ocr.thinking_budget, 0)

        # Gemini 4 Argon on reasoning task -> thinking_level MEDIUM
        from google.genai import types
        cfg_4_reasoning = get_thinking_config("gemini-4-argon", is_reasoning=True)
        self.assertIsNotNone(cfg_4_reasoning)
        self.assertEqual(cfg_4_reasoning.thinking_level, types.ThinkingLevel.MEDIUM)
        self.assertIsNone(cfg_4_reasoning.thinking_budget)

        # Gemini 4 Argon on perception/OCR task -> thinking_level LOW
        cfg_4_ocr = get_thinking_config("gemini-4-argon", is_reasoning=False)
        self.assertIsNotNone(cfg_4_ocr)
        self.assertEqual(cfg_4_ocr.thinking_level, types.ThinkingLevel.LOW)
        self.assertIsNone(cfg_4_ocr.thinking_budget)

        # Legacy Gemini 1.5 -> None (no thinking_config)
        cfg_15 = get_thinking_config("gemini-1.5-flash", is_reasoning=True)
        self.assertIsNone(cfg_15)

        # Empty / None model -> None
        self.assertIsNone(get_thinking_config("", is_reasoning=True))


if __name__ == "__main__":
    unittest.main()
