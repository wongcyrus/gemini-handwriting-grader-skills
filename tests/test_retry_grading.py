import json
import tempfile
from contextlib import ExitStack
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

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
    def test_explicit_id_retry_bypasses_rejected_cache_and_rechecks_all_ids(self):
        for batch in (True, False):
            with self.subTest(batch=batch):
                self._check_id_retry_cache(rejected=True, batch=batch)

    def test_explicit_id_retry_preserves_valid_metadata_cache(self):
        for batch in (True, False):
            with self.subTest(batch=batch):
                self._check_id_retry_cache(rejected=False, batch=batch)

    def _check_id_retry_cache(self, *, rejected, batch):
        from handwriting_grader.grading import MetadataBatchResponse, StudentIDCheckFailed
        from handwriting_grader.regions import file_hash

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as td, ExitStack() as patches:
            target = Path(td) / "grading"
            target.mkdir()
            regions = target / "regions"
            regions.mkdir()
            atomic_json(regions / "approval.json", {"synthetic": "approved"})
            atomic_json(regions / "annotations.json", {})
            approval_hash = file_hash(regions / "approval.json")
            cache_root = target.parent / "parent-cache"
            manifest = {
                "models": {"ocr": "synthetic", "grading": "synthetic"},
                "project": "synthetic", "location": "synthetic",
                "cache_agents": ["ocr"], "id_gate_version": 1,
                "cache_root": str(cache_root),
                "regions_approval_sha256": approval_hash,
            }
            results = {
                "scripts": [
                    {"script_index": si, "id_ocr": student_id, "errors": []}
                    for si, student_id in enumerate(("001", "002"))
                ],
                "marks": [{
                    "script_index": 0, "question": "1", "page_image": "images/0.jpg",
                    "grading_failed": True, "raw_mark": None,
                }],
                "moderated_questions": [],
            }
            atomic_json(target / "manifest.json", manifest)
            atomic_json(target / "results.json", results)
            question = {"question_number": "1", "marks": 2}
            boxes = {"ID": (0, {}), "1": (0, {})}

            def inspect(*args, **kwargs):
                current = json.loads((target / "results.json").read_text())
                return {
                    "target": target,
                    "manifest": json.loads((target / "manifest.json").read_text()),
                    "results": current, "questions_by_key": {"1": question},
                    "failed_marks": [m for m in current["marks"] if m["grading_failed"]],
                    "re_ocr_fields": ["ID"],
                }

            patches.enter_context(patch("handwriting_grader.grading.inspect_retry_grading", side_effect=inspect))
            patches.enter_context(patch("handwriting_grader.grading.attendance_guard", return_value=lambda: None))
            patches.enter_context(patch("handwriting_grader.grading.approved_inputs",
                                        return_value=([question], regions, {}, boxes)))
            patches.enter_context(patch("handwriting_grader.grading.load_metadata_candidates", return_value={}))
            patches.enter_context(patch("handwriting_grader.grading.load_metadata_roster", return_value=None))
            patches.enter_context(patch("handwriting_grader.attendance.present_roster", return_value=["001", "002"]))
            patches.enter_context(patch(
                "handwriting_grader.grading.crop_image",
                side_effect=lambda image, box: image.name.encode(),
            ))
            patches.enter_context(patch("handwriting_grader.local_filter.is_blank_cell", return_value=False))
            patches.enter_context(patch("handwriting_grader.grading.write_review"))

            def retry(ids):
                provider = FakeRetryProvider(manifest["models"], "synthetic", "synthetic")
                if batch:
                    metadata_call = Mock(return_value=MetadataBatchResponse(students=[
                        {"script_index": si, "id_ocr": student_id}
                        for si, student_id in enumerate(ids)
                    ]))
                    provider.ocr_metadata_batch = metadata_call
                else:
                    metadata_call = Mock(side_effect=ids)
                    provider.ocr = metadata_call
                provider.metadata_call = metadata_call
                factory = lambda *args: provider
                return provider, lambda: retry_grading(
                    {"layout": {"pages_per_student": 1}, "exam": {"output_dir": "."}},
                    target.parent, target.parent / "exam.yaml",
                    {"students": 2}, provider_factory=factory, re_ocr_fields=["ID"],
                )

            first, run = retry(["999", "002"] if rejected else ["001", "002"])
            if rejected:
                with self.assertRaises(StudentIDCheckFailed):
                    run()
                self.assertEqual(first.grade_calls, [])
                self.assertFalse(json.loads((target / "id-check.json").read_text())["passed"])
            else:
                run()
            self.assertEqual(first.metadata_call.call_count, 1 if batch else 2)
            cached = {p.name: p.read_bytes() for p in cache_root.glob("*.json")}
            self.assertEqual(len(cached), 1 if batch else 2)

            if rejected:
                still_bad, run = retry(["001", "001"])
                with self.assertRaises(StudentIDCheckFailed):
                    run()
                self.assertEqual(still_bad.metadata_call.call_count, 1 if batch else 2)
                self.assertEqual(still_bad.grade_calls, [])
                self.assertFalse(json.loads((target / "id-check.json").read_text())["passed"])
            else:
                parent = target
                target = parent.parent / "child" / "grading"
                target.mkdir(parents=True)
                for name in ("manifest.json", "results.json"):
                    atomic_json(target / name, json.loads((parent / name).read_text()))

            corrected, run = retry(["001", "002"])
            run()
            self.assertEqual(corrected.metadata_call.call_count, (1 if batch else 2) if rejected else 0)
            self.assertEqual(corrected.grade_calls, ["1"] if rejected else [])
            current = json.loads((target / "results.json").read_text())
            self.assertEqual([s["id_ocr"] for s in current["scripts"]], ["001", "002"])
            self.assertTrue(json.loads((target / "id-check.json").read_text())["passed"])
            self.assertEqual(file_hash(regions / "approval.json"), approval_hash)
            self.assertEqual(
                {p.name: p.read_bytes() for p in cache_root.glob("*.json")}, cached,
            )

    def test_inspect_retry_grading_identifies_failed_marks_and_moderations(self):
        config_path = Path("exam.yaml")
        if not config_path.exists() or not Path("output/grading/results.json").is_file() or not Path("output/rubric/approval.json").is_file():
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
        if not config_path.exists() or not Path("output/grading/results.json").is_file() or not Path("output/rubric/approval.json").is_file():
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
        if not config_path.exists() or not Path("output/regions/annotations.json").is_file():
            return
        import yaml
        config = yaml.safe_load(config_path.read_text())
        written_files = {}

        def fake_atomic_json(path, data):
            written_files[Path(path).name] = data

        with patch("handwriting_grader.regions.atomic_json", side_effect=fake_atomic_json):
            annotations = approve_regions(config, Path("."), config_path)
            self.assertIsInstance(annotations, dict)
            self.assertIn("approval.json", written_files)
            app = written_files["approval.json"]
            self.assertIn("annotations_sha256", app)
            self.assertIn("approved_at", app)

    def test_load_metadata_candidates_from_exam(self):
        from handwriting_grader.grading import load_metadata_candidates
        config_path = Path("exam.yaml")
        if not config_path.exists():
            return
        import yaml
        config = yaml.safe_load(config_path.read_text())
        candidates = load_metadata_candidates(config, Path("."), config_path)
        self.assertIn("ID", candidates)
        self.assertGreater(len(candidates["ID"]), 0)
        self.assertIn("NAME", candidates)
        self.assertIn("CLASS", candidates)
        self.assertIn("1A", candidates["CLASS"])

    def test_load_metadata_roster_from_exam(self):
        from handwriting_grader.grading import load_metadata_roster
        config_path = Path("exam.yaml")
        if not config_path.exists():
            return
        import yaml
        config = yaml.safe_load(config_path.read_text())
        roster = load_metadata_roster(config, Path("."), config_path)
        self.assertIsInstance(roster, list)
        self.assertEqual(len(roster), 74)
        first = roster[0]
        self.assertIn("id", first)
        self.assertIn("name", first)
        self.assertIn("class", first)

    def test_batch_extract_metadata_fallback(self):
        from handwriting_grader.grading import batch_extract_metadata
        class FallbackProvider:
            def ocr(self, image, field, candidates=None):
                return f"extracted_{field}"

        boxes = {
            "ID": (0, {"x": 0, "y": 0, "width": 10, "height": 10}),
            "CLASS": (0, {"x": 0, "y": 0, "width": 10, "height": 10}),
        }
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as td:
            img_dir = Path(td)
            from PIL import Image
            img = Image.new("RGB", (100, 100), color="white")
            img.save(img_dir / "0.jpg")
            res = batch_extract_metadata(
                provider=FallbackProvider(),
                script_indices=[0],
                fields=["ID", "CLASS"],
                boxes=boxes,
                stride=1,
                image_dir=img_dir,
            )
            self.assertIn(0, res)
            self.assertEqual(res[0]["id_ocr"], "extracted_ID")
            self.assertEqual(res[0]["class_ocr"], "extracted_CLASS")

    def test_partial_duplicate_and_unexpected_metadata_never_fall_back_to_paid_ocr(self):
        from handwriting_grader.grading import batch_extract_metadata, MetadataBatchResponse
        boxes = {field: (0, {}) for field in ("ID", "CLASS", "NAME")}
        for ids in ([0], [0, 0], [0, 9]):
            with self.subTest(ids=ids):
                provider = Mock()
                provider.ocr_metadata_batch.return_value = MetadataBatchResponse(
                    students=[{"script_index": index, "id_ocr": "synthetic"} for index in ids],
                )
                checkpoint = Mock()
                with patch("handwriting_grader.grading.crop_image", return_value=b"synthetic"):
                    with self.assertRaises(ValueError):
                        batch_extract_metadata(provider, [0, 1], list(boxes), boxes, 1, Path("."),
                                               on_chunk=checkpoint)
                provider.ocr.assert_not_called()
                checkpoint.assert_not_called()

    def test_metadata_checkpoint_survives_later_failure_and_preserves_35_script_baseline(self):
        from handwriting_grader.grading import batch_extract_metadata, MetadataBatchResponse
        boxes = {field: (0, {}) for field in ("ID", "CLASS", "NAME")}
        saved = {}
        requests = []
        class Provider:
            def ocr_metadata_batch(self, items, fields, roster_list=None):
                requests.append((len(items), sum(len(item["crops"]) for item in items)))
                if len(requests) == 2:
                    raise RuntimeError("synthetic metadata second chunk")
                return MetadataBatchResponse(students=[
                    {"script_index": item["script_index"], "id_ocr": "synthetic"}
                    for item in items
                ])
            def ocr(self, *args, **kwargs):
                raise AssertionError("Must not fall back to paid OCR")
        with patch("handwriting_grader.grading.crop_image", return_value=b"synthetic"):
            with self.assertRaisesRegex(RuntimeError, "synthetic metadata second chunk"):
                batch_extract_metadata(Provider(), list(range(36)), list(boxes), boxes, 1, Path("."),
                                       on_chunk=lambda chunk: saved.update(chunk))
        self.assertEqual(requests, [(35, 105), (1, 3)])
        self.assertEqual(set(saved), set(range(35)))
