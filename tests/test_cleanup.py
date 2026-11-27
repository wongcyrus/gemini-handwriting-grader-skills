"""Unit tests for clean command and cleanup module."""

import io
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

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
        (self.output / "regions" / "images").mkdir(parents=True)
        (self.output / "regions" / "images" / "0.jpg").write_bytes(b"region")
        (self.output / "model-cache").mkdir()
        (self.output / "model-cache" / "cached").write_bytes(b"cache")
        (self.output / "reports").mkdir()
        (self.output / "reports" / "summary.xlsx").write_text("fake", encoding="utf-8")
        (self.output / "ai-analysis").mkdir()
        (self.output / "ai-analysis" / "teacher-report.docx").write_text("fake", encoding="utf-8")
        self.config = {"exam": {"output_dir": "output"}}

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_inspect_cleanup_targets(self):
        info = inspect_cleanup_targets(self.config, self.root)
        self.assertEqual(len(info["items"]), 5)
        names = {item["name"] for item in info["items"]}
        self.assertIn("grading", names)
        self.assertIn("reports", names)
        self.assertIn("ai-analysis", names)
        self.assertEqual(info["deletion_targets"], ["model-cache"])

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
        self.assertTrue((self.output / "grading" / "images" / "page0.jpg").exists())
        self.assertTrue((self.output / "regions" / "images" / "0.jpg").exists())
        self.assertFalse((self.output / "model-cache").exists())
        with zipfile.ZipFile(result["archive_path"]) as archive:
            self.assertIn("output/grading/images/page0.jpg", archive.namelist())
            self.assertIn("output/regions/images/0.jpg", archive.namelist())
            self.assertNotIn("output/model-cache/cached", archive.namelist())

    def test_child_cleanup_preserves_reports_and_images_unless_purged(self):
        child = self.output / "runs" / "revision-1"
        (child / "grading" / "images").mkdir(parents=True)
        (child / "grading" / "images" / "0.jpg").write_bytes(b"image")
        (child / "reports").mkdir()
        (child / "reports" / "report.pdf").write_bytes(b"report")
        (child / "model-cache").mkdir()
        (child / "model-cache" / "cached").write_bytes(b"cache")
        preview = inspect_cleanup_targets(self.config, self.root, run_id="revision-1")
        self.assertEqual(preview["deletion_targets"], ["model-cache"])
        result = archive_and_clean(self.config, self.root, run_id="revision-1")
        self.assertTrue(result["preserved_reports"])
        self.assertTrue((child / "reports" / "report.pdf").exists())
        self.assertTrue((child / "grading" / "images" / "0.jpg").exists())
        self.assertFalse((child / "model-cache").exists())
        with zipfile.ZipFile(result["archive_path"]) as archive:
            self.assertIn("revision-1/grading/images/0.jpg", archive.namelist())
        preview = inspect_cleanup_targets(self.config, self.root, run_id="revision-1",
                                          preserve_reports=False)
        self.assertEqual(preview["deletion_targets"], ["."])
        result = archive_and_clean(self.config, self.root, run_id="revision-1",
                                   preserve_reports=False)
        self.assertFalse(child.exists())
        with zipfile.ZipFile(result["archive_path"]) as archive:
            self.assertIn("revision-1/reports/report.pdf", archive.namelist())
            self.assertIn("revision-1/grading/images/0.jpg", archive.namelist())

    def test_child_purge_cli_preview_matches_execution(self):
        from handwriting_grader.cli import main

        child = self.output / "runs" / "revision-1"
        child.mkdir(parents=True)
        stdout, stderr = io.StringIO(), io.StringIO()
        args = ["clean", "--exam", str(self.root / "exam.yaml"), "--run", "revision-1",
                "--purge-reports"]
        with patch("handwriting_grader.cli.load_config", return_value=self.config), \
             patch("handwriting_grader.cli.check_exam", return_value=([], {})), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(main(args), 1)
            self.assertTrue(child.exists())
            self.assertIn("Items to delete: .", stdout.getvalue())
            self.assertIn("entire selected child run", stdout.getvalue())
            self.assertEqual(main(args + ["--confirm-delete"]), 0)
        self.assertFalse(child.exists())

    def test_archive_collision_does_not_overwrite_or_clean(self):
        from datetime import datetime, timezone
        fixed = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch("handwriting_grader.cleanup.datetime") as clock:
            clock.now.return_value = fixed
            first = archive_and_clean(self.config, self.root)
            archive = Path(first["archive_path"])
            original = archive.read_bytes()
            (self.output / "model-cache").mkdir()
            with self.assertRaises(FileExistsError):
                archive_and_clean(self.config, self.root)
        self.assertEqual(archive.read_bytes(), original)
        self.assertTrue((self.output / "model-cache").exists())

    def test_full_archive_includes_cache_and_archive_failure_prevents_deletion(self):
        with patch("handwriting_grader.cleanup.zipfile.ZipFile", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                archive_and_clean(self.config, self.root, full_archive=True)
        self.assertTrue((self.output / "model-cache" / "cached").exists())
        result = archive_and_clean(self.config, self.root, full_archive=True)
        with zipfile.ZipFile(result["archive_path"]) as archive:
            self.assertIn("output/model-cache/cached", archive.namelist())

    def test_archive_and_clean_purging_all(self):
        result = archive_and_clean(self.config, self.root, archive=False, preserve_reports=False)
        self.assertEqual(result["status"], "cleaned")
        self.assertFalse((self.output / "reports").exists())
        self.assertFalse((self.output / "grading").exists())


if __name__ == "__main__":
    unittest.main()
