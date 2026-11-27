"""Offline workspace registration, documentation links and teacher CLI contracts."""

import argparse
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

import yaml

from handwriting_grader.cli import main


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "handwriting-grading"


class WorkspaceSkillTests(unittest.TestCase):
    def test_registry_resolves_teacher_skill_and_frontmatter(self):
        registry = json.loads((ROOT / ".agents" / "skills.json").read_text(encoding="utf-8"))
        registered = set()
        for entry in registry["entries"]:
            with self.subTest(entry=entry):
                location = (ROOT / entry["path"]).resolve()
                self.assertTrue(location.is_relative_to(ROOT))
                self.assertTrue(location.is_dir())
                files = list(location.rglob("SKILL.md"))
                self.assertTrue(files, "Registered path must contain a skill")
                registered.update(files)

        self.assertIn(SKILL / "SKILL.md", registered)
        names = set()
        for file in registered:
            with self.subTest(skill=file.relative_to(ROOT)):
                text = file.read_text(encoding="utf-8")
                frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
                self.assertIsNotNone(frontmatter)
                metadata = yaml.safe_load(frontmatter.group(1))
                self.assertEqual(metadata["name"], file.parent.name)
                self.assertIsInstance(metadata["description"], str)
                self.assertTrue(metadata["description"].strip())
                self.assertNotIn(metadata["name"], names)
                names.add(metadata["name"])

    def test_teacher_skill_relative_links_resolve_inside_checkout(self):
        files = [SKILL / "SKILL.md", *sorted((SKILL / "references").glob("*.md"))]
        checked = 0
        for file in files:
            text = file.read_text(encoding="utf-8")
            for target in re.findall(r"(?<!!)\[[^\]\n]+\]\(([^)\s]+)\)", text):
                url = urlsplit(target)
                if url.scheme in {"http", "https", "mailto"}:
                    continue
                with self.subTest(file=file.relative_to(ROOT), link=target):
                    self.assertFalse(url.scheme, "Local documentation links must be relative")
                    self.assertFalse(url.netloc)
                    self.assertFalse(Path(unquote(url.path)).is_absolute())
                    linked = (file.parent / unquote(url.path)).resolve()
                    self.assertTrue(linked.is_relative_to(ROOT))
                    self.assertTrue(linked.exists(), "Do not link absent output/config templates")
                    checked += 1
        self.assertGreater(checked, 0)


class _ParserCaptured(Exception):
    pass


