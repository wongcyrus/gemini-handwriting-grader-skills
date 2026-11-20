"""Unit tests for ADK telemetry, progress reporting, and cost calculation."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from handwriting_grader.telemetry import (
    DEFAULT_RATES,
    CallTrace,
    TelemetryTracker,
    get_active_tracker,
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

    def test_calculate_cost_images(self):
        cost = self.tracker.calculate_cost(
            model="gemini-3-pro-image",
            prompt_tokens=100,
            candidate_tokens=10,
            cached_tokens=0,
            images_count=2,
        )
        self.assertAlmostEqual(cost, 0.06, places=4)

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


if __name__ == "__main__":
    unittest.main()
