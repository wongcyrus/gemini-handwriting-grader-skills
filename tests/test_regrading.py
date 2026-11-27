import json
import io
import copy
import os
import shutil
import tempfile
import threading
import unittest
import urllib.request
from io import BytesIO
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from openpyxl import Workbook, load_workbook
from PIL import Image
from pypdf import PdfWriter

from handwriting_grader.analytics import create_analytics
from handwriting_grader.advanced import create_analysis
from handwriting_grader.cli import check_exam, main
from handwriting_grader.crop_proposals import prepare_crop_proposal, approve_crop_proposal
from handwriting_grader.distribution import preview_email
from handwriting_grader.grade_review import approve_grading, read_approved_grading, validate_review
from handwriting_grader.grading import grade_exam
from handwriting_grader.regrading import prepare_rubric_proposal, regrade, resume_regrade
from handwriting_grader.regions import atomic_json, file_hash, prepare_regions, verify_run
from handwriting_grader.reporting import create_reports
from handwriting_grader.rubric import approve_rubric, import_rubric, write_rubric
from handwriting_grader.score_editor import ScoreServer, load_state
from test_grading import FakeGrader


class RegradingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        book = Workbook()
        sheet = book.active
        sheet.title = "Roster"
        sheet.append(["ID", "NAME", "CLASS"])
        sheet.append(["001", "One", "A"])
        sheet.append(["002", "Two", "A"])
        book.save(self.root / "roster.xlsx")
        book.close()
        from PIL import Image, ImageDraw
        pages = []
        for idx in range(2):
            img = Image.new("RGB", (200, 300), "white")
            draw = ImageDraw.Draw(img)
            for y_off in range(2, 100, 6):
                draw.line([(0, y_off), (200, y_off)], fill="black", width=2)
            draw.text((15, 5), str(idx + 1), fill="black")
            draw.text((10, 120), f"Page {idx}", fill="black")
            pages.append(img)
        pages[0].save(self.root / "scan.pdf", save_all=True, append_images=pages[1:], resolution=72.0)
        self.questions = [
            {"question_number": "Q1", "question_text": "First", "marking_scheme": "Award two", "marks": 2},
            {"question_number": "Q2", "question_text": "Second", "marking_scheme": "Award three", "marks": 3},
        ]
        write_rubric(self.root / "source.xlsx", self.questions)
        self.config = {"exam": {"name": "Synthetic", "roster": "roster.xlsx", "roster_sheet": "Roster",
                                "scanned_pdf": "scan.pdf", "marking_scheme": "source.xlsx", "output_dir": "output"},
                       "layout": {"pages_per_student": 1, "same_layout_for_all": True}}
        self.exam = self.root / "exam.yaml"
        self.exam.write_text(yaml.safe_dump(self.config))
        self.models = self.root / "models.yaml"
        self.models.write_text(yaml.safe_dump({"vertex_ai": {"use_vertexai": True},
            "models": {"ocr": "gemini-3.8-flash", "grading": "gemini-3.8-flash", "moderation": "gemini-4-argon"}}))
        import_rubric(self.config, self.root, self.exam)
        approve_rubric(self.config, self.root, self.exam)
        regions = prepare_regions(self.config, self.root, 1)
        _, manifest = verify_run(self.config, self.root, self.exam)
        boxes = [{"x": i * 40, "y": 10, "width": 30, "height": 30, "label": label}
                 for i, label in enumerate(["NAME", "ID", "CLASS", "1", "2"])]
        annotations = {"0": boxes}
        atomic_json(regions / "draft.json", {"revision": 1, "annotations": annotations, "reviewed": [0]})
        atomic_json(regions / "annotations.json", annotations)
        atomic_json(regions / "approval.json", {
            "annotations_sha256": file_hash(regions / "annotations.json"),
            "config_sha256": manifest["config_sha256"], "pdf_sha256": manifest["pdf_sha256"], "revision": 1,
        })
        _, summary = check_exam(self.config, self.root)
        self.parent = grade_exam(self.config, self.root, self.exam, self.models, "project", "global", summary,
                                 provider_factory=FakeGrader)
        self.review(self.parent / "review.xlsx")
        book = load_workbook(self.parent / "review.xlsx")
        for row in book["Marks"].iter_rows(min_row=2):
            if row[0].value == 0 and row[1].value == "Q2":
                row[3].value = "Teacher corrected answer"
                row[6].value = "Teacher reasoning"
                row[10].value = 2
        book.save(self.parent / "review.xlsx")
        book.close()
        approve_grading(self.config, self.root, self.exam)

    def review(self, path):
        book = load_workbook(path)
        for row, student in [(2, "001"), (3, "002")]:
            book["Scripts"].cell(row, 4, student)
            book["Scripts"].cell(row, 5, "yes")
        for row in book["Marks"].iter_rows(min_row=2):
            if (row[7].value or row[9].value) and row[10].value is None:
                row[10].value = 1
            row[11].value = "yes"
        book.save(path)
        book.close()

    def test_child_review_diagnostics_uses_selected_run(self):
        child = regrade(self.config, self.root, self.exam, "check-child", None, "1",
                        self.models, "project", "global", provider_factory=FakeGrader)
        original_approval = file_hash(self.parent / "approval.json")
        args = ["check-grading-review", "--exam", str(self.exam), "--run", "check-child"]
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(args), 1)
        self.assertIn("identity not reviewed", output.getvalue())
        self.assertFalse((child / "grading" / "approval.json").exists())
        self.review(child / "grading" / "review.xlsx")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 0)
        self.assertEqual(file_hash(self.parent / "approval.json"), original_approval)
        self.assertFalse((child / "grading" / "approval.json").exists())

    def test_reviewed_shared_and_individual_crop_proposal_follows_child_lineage(self):
        source = self.root / "crops.json"
        source.write_text(json.dumps({
            "shared": {"x": 1, "y": 2, "width": 20, "height": 25},
            "students": {"1": {"x": 3, "y": 4, "width": 30, "height": 35}},
        }))
        prepare_crop_proposal(self.config, self.root, self.exam, "crop1", None, "Q1", source)
        with self.assertRaises((OSError, ValueError)):
            regrade(self.config, self.root, self.exam, "without-approval", None, "Q1", self.models,
                    "project", "global", crop_proposal_id="crop1", provider_factory=FakeGrader)
        approve_crop_proposal(self.config, self.root, self.exam, "crop1")
        sizes = []

        class CropsGrader(FakeGrader):
            def grade(self, question, answer="", image=None):
                if image is not None:
                    with Image.open(BytesIO(image)) as decoded:
                        sizes.append(decoded.size)
                return super().grade(question, answer, image=image)

        child = regrade(self.config, self.root, self.exam, "cropped", None, "Q1", self.models,
                        "project", "global", crop_proposal_id="crop1", provider_factory=CropsGrader)
        self.assertEqual(sorted(sizes), [(20, 25), (30, 35)])
        server = ScoreServer(self.config, self.root, config_path=self.exam, run_id="cropped")
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            request = urllib.request.Request(server.origin + "/api/state",
                                             headers={"Authorization": "Bearer " + server.token})
            with urllib.request.urlopen(request, timeout=10) as response:
                state = json.load(response)
            self.assertEqual([state["crops"][i][0]["width"] for i in range(2)], [20, 30])
        finally:
            server.shutdown()
            thread.join(10)
        self.review(child / "grading" / "review.xlsx")
        approve_grading(self.config, self.root, self.exam, run_id="cropped")
        sizes.clear()
        grandchild = regrade(self.config, self.root, self.exam, "inherited", "cropped", "Q1", self.models,
                             "project", "global", provider_factory=CropsGrader)
        self.assertEqual(sorted(sizes), [(20, 25), (30, 35)])
        self.review(grandchild / "grading" / "review.xlsx")
        approve_grading(self.config, self.root, self.exam, run_id="inherited")
        self.assertTrue(create_reports(self.config, self.root, self.exam, run_id="inherited").is_dir())
        source.write_text(source.read_text() + " ")
        with self.assertRaises(ValueError):
            read_approved_grading(self.config, self.root, self.exam, run_id="inherited")
        self.assertTrue(read_approved_grading(self.config, self.root, self.exam))

    def test_crop_proposals_reject_invalid_inputs_and_require_separate_approval(self):
        source = self.root / "crops.json"
        for data in (
            {"shared": {"x": -1, "y": 2, "width": 20, "height": 25}},
            {"shared": {"x": 1, "y": 2, "width": 0, "height": 25}},
            {"students": {"2": {"x": 1, "y": 2, "width": 20, "height": 25}}},
            {"students": {"00": {"x": 1, "y": 2, "width": 20, "height": 25}}},
        ):
            source.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                prepare_crop_proposal(self.config, self.root, self.exam, "invalid", None, "1", source)
        source.write_text('{"students": {}, "students": {}}')
        with self.assertRaisesRegex(ValueError, "duplicate keys"):
            prepare_crop_proposal(self.config, self.root, self.exam, "invalid", None, "1", source)
        source.write_text(json.dumps({"students": {"1": {"x": 1, "y": 2, "width": 20, "height": 25}}}))
        args = ["prepare-regrade-crops", "--exam", str(self.exam), "--proposal", "crop2",
                "--question", "Q1", "--crops", str(source)]
        self.assertEqual(main(args), 0)
        approval = ["approve-regrade-crops", "--exam", str(self.exam), "--proposal", "crop2"]
        self.assertEqual(main(approval), 1)
        self.assertEqual(main(approval + ["--confirm-reviewed"]), 0)
        self.assertEqual(main(approval + ["--confirm-reviewed"]), 1)
        request = ["regrade", "--exam", str(self.exam), "--run", "preview", "--question", "Q1",
                   "--model-config", str(self.models), "--project", "project", "--location", "global",
                   "--crop-proposal", "crop2"]
        def fake_regrade(*positional):
            return regrade(*positional, provider_factory=FakeGrader)

        with patch("handwriting_grader.regrading.regrade", side_effect=fake_regrade) as provider:
            self.assertEqual(main(request), 1)
            provider.assert_not_called()
            self.assertEqual(main(request + ["--confirm-cost"]), 0)
            self.assertEqual(provider.call_count, 1)
        self.assertTrue((self.root / "output" / "runs" / "preview" / "grading" / "review.xlsx").exists())

    def test_explicit_parent_cache_reuse_avoids_matching_model_calls(self):
        config = copy.deepcopy(self.config)
        config["exam"]["output_dir"] = "cached-output"
        exam = self.root / "cached-exam.yaml"
        exam.write_text(yaml.safe_dump(config))
        models = self.root / "cached-models.yaml"
        models.write_text(yaml.safe_dump({
            "vertex_ai": {"use_vertexai": True},
            "models": {"ocr": "gemini-3.8-flash", "grading": "gemini-3.8-flash", "moderation": "gemini-4-argon"},
            "caching": {"enabled": True, "scope": "run",
                        "agents": {"ocr": True, "grading": True, "moderation": True}},
        }))
        import_rubric(config, self.root, exam)
        approve_rubric(config, self.root, exam)
        regions = prepare_regions(config, self.root, 1)
        _, manifest = verify_run(config, self.root, exam)
        draft = json.loads((self.root / "output" / "regions" / "draft.json").read_text())
        atomic_json(regions / "draft.json", draft)
        atomic_json(regions / "annotations.json", draft["annotations"])
        atomic_json(regions / "approval.json", {
            "annotations_sha256": file_hash(regions / "annotations.json"),
            "config_sha256": manifest["config_sha256"], "pdf_sha256": manifest["pdf_sha256"],
            "revision": draft["revision"],
        })
        _, summary = check_exam(config, self.root)
        parent = grade_exam(config, self.root, exam, models, "project", "global", summary,
                            provider_factory=FakeGrader)
        self.review(parent / "review.xlsx")
        approve_grading(config, self.root, exam)
        parent_manifest = json.loads((parent / "manifest.json").read_text())
        args = ["regrade", "--exam", str(exam), "--run", "preview-cache", "--question", "Q1",
                "--model-config", str(models), "--project", "project", "--location", "global",
                "--reuse-parent-cache"]
        with patch("handwriting_grader.regrading.regrade") as execute, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(args), 1)
            execute.assert_not_called()
        self.assertIn(parent_manifest["cache_root"], output.getvalue())
        self.assertIn("rather than regenerated", output.getvalue())
        self.assertFalse((self.root / "cached-output" / "runs" / "preview-cache").exists())
        calls = []

        class NoCallGrader(FakeGrader):
            def ocr(self, image, field):
                calls.append(("ocr", field))
                return super().ocr(image, field)

            def grade(self, question, answer):
                calls.append(("grade", question["question_number"]))
                return super().grade(question, answer)

            def moderate(self, question, entries):
                calls.append(("moderate", question["question_number"]))
                return super().moderate(question, entries)

        child = regrade(config, self.root, exam, "reuse", None, "Q1", models, "project", "global",
                        reuse_parent_cache=True, provider_factory=NoCallGrader)
        self.assertEqual(calls, [])
        child_manifest = json.loads((child / "grading" / "manifest.json").read_text())
        self.assertTrue(child_manifest["reuse_parent_cache"])
        self.assertEqual(child_manifest["cache_root"], parent_manifest["cache_root"])
        self.review(child / "grading" / "review.xlsx")
        approve_grading(config, self.root, exam, run_id="reuse")
        self.assertTrue(read_approved_grading(config, self.root, exam, run_id="reuse"))

        calls.clear()
        changed_models = self.root / "different-models.yaml"
        changed = yaml.safe_load(models.read_text())
        changed["models"]["grading"] = "gemini-4-hydrogen"
        changed_models.write_text(yaml.safe_dump(changed))
        regrade(config, self.root, exam, "changed-model", None, "Q1", changed_models, "project", "global",
                reuse_parent_cache=True, provider_factory=NoCallGrader)
        self.assertIn(("grade", "Q1"), calls)
        self.assertNotIn(("ocr", "answer"), calls)

        calls.clear()
        independent = regrade(config, self.root, exam, "independent", None, "Q1", models,
                              "project", "global", provider_factory=NoCallGrader)
        independent_manifest = json.loads((independent / "grading" / "manifest.json").read_text())
        self.assertFalse(independent_manifest["reuse_parent_cache"])
        self.assertNotEqual(independent_manifest["cache_root"], parent_manifest["cache_root"])
        self.assertIn(("grade", "Q1"), calls)

        class Interrupt(NoCallGrader):
            def grade(self, question, answer):
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            regrade(config, self.root, exam, "interrupted-cache", None, "Q1", models,
                    "another-project", "global", reuse_parent_cache=True, provider_factory=Interrupt)
        calls.clear()
        resume_regrade(config, self.root, exam, "interrupted-cache", provider_factory=NoCallGrader)
        self.assertNotIn(("ocr", "answer"), calls)
        self.assertIn(("grade", "Q1"), calls)
        interrupted_manifest = json.loads(
            (self.root / "cached-output" / "runs" / "interrupted-cache" / "grading" / "manifest.json").read_text()
        )
        self.assertEqual(interrupted_manifest["cache_root"], parent_manifest["cache_root"])

        if os.name != "nt":
            cache_root = Path(parent_manifest["cache_root"])
            cache_root.chmod(0o755)
            try:
                with self.assertRaisesRegex(ValueError, "private"):
                    regrade(config, self.root, exam, "unsafe-cache", None, "Q1", models,
                            "project", "global", reuse_parent_cache=True, provider_factory=NoCallGrader)
            finally:
                cache_root.chmod(0o700)

    def test_parent_cache_reuse_requires_enabled_compatible_cache(self):
        with self.assertRaisesRegex(ValueError, "requires enabled compatible"):
            regrade(self.config, self.root, self.exam, "reuse-disabled", None, "Q1", self.models,
                    "project", "global", reuse_parent_cache=True, provider_factory=FakeGrader)
        self.assertFalse((self.root / "output" / "runs" / "reuse-disabled").exists())

    def test_preview_child_review_reports_and_parent_unchanged(self):
        before = {name: file_hash(self.parent / name)
                  for name in ("manifest.json", "results.json", "review.xlsx", "approval.json")}
        args = ["regrade", "--exam", str(self.exam), "--run", "revision1", "--question", "Q1",
                "--model-config", str(self.models), "--project", "project", "--location", "global"]
        self.assertEqual(main(args), 1)
        self.assertFalse((self.root / "output" / "runs" / "revision1").exists())
        calls = []

        class SpyGrader(FakeGrader):
            def ocr(self, image, field):
                calls.append(("ocr", field))
                return super().ocr(image, field)

            def grade(self, question, answer):
                calls.append(("grade", question["question_number"]))
                return super().grade(question, answer)

            def moderate(self, question, entries):
                calls.append(("moderate", question["question_number"], len(entries)))
                return super().moderate(question, entries)

        child = regrade(self.config, self.root, self.exam, "revision1", None, "1",
                        self.models, "project", "global", provider_factory=SpyGrader)
        self.assertEqual(calls, [("grade", "Q1"), ("grade", "Q1"), ("moderate", "Q1", 2)])
        self.assertEqual(before, {name: file_hash(self.parent / name) for name in before})
        with self.assertRaisesRegex(ValueError, "explicit human review"):
            validate_review(self.config, self.root, self.exam, run_id="revision1")
        book = load_workbook(child / "grading" / "review.xlsx")
        previous = list(book["Previous"].values)
        self.assertIn((0, "Q2", "Teacher corrected answer", "Teacher reasoning", 2, "no"), previous)
        self.assertTrue(all(row[4].value is None for row in book["Scripts"].iter_rows(min_row=2)))
        self.assertTrue(all(row[11].value is None for row in book["Marks"].iter_rows(min_row=2)))
        retained = next(row for row in book["Marks"].iter_rows(min_row=2)
                        if row[0].value == 0 and row[1].value == "Q2")
        self.assertEqual((retained[3].value, retained[6].value, retained[10].value),
                         ("Teacher corrected answer", "Teacher reasoning", 2))
        book.close()
        server = ScoreServer(self.config, self.root, config_path=self.exam, run_id="revision1")
        self.addCleanup(server.server_close)
        state = load_state(server)
        self.assertEqual(state[5][(0, "1")]["previous"]["mark"], 1)
        self.assertTrue(state[5][(0, "1")]["previous"]["rerun"])
        self.assertEqual(state[5][(0, "2")]["previous"]["answer"], "Teacher corrected answer")
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            request = urllib.request.Request(server.origin + "/api/state",
                                             headers={"Authorization": "Bearer " + server.token})
            with urllib.request.urlopen(request, timeout=10) as response:
                body = json.load(response)
            self.assertEqual(body["marks"][0][1]["previous"]["mark"], 2)
        finally:
            server.shutdown()
            thread.join(10)
        self.review(child / "grading" / "review.xlsx")
        approve_grading(self.config, self.root, self.exam, run_id="revision1")
        approved = read_approved_grading(self.config, self.root, self.exam, run_id="revision1")
        self.assertEqual(approved[6][(0, "2")], 2)
        reports = create_reports(self.config, self.root, self.exam, run_id="revision1")
        stats = create_analytics(self.config, self.root, self.exam, run_id="revision1")
        self.assertEqual(reports.parent, child)
        self.assertEqual(stats.parent, child)
        chart = json.loads((stats / "manifest.json").read_text())["question_boxplots"][0]
        self.assertEqual(file_hash(stats / chart["path"]), chart["sha256"])
        self.assertFalse((self.root / "output" / "analytics").exists())
        self.assertFalse((self.root / "output" / "reports").exists())
        settings = self.root / "email.yaml"
        settings.write_text(yaml.safe_dump({"sender": "teacher@example.test", "smtp_host": "smtp.example.test",
            "smtp_port": 587, "smtp_username": "teacher", "subject": "{exam}", "body": "Hello {name}"}))
        recipients = self.root / "recipients.csv"
        recipients.write_text("ID,EMAIL\n001,one@example.test\n002,two@example.test\n")
        email = preview_email(self.config, self.root, self.exam, settings, recipients, run_id="revision1")
        self.assertEqual(email.parent, child)
        self.assertEqual(main(["approve-email", "--exam", str(self.exam), "--run", "revision1",
                               "--confirm-reviewed"]), 0)
        with patch("handwriting_grader.distribution.smtplib.SMTP") as smtp, \
             patch.dict(os.environ, {"HANDWRITING_SMTP_PASSWORD": "fake-secret"}):
            smtp.return_value.__enter__.return_value.sendmail.return_value = {}
            send = ["send-email", "--exam", str(self.exam), "--run", "revision1"]
            self.assertEqual(main(send), 1)
            smtp.assert_not_called()
            self.assertEqual(main(send + ["--confirm-send"]), 0)
            self.assertEqual(smtp.return_value.__enter__.return_value.sendmail.call_count, 2)
        self.assertFalse((self.root / "output" / "email").exists())
        self.assertEqual(before, {name: file_hash(self.parent / name) for name in before})

    def test_changed_rubric_requires_review_and_rejects_other_changes(self):
        changed = [dict(question) for question in self.questions]
        changed[1]["marking_scheme"] = "Unauthorised change"
        replacement = self.root / "replacement.xlsx"
        write_rubric(replacement, changed)
        with self.assertRaisesRegex(ValueError, "Only the selected"):
            prepare_rubric_proposal(self.config, self.root, self.exam, "proposal", None, "Q1", replacement)
        changed[1]["marking_scheme"] = self.questions[1]["marking_scheme"]
        changed[0]["marking_scheme"] = "New criterion"
        changed[0]["marks"] = 4
        write_rubric(replacement, changed)
        proposal = prepare_rubric_proposal(self.config, self.root, self.exam, "proposal", None, "Q1", replacement)
        with self.assertRaises(OSError):
            regrade(self.config, self.root, self.exam, "child", None, "Q1", self.models, "project", "global",
                    "proposal", provider_factory=FakeGrader)
        self.assertEqual(main(["approve-regrade-rubric", "--exam", str(self.exam), "--proposal", "proposal"]), 1)
        self.assertEqual(main(["approve-regrade-rubric", "--exam", str(self.exam), "--proposal", "proposal",
                               "--confirm-reviewed"]), 0)
        child = regrade(self.config, self.root, self.exam, "child", None, "Q1", self.models, "project", "global",
                        "proposal", provider_factory=FakeGrader)
        self.assertEqual(read_approved_grading(self.config, self.root, self.exam)[3][0]["marks"], 2)
        self.review(child / "grading" / "review.xlsx")
        approve_grading(self.config, self.root, self.exam, run_id="child")
        self.assertEqual(read_approved_grading(self.config, self.root, self.exam, run_id="child")[3][0]["marks"], 4)
        replacement.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            read_approved_grading(self.config, self.root, self.exam, run_id="child")
        self.assertTrue((proposal / "approval.json").exists())

    def test_interrupted_child_resumes_and_preserves_checkpoint(self):
        class Interrupted(FakeGrader):
            calls = 0

            def grade(self, question, answer):
                type(self).calls += 1
                if type(self).calls == 2:
                    raise KeyboardInterrupt()
                return super().grade(question, answer)

        with self.assertRaises(KeyboardInterrupt):
            regrade(self.config, self.root, self.exam, "child", None, "Q1", self.models, "project", "global",
                    provider_factory=Interrupted)
        checkpoint = self.root / "output" / "runs" / "child" / "grading" / "results.json"
        before = json.loads(checkpoint.read_text())
        self.assertEqual(sum(row["question"] == "Q1" for row in before["marks"]), 1)
        resume_args = ["resume-grading", "--exam", str(self.exam), "--run", "child"]
        self.assertEqual(main(resume_args), 1)
        self.assertEqual(json.loads(checkpoint.read_text()), before)
        calls = []

        class Resume(FakeGrader):
            def grade(self, question, answer):
                calls.append(question["question_number"])
                return super().grade(question, answer)

        resume_regrade(self.config, self.root, self.exam, "child", provider_factory=Resume)
        self.assertEqual(calls, ["Q1"])
        self.assertTrue((checkpoint.parent / "review.xlsx").exists())
        with self.assertRaisesRegex(ValueError, "review or approval"):
            resume_regrade(self.config, self.root, self.exam, "child", provider_factory=Resume)

    def test_failed_answer_requires_manual_marks_and_skips_moderation(self):
        calls = []

        class Failing(FakeGrader):
            def grade(self, question, answer):
                raise ValueError("Synthetic failure")

            def moderate(self, question, entries):
                calls.append("moderation")
                return super().moderate(question, entries)

        child = regrade(self.config, self.root, self.exam, "failed", None, "Q1", self.models, "project", "global",
                        provider_factory=Failing)
        self.assertEqual(calls, [])
        raw = json.loads((child / "grading" / "results.json").read_text())
        rerun = [row for row in raw["marks"] if row["question"] == "Q1"]
        self.assertTrue(all(row["grading_failed"] and row["moderator_flag"] and row["moderated_mark"] is None
                            for row in rerun))
        book = load_workbook(child / "grading" / "review.xlsx")
        for row, student in [(2, "001"), (3, "002")]:
            book["Scripts"].cell(row, 4, student)
            book["Scripts"].cell(row, 5, "yes")
        for row in book["Marks"].iter_rows(min_row=2):
            row[11].value = "yes"
        book.save(child / "grading" / "review.xlsx")
        book.close()
        with self.assertRaisesRegex(ValueError, "manual mark"):
            approve_grading(self.config, self.root, self.exam, run_id="failed")

    def test_child_can_be_parent_and_uses_its_approved_rubric(self):
        first = regrade(self.config, self.root, self.exam, "first", None, "Q1", self.models, "project", "global",
                        provider_factory=FakeGrader)
        self.review(first / "grading" / "review.xlsx")
        approve_grading(self.config, self.root, self.exam, run_id="first")
        second = regrade(self.config, self.root, self.exam, "second", "first", "Q2", self.models, "project", "global",
                         provider_factory=FakeGrader)
        self.review(second / "grading" / "review.xlsx")
        self.assertEqual(main(["approve-grading", "--exam", str(self.exam), "--run", "second",
                               "--confirm-reviewed"]), 0)
        self.assertEqual(main(["report", "--exam", str(self.exam), "--run", "second"]), 0)
        self.assertTrue((second / "reports" / "manifest.json").exists())
        self.assertFalse((first / "reports").exists())

    def test_concurrent_child_creation_never_replaces_existing_run(self):
        entered = threading.Event()
        release = threading.Event()
        failures = []

        class Slow(FakeGrader):
            def grade(self, question, answer="", image=None):
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("Test timed out")
                return super().grade(question, answer, image=image)

        def create():
            try:
                regrade(self.config, self.root, self.exam, "child", None, "Q1", self.models, "project", "global",
                        provider_factory=Slow)
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=create)
        thread.start()
        try:
            self.assertTrue(entered.wait(10))
            with self.assertRaisesRegex(ValueError, "already exists"):
                regrade(self.config, self.root, self.exam, "child", None, "Q1", self.models, "project", "global",
                        provider_factory=FakeGrader)
        finally:
            release.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])

    def test_stale_parent_and_invalid_run_id_are_rejected(self):
        with self.assertRaises(ValueError):
            regrade(self.config, self.root, self.exam, "../escape", None, "Q1", self.models, "project", "global")
        child = regrade(self.config, self.root, self.exam, "child", None, "Q1", self.models, "project", "global",
                        provider_factory=FakeGrader)
        self.review(child / "grading" / "review.xlsx")
        approve_grading(self.config, self.root, self.exam, run_id="child")
        book = load_workbook(self.parent / "review.xlsx")
        book["Scripts"]["D2"] = "002"
        book.save(self.parent / "review.xlsx")
        book.close()
        with self.assertRaises(ValueError):
            read_approved_grading(self.config, self.root, self.exam, run_id="child")

    def test_inherited_checkpoint_cannot_be_changed(self):
        child = regrade(self.config, self.root, self.exam, "child", None, "Q1", self.models, "project", "global",
                        provider_factory=FakeGrader)
        results = child / "grading" / "results.json"
        data = json.loads(results.read_text())
        next(row for row in data["marks"] if row["question"] == "Q2")["moderated_mark"] = 0
        results.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "Inherited child results"):
            validate_review(self.config, self.root, self.exam, run_id="child")

    def test_child_ai_analysis_is_isolated_and_requires_fresh_approval(self):
        child = regrade(self.config, self.root, self.exam, "child", None, "Q1", self.models, "project", "global",
                        provider_factory=FakeGrader)
        self.review(child / "grading" / "review.xlsx")
        approve_grading(self.config, self.root, self.exam, run_id="child")
        model = self.root / "analytics.yaml"
        model.write_text(yaml.safe_dump({"vertex_ai": {"use_vertexai": True}, "models": {"analytics": "gemini-4-argon"}}))

        class FakeAnalyst:
            def __init__(self, *args):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def analyze(self, task, data):
                return "Synthetic feedback"

        analysis = create_analysis(self.config, self.root, self.exam, model, "project", "global",
                                   provider_factory=FakeAnalyst, run_id="child")
        self.assertEqual(analysis.parent, child)
        self.assertEqual(main(["approve-analysis", "--exam", str(self.exam), "--run", "child",
                               "--confirm-reviewed"]), 1)
        book = load_workbook(analysis / "review.xlsx")
        for sheet in book:
            for row in sheet.iter_rows(min_row=2):
                row[len(row) - 1].value = "yes"
        book.save(analysis / "review.xlsx")
        book.close()
        self.assertEqual(main(["approve-analysis", "--exam", str(self.exam), "--run", "child",
                               "--confirm-reviewed"]), 0)
        self.assertEqual(main(["advanced-report", "--exam", str(self.exam), "--run", "child"]), 0)
        self.assertTrue((analysis / "reports" / "teacher-report.docx").exists())
        self.assertFalse((self.root / "output" / "ai-analysis").exists())

    def test_confirmed_cli_regrade_uses_requested_child(self):
        args = ["regrade", "--exam", str(self.exam), "--run", "cli", "--question", "Q1",
                "--model-config", str(self.models), "--project", "project", "--location", "global"]

        def fake_regrade(*positional):
            return regrade(*positional, provider_factory=FakeGrader)

        with patch("handwriting_grader.regrading.regrade", side_effect=fake_regrade) as call:
            self.assertEqual(main(args), 1)
            call.assert_not_called()
            self.assertEqual(main(args + ["--confirm-cost"]), 0)
            self.assertEqual(call.call_count, 1)
        self.assertTrue((self.root / "output" / "runs" / "cli" / "grading" / "review.xlsx").exists())


if __name__ == "__main__":
    unittest.main()
