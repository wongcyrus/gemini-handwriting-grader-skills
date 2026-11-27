"""Unit tests for multimodal batch grading and structured output mode."""

import io
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from PIL import Image, ImageDraw

from handwriting_grader.grading import (
    GeminiSafetyBlocked,
    MAX_IMAGES_PER_REQUEST,
    MetadataBatchResponse,
    QuestionBatchGradeResponse,
    StudentBatchGradeItem,
    continue_grading,
)
from handwriting_grader.regions import atomic_json


class FakeBatchProvider:
    def __init__(self, models, project, location):
        self.batch_calls = []
        self.item_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def ocr(self, image, field):
        return f"VAL_{field}"

    def grade_question_batch(self, question, script_items):
        self.batch_calls.append((question["question_number"], len(script_items)))
        return {
            item["script_index"]: {
                "script_index": item["script_index"],
                "answer": f"Answer for script {item['script_index']}",
                "mark": float(question["marks"]),
                "similarity_score": 0.95,
                "reasoning": f"Good answer for script {item['script_index']}",
                "moderator_flag": item["script_index"] == 1,
                "moderator_note": "Review script 1" if item["script_index"] == 1 else "",
            }
            for item in script_items
        }

    def grade(self, question, answer="", image=None):
        self.item_calls.append(question["question_number"])
        return {"answer": "Item answer", "mark": 1.0, "similarity_score": 0.5, "reasoning": "Item reasoning"}

    def moderate(self, question, entries):
        return {
            e["script_index"]: {
                "script_index": e["script_index"],
                "moderated_mark": e["mark"],
                "flag": False,
                "note": "",
            }
            for e in entries
        }


class FakeItemOnlyProvider:
    def __init__(self, models, project, location):
        self.item_calls = []
        self.mod_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def ocr(self, image, field):
        return f"VAL_{field}"

    def grade(self, question, answer="", image=None):
        self.item_calls.append(question["question_number"])
        return {"answer": "Item answer", "mark": 1.0, "similarity_score": 0.5, "reasoning": "Item reasoning"}

    def moderate(self, question, entries):
        self.mod_calls.append(question["question_number"])
        return {
            e["script_index"]: {
                "script_index": e["script_index"],
                "moderated_mark": e["mark"],
                "flag": False,
                "note": "",
            }
            for e in entries
        }


