"""Unit tests for clean command and cleanup module."""

import tempfile
import unittest
from pathlib import Path

from handwriting_grader.cleanup import archive_and_clean, inspect_cleanup_targets


class TestCleanup(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output = self.root / "output"
        self.output.mkdir(parents=True, exist_ok=True)
        (self.output / "grading").mkdir()
        (self.output / "grading" / "results.json").write_text("{}", encoding="utf-8")
        (self.output / "grading" / "images").mkdir()
        (self.output / "grading" / "images" / "page0.jpg").write_bytes(b"img")
        (self.output / "reports").mkdir()
        (self.output / "reports" / "summary.xlsx").write_text("fake", encoding="utf-8")
        (self.output / "ai-analysis").mkdir()
        (self.output / "ai-analysis" / "teacher-report.docx").write_text("fake", encoding="utf-8")
        self.config = {"exam": {"output_dir": "output"}}

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_inspect_cleanup_targets(self):
        info = inspect_cleanup_targets(self.config, self.root)
        self.assertEqual(len(info["items"]), 3)
        names = {item["name"] for item in info["items"]}
        self.assertIn("grading", names)
        self.assertIn("reports", names)
        self.assertIn("ai-analysis", names)

    def test_archive_and_clean_preserving_reports(self):
        result = archive_and_clean(self.config, self.root, archive=True, preserve_reports=True)
        self.assertEqual(result["status"], "cleaned")
        self.assertTrue(result["preserved_reports"])
        self.assertTrue(Path(result["archive_path"]).exists())
        self.assertIn("slim", Path(result["archive_path"]).name)
        # reports, ai-analysis, and HIL/AI metadata should still exist
        self.assertTrue((self.output / "reports").exists())
        self.assertTrue((self.output / "ai-analysis").exists())
        self.assertTrue((self.output / "grading" / "results.json").exists())
        # bulky grading/images should be deleted
        self.assertFalse((self.output / "grading" / "images").exists())

    def test_archive_and_clean_purging_all(self):
        result = archive_and_clean(self.config, self.root, archive=False, preserve_reports=False)
        self.assertEqual(result["status"], "cleaned")
        self.assertFalse((self.output / "reports").exists())
        self.assertFalse((self.output / "grading").exists())


if __name__ == "__main__":
    unittest.main()
