"""Synthetic regression coverage for provider-neutral recovery."""

import json
import io
import re
import tempfile
import unittest
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpyxl import load_workbook

import test_grading as fixtures
from test_batch_grading import FakeBatchProvider
from handwriting_grader import batch_recovery, grading
from handwriting_grader.regions import atomic_json, file_hash


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory
        def local_directory(*args, **kwargs):
            kwargs.setdefault("dir", Path.cwd())
            return temporary_directory(*args, **kwargs)
        with patch("tempfile.TemporaryDirectory", side_effect=local_directory):
            fixtures.GradingTests.setUp(self)

    def run_grading(self, provider=fixtures.FakeGrader):
        return fixtures.GradingTests.run_grading(self, provider)

    def read_results(self, target):
        return json.loads((target / "results.json").read_text())

    def edit_review(self, target, edit):
        book = load_workbook(target / "review.xlsx")
        try:
            edit(book)
            book.save(target / "review.xlsx")
        finally:
            book.close()

    def dispatch_cli(self, arguments):
        from handwriting_grader.cli import main
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main([*arguments, "--exam", str(self.exam)])
        return status, stdout.getvalue(), stderr.getvalue()

    def failed_run(self, *, completed=False):
        target = self.run_grading(FakeBatchProvider)
        results = self.read_results(target)
        for row in results["marks"]:
            row.update(answer="", raw_mark=None, moderated_mark=None, similarity_score=None,
                       reasoning="", moderator_flag=True, moderator_note="image_policy_block",
                       grading_failed=True, image_policy_block=True,
                       grading_error_detail="synthetic block")
        results["completed"] = completed
        atomic_json(target / "results.json", results)
        (target / "review.xlsx").unlink()
        if completed:
            grading.write_review(target / "review.xlsx", results)
        return target

    def test_review_updates_keep_manual_zero_corrections_ids_and_custom_columns(self):
        target = self.run_grading()
        def edit(book):
            book["Scripts"].cell(2, 4, "001")
            book["Scripts"].cell(2, 5, "yes")
            sheet = book["Marks"]
            sheet.cell(1, 14, "teacher_note")
            sheet.cell(2, 14, "=literal teacher text").data_type = "s"
            sheet.cell(2, 4, "corrected answer")
            sheet.cell(2, 7, "corrected reasoning")
            sheet.cell(2, 11, 0)
            sheet.cell(2, 12, "yes")
            sheet.cell(3, 12, "yes")
        self.edit_review(target, edit)
        previous = self.read_results(target)
        after = deepcopy(previous)
        after["scripts"][0]["id_ocr"] = "new OCR"
        for row in after["marks"]:
            row.update(answer="new answer", reasoning="new reasoning", raw_mark=1.5, moderated_mark=1.5)
        path = target / "review.xlsx"
        old_hash = file_hash(path)
        prepared = grading._update_retry_review(path, previous, after, {0}, {"1"}, old_hash, prepare=True)
        self.assertTrue((target / "review-history" / f"{old_hash}.xlsx").is_file())
        grading._update_retry_review(path, previous, after, {0}, {"1"}, prepared)
        book = load_workbook(path)
        try:
            self.assertEqual(book["Scripts"].cell(2, 4).value, "001")
            self.assertIsNone(book["Scripts"].cell(2, 5).value)
            self.assertEqual(book["Scripts"].cell(2, 2).value, "new OCR")
            sheet = book["Marks"]
            self.assertEqual(sheet.cell(2, 4).value, "corrected answer")
            self.assertEqual(sheet.cell(2, 7).value, "corrected reasoning")
            self.assertEqual(sheet.cell(2, 11).value, 0)
            self.assertEqual(sheet.cell(2, 14).value, "=literal teacher text")
            self.assertEqual(sheet.cell(2, 14).data_type, "s")
            self.assertIsNone(sheet.cell(2, 12).value)
            self.assertIsNone(sheet.cell(3, 12).value)
            self.assertEqual(sheet.cell(3, 4).value, "new answer")
        finally:
            book.close()

    def test_unaffected_review_confirmations_and_metadata_are_untouched(self):
        target = self.run_grading()
        self.edit_review(target, lambda b: b["Scripts"].cell(3, 5, "yes"))
        previous = self.read_results(target)
        after = deepcopy(previous)
        after["scripts"][1]["name_ocr"] = "not selected"
        path = target / "review.xlsx"
        grading._update_retry_review(path, previous, after, {0}, set(), file_hash(path), prepare=True)
        book = load_workbook(path)
        try:
            self.assertEqual(book["Scripts"].cell(3, 5).value, "yes")
            self.assertEqual(book["Scripts"].cell(3, 6).value, "Example name")
        finally:
            book.close()

    def test_atomic_creation_does_not_replace_existing_or_publish_partial_workbook(self):
        target = self.run_grading()
        results = self.read_results(target)
        path = target / "review.xlsx"
        before = file_hash(path)
        with self.assertRaises(ValueError):
            grading.write_review(path, results, overwrite=True)
        with self.assertRaises(FileExistsError):
            grading.write_review(path, results)
        self.assertEqual(file_hash(path), before)
        new_path = target / "broken.xlsx"
        with patch.object(grading.Workbook, "save", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                grading.write_review(new_path, results)
        self.assertFalse(new_path.exists())
        self.assertEqual(sorted(p.name for p in target.glob("*.xlsx")), ["review.xlsx"])

    def test_completed_checkpoint_resume_republishes_atomically_without_provider(self):
        target = self.run_grading()
        (target / "review.xlsx").unlink()
        provider = Mock(side_effect=AssertionError("Completed run must not invoke a model"))
        with patch.object(grading.Workbook, "save", side_effect=OSError("interrupted publication")):
            with self.assertRaises(OSError):
                grading.resume_grading(self.config, self.root, self.exam, self.summary, provider_factory=provider)
        self.assertTrue(self.read_results(target)["completed"])
        self.assertFalse((target / "review.xlsx").exists())
        grading.resume_grading(self.config, self.root, self.exam, self.summary, provider_factory=provider)
        provider.assert_not_called()
        self.assertTrue((target / "review.xlsx").is_file())

    def test_invalid_duplicate_missing_or_stale_review_rows_fail_closed(self):
        target = self.run_grading()
        results = self.read_results(target)
        path = target / "review.xlsx"
        original = path.read_bytes()
        for bad_id in ("0", True, 99, 1):
            with self.subTest(bad_id=bad_id):
                path.write_bytes(original)
                self.edit_review(target, lambda b: setattr(b["Marks"].cell(2, 1), "value", bad_id))
                before = file_hash(path)
                with self.assertRaises(ValueError):
                    grading._update_retry_review(path, results, results, set(), {"1"}, before, prepare=True)
                self.assertEqual(file_hash(path), before)
        path.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "changed"):
            grading._update_retry_review(path, results, results, set(), {"1"}, "stale", prepare=True)

    def test_interrupted_retry_resets_affected_confirmations_before_request_and_backs_up(self):
        target = self.run_grading()
        fixtures.GradingTests.fill_review(self, target / "review.xlsx", flagged_override=0)
        original = file_hash(target / "review.xlsx")
        class Interrupt(fixtures.FakeGrader):
            def grade(provider, question, answer="", image=None):
                book = load_workbook(target / "review.xlsx")
                try:
                    assert all(book["Marks"].cell(row, 12).value is None for row in (2, 3))
                    assert all(book["Scripts"].cell(row, 5).value == "yes" for row in (2, 3))
                finally:
                    book.close()
                raise KeyboardInterrupt("synthetic interruption")
        with self.assertRaises(KeyboardInterrupt):
            grading.retry_grading(self.config, self.root, self.exam, self.summary,
                                  provider_factory=Interrupt, question="1")
        self.assertFalse((target / "processing.lock").exists())
        self.assertFalse((target / "review.lock").exists())
        backups = list((target / "retry-history").glob("retry-*/review.xlsx"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(file_hash(backups[0]), original)
        book = load_workbook(target / "review.xlsx")
        try:
            self.assertEqual(book["Marks"].cell(2, 11).value, 0)
            self.assertIsNone(book["Marks"].cell(2, 12).value)
        finally:
            book.close()

    def test_retry_refuses_human_changes_during_request(self):
        target = self.run_grading()
        before = file_hash(target / "results.json")
        owner = self
        class Concurrent(fixtures.FakeGrader):
            def grade(provider, question, answer="", image=None):
                owner.edit_review(target, lambda b: b["Marks"].cell(2, 7, "human changed"))
                return super().grade(question, answer, image)
        with self.assertRaisesRegex(ValueError, "changed"):
            grading.retry_grading(self.config, self.root, self.exam, self.summary,
                                  provider_factory=Concurrent, question="1")
        self.assertEqual(file_hash(target / "results.json"), before)

    def test_failed_batch_preview_retry_only_failed_work_and_no_continuation(self):
        target = self.failed_run()
        results = self.read_results(target)
        results["marks"][0].update(raw_mark=0, moderated_mark=0, grading_failed=False)
        atomic_json(target / "results.json", results)
        info = grading.inspect_failed_question_batches(self.config, self.root, self.exam, self.summary)
        self.assertEqual(len(info["failed"]), 1)
        self.assertEqual(info["requests"], 1)
        provider = FakeBatchProvider({}, "", "")
        with self.assertRaisesRegex(ValueError, "changed"):
            grading.retry_failed_question_batches(self.config, self.root, self.exam, self.summary, "stale",
                                                 provider_factory=lambda *a: provider)
        grading.retry_failed_question_batches(self.config, self.root, self.exam, self.summary,
                                             info["checkpoint_hash"], provider_factory=lambda *a: provider)
        self.assertEqual(provider.batch_calls, [("Q1", 1)])
        saved = self.read_results(target)
        self.assertEqual(saved["marks"][0], results["marks"][0])
        self.assertFalse(saved["marks"][1]["grading_failed"])
        self.assertFalse(saved["completed"])
        self.assertFalse((target / "review.xlsx").exists())
        self.assertFalse((target / "approval.json").exists())

    def test_failed_batch_retry_rechecks_all_ids_before_provider_or_answer_crops(self):
        target = self.failed_run()
        original = self.read_results(target)
        original["marks"][0].update(raw_mark=0, moderated_mark=0, grading_failed=False)
        cases = [
            ("unknown", "999", [], True),
            ("missing", "", [], False),
            ("duplicate", "002", [], True),
            ("extraction_error", "001", [{"field": "ID", "type": "ValueError"}], False),
        ]
        for label, student_id, errors, cached_passed in cases:
            with self.subTest(case=label):
                results = deepcopy(original)
                results["scripts"][0].update(id_ocr=student_id, errors=errors)
                atomic_json(target / "results.json", results)
                atomic_json(target / "id-check.json", {"passed": cached_passed})
                info = grading.inspect_failed_question_batches(self.config, self.root, self.exam, self.summary)
                self.assertEqual([row["script_index"] for row in info["failed"]], [1])
                provider = Mock(side_effect=AssertionError("ID failure must precede provider creation"))
                with patch.object(grading, "crop_image") as crop:
                    with self.assertRaises(grading.StudentIDCheckFailed):
                        grading.retry_failed_question_batches(
                            self.config, self.root, self.exam, self.summary, info["checkpoint_hash"],
                            provider_factory=provider,
                        )
                provider.assert_not_called()
                crop.assert_not_called()
                report = json.loads((target / "id-check.json").read_text())
                self.assertFalse(report["passed"])
                self.assertIn(0, [issue["script_index"] for issue in report["issues"]])
                self.assertEqual(self.read_results(target), results)
                self.assertFalse((target / "processing.lock").exists())

    def test_failed_batch_retry_recomputes_stale_failed_id_report(self):
        target = self.failed_run()
        atomic_json(target / "id-check.json", {"passed": False})
        info = grading.inspect_failed_question_batches(self.config, self.root, self.exam, self.summary)
        provider = FakeBatchProvider({}, "", "")
        grading.retry_failed_question_batches(
            self.config, self.root, self.exam, self.summary, info["checkpoint_hash"],
            provider_factory=lambda *a: provider,
        )
        self.assertEqual(provider.batch_calls, [("Q1", 2)])
        self.assertTrue(json.loads((target / "id-check.json").read_text())["passed"])

    def test_failed_batch_retry_preserves_legacy_manifest_behavior(self):
        target = self.failed_run()
        manifest = json.loads((target / "manifest.json").read_text())
        manifest.pop("id_gate_version")
        atomic_json(target / "manifest.json", manifest)
        results = self.read_results(target)
        results["scripts"][0]["id_ocr"] = "legacy OCR awaiting human review"
        atomic_json(target / "results.json", results)
        info = grading.inspect_failed_question_batches(self.config, self.root, self.exam, self.summary)
        provider = FakeBatchProvider({}, "", "")
        grading.retry_failed_question_batches(
            self.config, self.root, self.exam, self.summary, info["checkpoint_hash"],
            provider_factory=lambda *a: provider,
        )
        self.assertEqual(provider.batch_calls, [("Q1", 2)])

    def test_offline_export_leaves_checkpoint_and_run_unchanged(self):
        target = self.failed_run()
        before = file_hash(target / "results.json")
        output = self.root / "offline-review"
        grading.export_failed_review(self.config, self.root, self.exam, self.summary, output)
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual(manifest["checkpoint_sha256"], before)
        self.assertEqual(manifest["failed_answers"], 2)
        self.assertEqual(len(list((output / "crops").glob("*.png"))), 2)
        self.assertEqual(file_hash(target / "results.json"), before)
        self.assertFalse((target / "approval.json").exists())
        self.assertFalse((target / "review.xlsx").exists())

    def test_completed_recovery_protects_manual_zero_corrected_and_confirmed_answers(self):
        target = self.failed_run(completed=True)
        for field, value in ((11, 0), (4, "human answer"), (7, "human reason"), (12, "yes")):
            with self.subTest(field=field):
                self.edit_review(target, lambda b: b["Marks"].cell(2, field, value))
                info = batch_recovery.inspect_batch_recovery(self.config, self.root, self.exam, "1")
                self.assertEqual([row["script_index"] for row in info["rows"]], [1])
                self.edit_review(target, lambda b: setattr(b["Marks"].cell(2, field), "value", None))

    def test_budgeted_safety_bisection_persists_detail_and_leaves_singletons_unscored(self):
        target = self.failed_run(completed=True)
        original_review = file_hash(target / "review.xlsx")
        info = batch_recovery.inspect_batch_recovery(self.config, self.root, self.exam, "1")
        class Filtered(FakeBatchProvider):
            def grade_question_batch(provider, question, items):
                if any(item["script_index"] == 1 for item in items):
                    raise grading.GeminiSafetyBlocked("SAFETY")
                return super().grade_question_batch(question, items)
        result = batch_recovery.recover_question_batches(
            self.config, self.root, self.exam, "1", info["revision"], 2, provider_factory=Filtered,
        )
        self.assertEqual((result["recovered"], result["blocked"], result["pending"], result["requests"]),
                         (1, 0, 1, 2))
        state = json.loads((result["history"] / "recovery.json").read_text())
        self.assertEqual(state["attempts"][0]["error_type"], "GeminiSafetyBlocked")
        self.assertIn("SAFETY", state["attempts"][0]["detail"])
        saved = self.read_results(target)
        self.assertTrue(saved["marks"][0]["batch_recovery_review_pending"])
        self.assertFalse(saved["marks"][0]["grading_failed"])
        self.assertIsNone(saved["marks"][1]["raw_mark"])
        self.assertEqual(file_hash(target / "review.xlsx"), original_review)
        next_info = batch_recovery.inspect_batch_recovery(self.config, self.root, self.exam, "1")
        singleton = batch_recovery.recover_question_batches(
            self.config, self.root, self.exam, "1", next_info["revision"], 1, provider_factory=Filtered,
        )
        self.assertEqual((singleton["recovered"], singleton["blocked"], singleton["requests"]), (0, 1, 1))
        self.assertIsNone(self.read_results(target)["marks"][1]["raw_mark"])

    def test_generic_error_is_not_safety_and_stops_without_bisection(self):
        self.failed_run(completed=True)
        info = batch_recovery.inspect_batch_recovery(self.config, self.root, self.exam, "1")
        class Generic(FakeBatchProvider):
            def grade_question_batch(provider, question, items):
                raise RuntimeError("Image processing blocked due to content policy violation")
        result = batch_recovery.recover_question_batches(
            self.config, self.root, self.exam, "1", info["revision"], 10, provider_factory=Generic,
        )
        self.assertEqual((result["recovered"], result["blocked"], result["pending"], result["requests"]),
                         (0, 0, 2, 1))
        self.assertIn("RuntimeError", result["error"])

    def test_completed_recovery_revision_change_keeps_durable_grades_and_human_edit(self):
        target = self.failed_run(completed=True)
        info = batch_recovery.inspect_batch_recovery(self.config, self.root, self.exam, "1")
        owner = self
        class Concurrent(FakeBatchProvider):
            def grade_question_batch(provider, question, items):
                response = super().grade_question_batch(question, items)
                owner.edit_review(target, lambda b: b["Marks"].cell(2, 11, 0))
                return response
        before = file_hash(target / "results.json")
        with self.assertRaisesRegex(ValueError, "changed"):
            batch_recovery.recover_question_batches(
                self.config, self.root, self.exam, "1", info["revision"], 1, provider_factory=Concurrent,
            )
        self.assertEqual(file_hash(target / "results.json"), before)
        histories = list((target / "retry-history").glob("bisect-*/recovery.json"))
        self.assertEqual(len(json.loads(histories[0].read_text())["grades"]), 2)
        book = load_workbook(target / "review.xlsx")
        try:
            self.assertEqual(book["Marks"].cell(2, 11).value, 0)
        finally:
            book.close()

    def test_recovery_budget_and_preview_revision_fail_before_requests(self):
        self.failed_run(completed=True)
        info = batch_recovery.inspect_batch_recovery(self.config, self.root, self.exam, "1")
        provider = Mock(side_effect=AssertionError("Must not invoke a model"))
        for budget in (True, 0, -1, 1.5):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                batch_recovery.recover_question_batches(
                    self.config, self.root, self.exam, "1", info["revision"], budget, provider_factory=provider,
                )
        with self.assertRaisesRegex(ValueError, "changed"):
            batch_recovery.recover_question_batches(
                self.config, self.root, self.exam, "1", "stale", 1, provider_factory=provider,
            )
        provider.assert_not_called()

    def test_cli_retry_failed_batches_preview_stale_hash_and_approved_real_dispatch(self):
        target = self.failed_run()
        before = self.read_results(target)
        before["marks"][0].update(raw_mark=0, moderated_mark=0, grading_failed=False)
        atomic_json(target / "results.json", before)
        implementation = grading.retry_failed_question_batches
        provider = FakeBatchProvider({}, "", "")
        factory = Mock(return_value=provider)
        def adapter(*args, **kwargs):
            return implementation(*args, provider_factory=factory, **kwargs)
        with patch("handwriting_grader.grading.retry_failed_question_batches", side_effect=adapter) as dispatch, \
                patch("handwriting_grader.grading.genai.Client",
                      side_effect=AssertionError("CLI must use only the injected synthetic provider")):
            status, stdout, stderr = self.dispatch_cli(["retry-failed-batches"])
            self.assertEqual(status, 1)
            self.assertIn("No request sent", stderr)
            checkpoint = re.search(r"Checkpoint SHA-256: ([0-9a-f]{64})", stdout).group(1)
            dispatch.assert_not_called()
            factory.assert_not_called()
            status, _, stderr = self.dispatch_cli([
                "retry-failed-batches", "--confirm-cost", "--checkpoint-sha256", "stale",
            ])
            self.assertEqual(status, 1)
            self.assertIn("no request sent", stderr)
            dispatch.assert_not_called()
            factory.assert_not_called()
            status, stdout, _ = self.dispatch_cli([
                "retry-failed-batches", "--confirm-cost", "--checkpoint-sha256", checkpoint,
            ])
            self.assertEqual(status, 0)
            self.assertIn("preview resume-grading", stdout)
            dispatch.assert_called_once()
            factory.assert_called_once()
        self.assertEqual(provider.batch_calls, [("Q1", 1)])
        saved = self.read_results(target)
        self.assertEqual(saved["marks"][0], before["marks"][0])
        self.assertFalse(saved["marks"][1]["grading_failed"])
        self.assertFalse(saved["completed"])
        self.assertFalse((target / "review.xlsx").exists())
        self.assertFalse((target / "approval.json").exists())

    def test_cli_bisect_failed_batch_preview_stale_revision_and_approved_real_dispatch(self):
        target = self.failed_run(completed=True)
        review_hash = file_hash(target / "review.xlsx")
        implementation = batch_recovery.recover_question_batches
        provider = FakeBatchProvider({}, "", "")
        factory = Mock(return_value=provider)
        def adapter(*args, **kwargs):
            return implementation(*args, provider_factory=factory, **kwargs)
        args = ["bisect-failed-batch", "--question", "1", "--max-requests", "1"]
        with patch("handwriting_grader.batch_recovery.recover_question_batches", side_effect=adapter) as dispatch, \
                patch("handwriting_grader.grading.genai.Client",
                      side_effect=AssertionError("CLI must use only the injected synthetic provider")):
            status, stdout, stderr = self.dispatch_cli(args)
            self.assertEqual(status, 1)
            self.assertIn("No request sent", stderr)
            revision = re.search(r"Review and grading revision: ([0-9a-f:]+)", stdout).group(1)
            dispatch.assert_not_called()
            factory.assert_not_called()
            status, _, stderr = self.dispatch_cli([*args, "--confirm-cost", "--revision", "stale"])
            self.assertEqual(status, 1)
            self.assertIn("no request sent", stderr)
            dispatch.assert_not_called()
            factory.assert_not_called()
            status, stdout, _ = self.dispatch_cli([*args, "--confirm-cost", "--revision", revision])
            self.assertEqual(status, 0)
            self.assertIn("2 recovered", stdout)
            dispatch.assert_called_once()
            factory.assert_called_once()
        self.assertEqual(provider.batch_calls, [("Q1", 2)])
        saved = self.read_results(target)
        self.assertTrue(all(not row["grading_failed"] and row["batch_recovery_review_pending"]
                            for row in saved["marks"]))
        self.assertEqual(file_hash(target / "review.xlsx"), review_hash)
        self.assertFalse((target / "approval.json").exists())

    def test_cli_export_failed_review_dispatches_real_offline_export_without_inference(self):
        target = self.failed_run()
        before = file_hash(target / "results.json")
        output = self.root / "cli-offline-review"
        implementation = grading.export_failed_review
        with patch("handwriting_grader.grading.export_failed_review", wraps=implementation) as dispatch, \
                patch("handwriting_grader.grading.genai.Client",
                      side_effect=AssertionError("Offline export must not initialize an inference client")) as client:
            status, _, stderr = self.dispatch_cli(["export-failed-review", "--output", str(output)])
            self.assertEqual(status, 0, stderr)
            dispatch.assert_called_once()
            client.assert_not_called()
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual(manifest["checkpoint_sha256"], before)
        self.assertEqual(manifest["failed_answers"], 2)
        self.assertTrue((output / "review.xlsx").is_file())
        self.assertEqual(len(list((output / "crops").glob("*.png"))), 2)
        self.assertEqual(file_hash(target / "results.json"), before)
        self.assertFalse((target / "review.xlsx").exists())
        self.assertFalse((target / "approval.json").exists())


class GeminiSafetyTests(unittest.TestCase):
    def test_vertex_client_constructor_retains_sdk_retries_and_provider_settings(self):
        with patch("handwriting_grader.grading.genai.Client") as client:
            grading.VertexGrader({"ocr": "gemini-3.8-flash"}, "synthetic-project", "global")
        options = client.call_args.kwargs
        self.assertTrue(options["vertexai"])
        self.assertEqual(options["project"], "synthetic-project")
        self.assertEqual(options["location"], "global")
        http = options["http_options"]
        self.assertEqual(http.timeout, 300_000)
        self.assertEqual(http.retry_options.attempts, 3)
        self.assertEqual(http.retry_options.http_status_codes, [429, 500, 502, 503, 504])

    def test_native_metadata_and_question_transports_enforce_application_cap(self):
        provider = object.__new__(grading.VertexGrader)
        provider.models = {"ocr": "gemini-3.8-flash", "grading": "gemini-3.8-flash",
                           "one_pass_grading": "gemini-4-argon"}
        requests = []
        def generate(**options):
            contents = options["contents"]
            indices = [int(part.split("#")[1].split("]")[0])
                       for part in contents if isinstance(part, str) and part.startswith("\n--- [SCRIPT")]
            requests.append((options["model"], len(indices), options["config"]))
            grades = [
                {"script_index": index, "answer": "synthetic", "mark": 1,
                 "similarity_score": 0.5, "reasoning": "synthetic"}
                for index in indices
            ]
            return SimpleNamespace(parsed={"grades": grades}, candidates=[], usage_metadata=None)
        provider.client = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(side_effect=generate)))
        question = {"question_number": "1", "question_text": "Q", "marking_scheme": "S", "marks": 2}
        items = [{"script_index": index, "image": b"synthetic"}
                 for index in range(grading.MAX_IMAGES_PER_REQUEST + 1)]
        response = provider.grade_question_batch(question, items)
        self.assertEqual(len(response.grades), len(items))
        self.assertEqual([(model, size) for model, size, _ in requests],
                         [("gemini-4-argon", 105), ("gemini-4-argon", 1)])
        self.assertTrue(all(config.response_schema is grading.QuestionBatchGradeResponse
                            and config.thinking_config is not None for _, _, config in requests))
        with self.assertRaises(ValueError):
            provider.ocr_metadata_batch([
                {"script_index": index, "crops": {"ID": b"x", "CLASS": b"x", "NAME": b"x"}}
                for index in range(36)
            ])
        self.assertEqual(provider.client.models.generate_content.call_count, 2)

    def test_prompt_and_candidate_safety_are_typed_and_generic_errors_are_not(self):
        for response in (
            SimpleNamespace(prompt_feedback=SimpleNamespace(block_reason="SAFETY"), candidates=[]),
            SimpleNamespace(candidates=[SimpleNamespace(finish_reason="PROHIBITED_CONTENT")]),
        ):
            with self.assertRaises(grading.GeminiSafetyBlocked):
                grading._check_gemini_safety(response)
        grading._check_gemini_safety(SimpleNamespace(candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")]))
        self.assertFalse(batch_recovery._policy_block(ValueError("content_filter")))

    def test_vertex_response_safety_raises_before_parsing_with_unchanged_settings(self):
        provider = object.__new__(grading.VertexGrader)
        provider.models = {"ocr": "gemini-3.8-flash", "grading": "gemini-3.8-flash"}
        provider.client = SimpleNamespace(models=Mock())
        provider.client.models.generate_content.return_value = SimpleNamespace(
            prompt_feedback=SimpleNamespace(block_reason="SAFETY"), candidates=[],
            text=None, parsed=None, usage_metadata=None,
        )
        question = {"question_number": "1", "question_text": "Q", "marking_scheme": "S", "marks": 2}
        for call in (
            lambda: provider.ocr(b"synthetic", "ID"),
            lambda: provider.ocr_metadata_batch([{"script_index": 0, "crops": {"ID": b"synthetic"}}], fields=["ID"]),
            lambda: provider.grade(question, image=b"synthetic"),
            lambda: provider.grade_question_batch(question, [{"script_index": 0, "image": b"synthetic"}]),
        ):
            with self.assertRaises(grading.GeminiSafetyBlocked):
                call()
        for call in provider.client.models.generate_content.call_args_list:
            self.assertIsNone(call.kwargs["config"].safety_settings)