class TeacherCLIContractTests(unittest.TestCase):
    """Capture the real parser before dispatch; no files, servers or model calls."""

    @classmethod
    def setUpClass(cls):
        def capture(parser, *args, **kwargs):
            cls.parser = parser
            raise _ParserCaptured

        with patch.object(argparse.ArgumentParser, "parse_args", capture):
            try:
                main([])
            except _ParserCaptured:
                pass
            else:
                raise AssertionError("CLI parser was not captured before execution")

    def parse(self, command, *flags):
        return self.parser.parse_args([command, *flags])

    def test_discovery_is_metadata_only_unless_probe_and_cost_are_selected(self):
        args = self.parse("discover-models", "--project", "synthetic-project")
        self.assertFalse(args.probe)
        self.assertFalse(args.confirm_cost)
        paid = self.parse(
            "discover-models", "--project", "synthetic-project", "--probe", "--confirm-cost"
        )
        self.assertTrue(paid.probe)
        self.assertTrue(paid.confirm_cost)

    def test_incomplete_recovery_accepts_preview_checkpoint_hash(self):
        preview = self.parse("retry-failed-batches", "--exam", "synthetic-exam.yaml")
        self.assertFalse(preview.confirm_cost)
        self.assertIsNone(preview.checkpoint_sha256)
        paid = self.parse(
            "retry-failed-batches", "--exam", "synthetic-exam.yaml",
            "--checkpoint-sha256", "a" * 64, "--confirm-cost",
        )
        self.assertEqual(paid.checkpoint_sha256, "a" * 64)
        self.assertTrue(paid.confirm_cost)

    def test_failed_review_export_has_separate_explicit_output(self):
        args = self.parse(
            "export-failed-review", "--exam", "synthetic-exam.yaml",
            "--output", "fresh-manual-review",
        )
        self.assertEqual(Path(args.output), Path("fresh-manual-review"))
        with patch("sys.stderr"):
            with self.assertRaises(SystemExit) as error:
                self.parse("export-failed-review", "--exam", "synthetic-exam.yaml")
        self.assertEqual(error.exception.code, 2)

    def test_completed_batch_bisection_is_bounded_and_revision_bound(self):
        flags = ("--exam", "synthetic-exam.yaml", "--question", "Q1", "--max-requests", "39")
        preview = self.parse("bisect-failed-batch", *flags)
        self.assertFalse(preview.confirm_cost)
        self.assertEqual(preview.question, "Q1")
        self.assertEqual(preview.max_requests, 39)
        paid = self.parse(
            "bisect-failed-batch", *flags, "--revision", "synthetic-revision", "--confirm-cost"
        )
        self.assertEqual(paid.revision, "synthetic-revision")
        self.assertTrue(paid.confirm_cost)

    def test_clustering_defaults_offline_and_supports_paid_preview_revision(self):
        offline = self.parse("cluster-answers", "--exam", "synthetic-exam.yaml")
        self.assertFalse(offline.preview_embedding)
        self.assertFalse(offline.confirm_cost)
        self.assertIsNone(offline.model_config)
        preview = self.parse(
            "cluster-answers", "--exam", "synthetic-exam.yaml",
            "--preview-embedding", "--model-config", "synthetic-config.yaml",
        )
        self.assertTrue(preview.preview_embedding)
        self.assertFalse(preview.confirm_cost)
        self.assertEqual(Path(preview.model_config), Path("synthetic-config.yaml"))
        paid = self.parse(
            "cluster-answers", "--exam", "synthetic-exam.yaml",
            "--revision", "synthetic-revision", "--confirm-cost",
        )
        self.assertEqual(paid.revision, "synthetic-revision")
        self.assertTrue(paid.confirm_cost)

    def test_review_does_not_require_a_paid_profile(self):
        offline = self.parse("review-grading", "--exam", "synthetic-exam.yaml")
        self.assertFalse(offline.confirm_cost)
        self.assertIsNone(offline.model_config)
        paid = self.parse(
            "review-grading", "--exam", "synthetic-exam.yaml",
            "--model-config", "synthetic-config.yaml", "--confirm-cost",
        )
        self.assertTrue(paid.confirm_cost)
        self.assertEqual(Path(paid.model_config), Path("synthetic-config.yaml"))

    def test_advanced_reports_keep_offline_default_and_explicit_image_profile(self):
        offline = self.parse("advanced-report", "--exam", "synthetic-exam.yaml")
        self.assertFalse(offline.infographics)
        self.assertFalse(offline.confirm_cost)
        compatible = self.parse(
            "advanced-report", "--exam", "synthetic-exam.yaml", "--no-infographics"
        )
        self.assertTrue(compatible.no_infographics)
        self.assertFalse(compatible.infographics)
        preview = self.parse(
            "advanced-report", "--exam", "synthetic-exam.yaml", "--infographics",
            "--image-model", "synthetic-image-model",
            "--image-model-config", "synthetic-image.yaml",
            "--report-name", "reports-infographics",
        )
        self.assertTrue(preview.infographics)
        self.assertFalse(preview.confirm_cost)
        self.assertEqual(Path(preview.image_model_config), Path("synthetic-image.yaml"))
        self.assertEqual(preview.report_name, "reports-infographics")
        paid = self.parse(
            "advanced-report", "--exam", "synthetic-exam.yaml",
            "--infographics", "--confirm-cost",
        )
        self.assertTrue(paid.confirm_cost)


if __name__ == "__main__":
    unittest.main()