class TestBatchGrading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.images_dir = self.root / "images"
        self.images_dir.mkdir(parents=True)

        # Create 2 scripts (1 page each)
        for i in range(2):
            img = Image.new("RGB", (300, 300), "white")
            draw = ImageDraw.Draw(img)
            # Add handwriting inside Q1.1 (y: 50..100) and Q1.2 (y: 110..160)
            draw.line([(25, 75), (85, 75)], fill="black", width=5)
            draw.line([(25, 135), (85, 135)], fill="black", width=5)
            img.save(self.images_dir / f"{i}.jpg", "JPEG")

        self.questions = [
            {"question_number": "1.1", "question_text": "Q1", "marking_scheme": "Criteria 1", "marks": 2},
            {"question_number": "1.2", "question_text": "Q2", "marking_scheme": "Criteria 2", "marks": 3},
        ]
        self.boxes = {
            "ID": (0, {"x": 10, "y": 10, "width": 50, "height": 30, "label": "ID"}),
            "CLASS": (0, {"x": 70, "y": 10, "width": 50, "height": 30, "label": "CLASS"}),
            "NAME": (0, {"x": 130, "y": 10, "width": 50, "height": 30, "label": "NAME"}),
            "1.1": (0, {"x": 10, "y": 50, "width": 100, "height": 50, "label": "1.1"}),
            "1.2": (0, {"x": 10, "y": 110, "width": 100, "height": 50, "label": "1.2"}),
        }
        self.summary = {"students": 2}
        self.stride = 1
        self.models = {"ocr": "flash", "grading": "flash", "moderation": "argon", "batch_grading": "argon"}
        self.metadata_fields = ["ID", "CLASS", "NAME"]

    def test_batch_mode_grades_all_questions_and_sets_fields(self):
        results = {"version": 1, "checkpoint_version": 1, "completed": False, "scripts": [], "marks": [], "moderated_questions": []}
        atomic_json(self.root / "results.json", results)

        provider_inst = None
        def provider_factory(models, project, location):
            nonlocal provider_inst
            provider_inst = FakeBatchProvider(models, project, location)
            return provider_inst

        continue_grading(
            self.root, results, self.questions, self.boxes, self.summary, self.stride,
            self.models, "project", "location", provider_factory,
            [], self.root / "cache", self.metadata_fields, lambda: None,
            mode="batch", batch_concurrency=2,
        )

        saved = json.loads((self.root / "results.json").read_text())
        self.assertEqual(len(saved["scripts"]), 2)
        self.assertEqual(len(saved["marks"]), 4)  # 2 questions * 2 scripts
        self.assertEqual(len(saved["moderated_questions"]), 2)

        # Batch provider was called once per question (2 questions = 2 batch calls)
        self.assertEqual(len(provider_inst.batch_calls), 2)
        self.assertEqual(len(provider_inst.item_calls), 0)

        # Verify mark contents
        m0 = next(m for m in saved["marks"] if m["question"] == "1.1" and m["script_index"] == 0)
        self.assertEqual(m0["raw_mark"], 2.0)
        self.assertEqual(m0["moderated_mark"], 2.0)
        self.assertFalse(m0["moderator_flag"])
        self.assertFalse(m0["grading_failed"])

        m1 = next(m for m in saved["marks"] if m["question"] == "1.1" and m["script_index"] == 1)
        self.assertTrue(m1["moderator_flag"])
        self.assertEqual(m1["moderator_note"], "Review script 1")

    def test_batch_mode_handles_blank_cell_locally_without_ai(self):
        # Make script 1's question 1.1 box pure white
        white_img = Image.new("RGB", (300, 300), "white")
        white_img.save(self.images_dir / "1.jpg", "JPEG")

        results = {"version": 1, "checkpoint_version": 1, "completed": False, "scripts": [], "marks": [], "moderated_questions": []}
        atomic_json(self.root / "results.json", results)

        provider_inst = None
        def provider_factory(models, project, location):
            nonlocal provider_inst
            provider_inst = FakeBatchProvider(models, project, location)
            return provider_inst

        continue_grading(
            self.root, results, self.questions, self.boxes, self.summary, self.stride,
            self.models, "project", "location", provider_factory,
            [], self.root / "cache", self.metadata_fields, lambda: None,
            mode="batch", batch_concurrency=1,
        )

        saved = json.loads((self.root / "results.json").read_text())
        m1_blank = next(m for m in saved["marks"] if m["question"] == "1.1" and m["script_index"] == 1)
        self.assertEqual(m1_blank["raw_mark"], 0)
        self.assertEqual(m1_blank["moderated_mark"], 0)
        self.assertEqual(m1_blank["reasoning"], "Blank answer (local filter).")
        self.assertFalse(m1_blank["moderator_flag"])

        # Script 0 was non-blank, so batch_calls for Q1.1 had length 1 (script 1 omitted from AI call!)
        q1_batch = next(call for call in provider_inst.batch_calls if call[0] == "1.1")
        self.assertEqual(q1_batch[1], 1)

    def test_fallback_to_item_when_provider_lacks_batch_method(self):
        results = {"version": 1, "checkpoint_version": 1, "completed": False, "scripts": [], "marks": [], "moderated_questions": []}
        atomic_json(self.root / "results.json", results)

        provider_inst = None
        def provider_factory(models, project, location):
            nonlocal provider_inst
            provider_inst = FakeItemOnlyProvider(models, project, location)
            return provider_inst

        continue_grading(
            self.root, results, self.questions, self.boxes, self.summary, self.stride,
            self.models, "project", "location", provider_factory,
            [], self.root / "cache", self.metadata_fields, lambda: None,
            mode="batch", batch_concurrency=1,
        )

        saved = json.loads((self.root / "results.json").read_text())
        self.assertEqual(len(saved["marks"]), 4)
        # item provider was called for each script and question
        self.assertEqual(len(provider_inst.item_calls), 4)
        self.assertEqual(len(provider_inst.mod_calls), 2)

    def test_structured_output_schema_validation(self):
        valid_data = {
            "grades": [
                {
                    "script_index": 0,
                    "answer": "Answer 0",
                    "mark": 2.5,
                    "similarity_score": 0.8,
                    "reasoning": "Good",
                    "moderator_flag": False,
                    "moderator_note": "",
                }
            ]
        }
        resp = QuestionBatchGradeResponse.model_validate(valid_data)
        self.assertEqual(len(resp.grades), 1)
        self.assertEqual(resp.grades[0].mark, 2.5)

        # Missing required field
        invalid_data = {"grades": [{"script_index": 0}]}
        with self.assertRaises(Exception):
            QuestionBatchGradeResponse.model_validate(invalid_data)

    def checkpoint(self, students=2):
        return {
            "version": 1, "checkpoint_version": 1, "completed": False,
            "scripts": [{"script_index": si, "id_ocr": "", "class_ocr": "", "name_ocr": ""}
                        for si in range(students)],
            "marks": [], "moderated_questions": [],
        }

    def run_batch(self, provider, results, *, students=2, continue_after_block=False):
        continue_grading(
            self.root, results, self.questions, self.boxes, {"students": students}, self.stride,
            self.models, "project", "location", lambda *args: provider,
            [], self.root / "cache", self.metadata_fields, lambda: None,
            mode="batch", batch_concurrency=1,
            continue_on_image_policy_block=continue_after_block,
        )

    def test_failed_question_stops_later_work_and_preserves_detail(self):
        class Failing(FakeBatchProvider):
            def grade_question_batch(provider, question, items):
                provider.batch_calls.append((question["question_number"], len(items)))
                raise RuntimeError("synthetic bounded request failure")
        provider = Failing({}, "", "")
        with self.assertRaisesRegex(RuntimeError, "synthetic bounded request failure"):
            self.run_batch(provider, self.checkpoint())
        self.assertEqual(provider.batch_calls, [("1.1", 2)])
        saved = json.loads((self.root / "results.json").read_text())
        self.assertEqual(saved["moderated_questions"], ["1.1"])
        self.assertEqual(len(saved["marks"]), 2)
        self.assertTrue(all(row["raw_mark"] is None and row["grading_failed"] for row in saved["marks"]))
        self.assertIn("synthetic bounded request failure", saved["marks"][0]["grading_error_detail"])
        self.assertFalse((self.root / "review.xlsx").exists())

    def test_only_explicit_safety_block_can_continue_with_unscored_answers(self):
        class Safety(FakeBatchProvider):
            def grade_question_batch(provider, question, items):
                if question["question_number"] == "1.1":
                    provider.batch_calls.append(("1.1", len(items)))
                    raise GeminiSafetyBlocked("SAFETY")
                return super().grade_question_batch(question, items)
        provider = Safety({}, "", "")
        self.run_batch(provider, self.checkpoint(), continue_after_block=True)
        saved = json.loads((self.root / "results.json").read_text())
        blocked = [row for row in saved["marks"] if row["question"] == "1.1"]
        self.assertTrue(all(row["raw_mark"] is None and row["image_policy_block"] for row in blocked))
        self.assertEqual(provider.batch_calls, [("1.1", 2), ("1.2", 2)])
        class Generic(Safety):
            def grade_question_batch(provider, question, items):
                raise RuntimeError("Image processing blocked due to content policy violation")
        with self.assertRaises(RuntimeError):
            self.run_batch(Generic({}, "", ""), self.checkpoint(), continue_after_block=True)

    def test_transport_bound_and_checkpoint_of_successful_chunk_before_later_failure(self):
        count = MAX_IMAGES_PER_REQUEST + 1
        class LaterFailure(FakeBatchProvider):
            def grade_question_batch(provider, question, items):
                if len(provider.batch_calls) == 1:
                    raise RuntimeError("second transport chunk")
                return super().grade_question_batch(question, items)
        provider = LaterFailure({}, "", "")
        with patch("handwriting_grader.grading.crop_image", return_value=b"synthetic"), \
                patch("handwriting_grader.grading.is_blank_cell", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "second transport chunk"):
                self.run_batch(provider, self.checkpoint(count), students=count)
        saved = json.loads((self.root / "results.json").read_text())
        self.assertEqual(provider.batch_calls, [("1.1", MAX_IMAGES_PER_REQUEST)])
        self.assertEqual(sum(not row["grading_failed"] for row in saved["marks"]), MAX_IMAGES_PER_REQUEST)
        self.assertIsNone(saved["marks"][-1]["raw_mark"])
        self.assertEqual(saved["moderated_questions"], ["1.1"])

    def test_invalid_batch_ids_and_scores_are_failed_without_silent_partial_grades(self):
        for mutation in ("missing", "duplicate", "unexpected", "out-of-range"):
            with self.subTest(mutation=mutation):
                class Invalid(FakeBatchProvider):
                    def grade_question_batch(provider, question, items):
                        grades = list(super().grade_question_batch(question, items).values())
                        if mutation == "missing":
                            grades.pop()
                        elif mutation == "duplicate":
                            grades[1]["script_index"] = 0
                        elif mutation == "unexpected":
                            grades[1]["script_index"] = 100
                        else:
                            grades[0]["mark"] = 100
                        return {"grades": grades}
                with self.assertRaises(RuntimeError):
                    self.run_batch(Invalid({}, "", ""), self.checkpoint())
                saved = json.loads((self.root / "results.json").read_text())
                self.assertTrue(all(row["grading_failed"] for row in saved["marks"]))


if __name__ == "__main__":
    unittest.main()
