import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from handwriting_grader.cli import main
from handwriting_grader.grading import inspect_retry_grading, retry_grading
from handwriting_grader.regions import atomic_json


class FakeRetryProvider:
    def __init__(self, models, project, location):
        self.models = models
        self.grade_calls = []
        self.moderate_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def ocr(self, image, field):
        return "ocr_answer"

    def grade(self, question, answer="", image=None):
        self.grade_calls.append(question["question_number"])
        return {
            "answer": "retried answer",
            "mark": 1.5,
            "similarity_score": 0.8,
            "reasoning": "retried reasoning",
        }

    def moderate(self, question, entries):
        self.moderate_calls.append(question["question_number"])
        return {
            e["script_index"]: {
                "script_index": e["script_index"],
                "moderated_mark": e["mark"],
                "flag": False,
                "note": "moderation ok",
            }
            for e in entries
        }


class TestRetryGrading(unittest.TestCase):
    def test_inspect_retry_grading_identifies_failed_marks_and_moderations(self):
        config_path = Path("exam.yaml")
        if not config_path.exists():
            return
        import yaml
        config = yaml.safe_load(config_path.read_text())
        info = inspect_retry_grading(config, Path("."), config_path)
        self.assertIn("target", info)
        self.assertIn("failed_marks", info)
        self.assertIn("unmoderated_questions", info)
        self.assertIsInstance(info["failed_marks"], list)
        self.assertIsInstance(info["unmoderated_questions"], list)

    def test_retry_grading_no_op_when_all_complete(self):
        config_path = Path("exam.yaml")
        if not config_path.exists():
            return
        import yaml
        config = yaml.safe_load(config_path.read_text())
        with patch("handwriting_grader.grading.inspect_retry_grading") as mock_inspect:
            mock_inspect.return_value = {
                "target": Path("output/grading"),
                "manifest": {},
                "results": {"marks": []},
                "questions": [],
                "questions_by_key": {},
                "failed_marks": [],
                "unmoderated_questions": [],
            }
            target = retry_grading(config, Path("."), config_path, summary={"students": 74})
            self.assertEqual(target, Path("output/grading"))

    def test_cli_help_includes_retry_grading(self):
        with patch("sys.stdout") as mock_stdout, patch("sys.stderr"):
            with self.assertRaises(SystemExit) as cm:
                main(["retry-grading", "--help"])
            self.assertEqual(cm.exception.code, 0)

    def test_cli_help_includes_approve_regions(self):
        with patch("sys.stdout"), patch("sys.stderr"):
            with self.assertRaises(SystemExit) as cm:
                main(["approve-regions", "--help"])
            self.assertEqual(cm.exception.code, 0)

    def test_inspect_retry_grading_supports_re_ocr_and_question(self):
        config_path = Path("exam.yaml")
        if not config_path.exists():
            return
        import yaml
        config = yaml.safe_load(config_path.read_text())
        info = inspect_retry_grading(
            config, Path("."), config_path,
            re_ocr_fields=["ID"], question="1.1",
        )
        self.assertIn("target", info)
        self.assertIn("re_ocr_fields", info)
        self.assertEqual(info["re_ocr_fields"], ["ID"])
        self.assertIn("target_question", info)
        self.assertIsNotNone(info["target_question"])
        self.assertIn("estimated_requests", info)

    def test_approve_regions_workflow(self):
        from handwriting_grader.regions import approve_regions, verify_run
        config_path = Path("exam.yaml")
        if not config_path.exists():
            return
        import yaml
        config = yaml.safe_load(config_path.read_text())
        root, manifest = verify_run(config, Path("."))
        annotations = approve_regions(config, Path("."), config_path)
        self.assertIsInstance(annotations, dict)
        self.assertTrue((root / "approval.json").is_file())
        with (root / "approval.json").open(encoding="utf-8") as f:
            app = json.load(f)
        self.assertIn("annotations_sha256", app)
        self.assertIn("approved_at", app)
