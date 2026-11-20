import base64
import io
import importlib.util
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import yaml
from openpyxl import Workbook, load_workbook
from PIL import Image
from pypdf import PdfReader, PdfWriter
from docx import Document
from pptx import Presentation
from matplotlib.axes import Axes

from handwriting_grader.advanced import create_analysis
from handwriting_grader.analytics import question_boxplots, question_passing_policy
from handwriting_grader.cli import check_exam, main
from handwriting_grader.grade_review import approve_grading, read_approved_grading, validate_review
from handwriting_grader.grading import VertexGrader, approved_inputs, grade_exam, resume_grading, safe_append
from handwriting_grader.regions import atomic_json, file_hash, prepare_regions, verify_run
from handwriting_grader.reporting import passing_policy, passing_rows, sample_rows, write_workbooks
from handwriting_grader.rubric import approve_rubric, import_rubric, write_rubric
from handwriting_grader.score_editor import ScoreServer


class FakeGrader:
    def __init__(self, models, project, location):
        self.ids = iter(["001", "002"])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def ocr(self, image, field):
        if field == "ID":
            return next(self.ids)
        if field == "NAME":
            return "Example name"
        return "A" if field == "CLASS" else "Example answer"

    def grade(self, question, answer="", image=None):
        return {"answer": "Example answer", "mark": 1.0, "similarity_score": 0.5, "reasoning": "Example"}

    def moderate(self, question, entries):
        return {
            entry["script_index"]: {
                "script_index": entry["script_index"],
                "moderated_mark": entry["mark"],
                "flag": entry["script_index"] == 0,
                "note": "Check" if entry["script_index"] == 0 else "",
            }
            for entry in entries
        }


@unittest.skipUnless(shutil.which("pdftoppm"), "Poppler is required")
class GradingTests(unittest.TestCase):
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
        pdf = PdfWriter()
        for _ in range(4):
            pdf.add_blank_page(width=200, height=300)
        with (self.root / "scan.pdf").open("wb") as file:
            pdf.write(file)
        write_rubric(self.root / "source.xlsx", [{
            "question_number": "Q1", "question_text": "Question",
            "marking_scheme": "Award two marks", "marks": 2,
        }])
        self.config = {
            "exam": {
                "name": "Example", "roster": "roster.xlsx", "roster_sheet": "Roster",
                "scanned_pdf": "scan.pdf", "marking_scheme": "source.xlsx", "output_dir": "output",
            },
            "layout": {"pages_per_student": 2, "same_layout_for_all": True},
        }
        self.exam = self.root / "exam.yaml"
        self.exam.write_text(yaml.safe_dump(self.config))
        self.models = self.root / "models.yaml"
        self.models.write_text(yaml.safe_dump({
            "vertex_ai": {"use_vertexai": True},
            "models": {"ocr": "ocr-model", "grading": "grade-model", "moderation": "moderate-model"},
        }))
        _, self.summary = check_exam(self.config, self.root)
        import_rubric(self.config, self.root, self.exam)
        approve_rubric(self.config, self.root, self.exam)
        regions = prepare_regions(self.config, self.root, 2)
        _, manifest = verify_run(self.config, self.root, self.exam)
        boxes = [
            {"x": i * 40, "y": 10, "width": 30, "height": 30, "label": label}
            for i, label in enumerate(["NAME", "ID", "CLASS", "1"])
        ]
        annotations = {"0": boxes, "1": []}
        atomic_json(regions / "draft.json", {"revision": 1, "annotations": annotations, "reviewed": [0, 1]})
        atomic_json(regions / "annotations.json", annotations)
        atomic_json(regions / "approval.json", {
            "annotations_sha256": file_hash(regions / "annotations.json"),
            "config_sha256": manifest["config_sha256"], "pdf_sha256": manifest["pdf_sha256"],
            "revision": 1,
        })

    def run_grading(self, provider=FakeGrader):
        return grade_exam(self.config, self.root, self.exam, self.models, "test-project", "global",
                          self.summary, provider_factory=provider)

    def fill_review(self, path, *, flagged_override=1):
        book = load_workbook(path)
        scripts = book["Scripts"]
        for index, student in enumerate(("001", "002"), start=2):
            scripts.cell(index, 4, student)
            scripts.cell(index, 5, "yes")
        marks = book["Marks"]
        for row in range(2, 4):
            marks.cell(row, 12, "yes")
        if flagged_override is not None:
            marks.cell(2, 11, flagged_override)
        book.save(path)
        book.close()

    def start_score_editor(self):
        self.score_server = ScoreServer(self.config, self.root, config_path=self.exam)
        thread = threading.Thread(target=self.score_server.serve_forever, daemon=True)
        thread.start()

        def cleanup():
            self.score_server.shutdown()
            thread.join(timeout=5)
            self.score_server.server_close()

        self.addCleanup(cleanup)

    def score_request(self, path, body=None, *, token=True, origin=True, host=None):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {self.score_server.token}"
        if host is not None:
            headers["Host"] = host
        if body is not None:
            headers["Content-Type"] = "application/json"
            if origin:
                headers["Origin"] = self.score_server.origin
            body = json.dumps(body).encode()
        request = urllib.request.Request(self.score_server.origin + path, data=body, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.read()

    def test_score_editor_security_concurrency_and_review_invalidation(self):
        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        approve_grading(self.config, self.root, self.exam)
        self.start_score_editor()
        self.assertEqual(self.score_request("/api/state", token=False)[0], 403)
        self.assertEqual(self.score_request("/api/state", host="evil.example")[0], 403)
        self.assertEqual(self.score_request("/api/save", {}, origin=False)[0], 403)
        status, content = self.score_request("/api/state")
        self.assertEqual(status, 200)
        state = json.loads(content)
        revision = state["revision"]
        self.assertEqual(state["marks"][0][0]["similarity_score"], 0.5)
        workbook_hash = file_hash(root / "review.xlsx")
        suggestion = {"revision": revision, "script_index": 0, "question_index": 0,
                      "threshold": ".8", "step": ".3"}
        status, content = self.score_request("/api/suggest", suggestion)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["suggestion"], "0.9")
        self.assertEqual(file_hash(root / "review.xlsx"), workbook_hash)
        self.assertEqual(self.score_request("/api/suggest", {**suggestion, "step": "0"})[0], 400)
        self.assertEqual(self.score_request("/api/suggest", {**suggestion, "step": "1e-999999"})[0], 400)
        status, image = self.score_request("/api/image/0/0")
        self.assertEqual(status, 200)
        self.assertEqual(image[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(self.score_request("/api/image/0/full-0")[1][:3], b"\xff\xd8\xff")
        self.assertEqual(self.score_request("/api/image/0/../../review.xlsx")[0], 404)
        change = {
            "revision": revision, "kind": "mark", "script_index": 0, "question_index": 0,
            "answer": "=1+1", "reasoning": "Reviewed", "manual_mark": "", "reviewed": True,
        }
        original_results = (root / "results.json").read_bytes()
        altered = json.loads(original_results)
        altered["marks"][0]["answer"] = "Changed outside the editor"
        (root / "results.json").write_text(json.dumps(altered))
        self.assertEqual(self.score_request("/api/save", change)[0], 409)
        (root / "results.json").write_bytes(original_results)
        self.assertEqual(self.score_request("/api/save", change)[0], 400)
        lock = root / ".score-review.lock"
        lock.write_text("test")
        self.assertEqual(self.score_request("/api/save", change)[0], 409)
        lock.unlink()
        change["manual_mark"] = "2"
        status, content = self.score_request("/api/save", change)
        self.assertEqual(status, 200, content)
        self.assertEqual(self.score_request("/api/save", change)[0], 409)
        self.assertEqual(file_hash(root / "review-history" / f"{workbook_hash}.xlsx"), workbook_hash)
        with self.assertRaisesRegex(ValueError, "changed since approval"):
            read_approved_grading(self.config, self.root, self.exam)
        book = load_workbook(root / "review.xlsx", data_only=False)
        try:
            self.assertEqual(book["Marks"]["D2"].value, "=1+1")
            self.assertEqual(book["Marks"]["D2"].data_type, "s")
        finally:
            book.close()
        state = json.loads(self.score_request("/api/state")[1])
        self.assertEqual(self.score_request("/api/suggest", {**suggestion, "revision": state["revision"]})[0], 400)
        self.assertEqual(self.score_request("/api/approve", {"revision": state["revision"]})[0], 400)
        self.assertEqual(self.score_request("/api/approve", {
            "revision": state["revision"], "confirmed": True,
        })[0], 200)
        self.assertEqual(read_approved_grading(self.config, self.root, self.exam)[2]["marks"][0]["answer"], "=1+1")

    def test_score_editor_cli_starts_and_serves_review(self):
        self.run_grading()
        command = [sys.executable, "-c", "from handwriting_grader.cli import main; raise SystemExit(main())",
                   "review-grading", "--exam", str(self.exam)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready, _, _ = select.select([process.stdout], [], [], 15)
            self.assertTrue(ready, "CLI editor did not become ready")
            line = process.stdout.readline().strip()
            self.assertTrue(line.startswith("Open http://127.0.0.1:"), line)
            url, token = line.removeprefix("Open ").split("#", 1)
            request = urllib.request.Request(url + "api/state", headers={"Authorization": "Bearer " + token})
            with urllib.request.urlopen(request, timeout=15) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(len(json.load(response)["scripts"]), 2)
        finally:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=10)

    def test_score_editor_handles_older_results_without_similarity(self):
        root = self.run_grading()
        results = json.loads((root / "results.json").read_text())
        for mark in results["marks"]:
            del mark["similarity_score"]
        (root / "results.json").write_text(json.dumps(results))
        self.start_score_editor()
        state = json.loads(self.score_request("/api/state")[1])
        self.assertIsNone(state["marks"][0][0]["similarity_score"])
        status, content = self.score_request("/api/suggest", {
            "revision": state["revision"], "script_index": 0, "question_index": 0,
            "threshold": ".8", "step": ".25",
        })
        self.assertEqual(status, 400)
        self.assertIn("Similarity is unavailable", json.loads(content)["error"])

    def test_bulk_similarity_preview_and_explicit_unreviewed_drafts(self):
        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        approve_grading(self.config, self.root, self.exam)
        self.start_score_editor()
        state = json.loads(self.score_request("/api/state")[1])
        request = {"revision": state["revision"], "question_index": 0, "threshold": ".8", "step": ".25"}
        original_hash = file_hash(root / "review.xlsx")
        self.assertEqual(self.score_request("/api/bulk-apply", {**request, "confirmed": True})[0], 400)
        status, response = self.score_request("/api/bulk-preview", request)
        self.assertEqual(status, 200)
        preview = json.loads(response)
        self.assertEqual([x["suggestion"] for x in preview["suggestions"]], ["1.00", "1.00"])
        self.assertEqual(file_hash(root / "review.xlsx"), original_hash)
        selected = [{"script_index": 0, "suggestion": "1.00"}]
        apply = {**request, "preview_token": preview["preview_token"], "selected": selected, "confirmed": True}
        self.assertEqual(self.score_request("/api/bulk-apply", {**apply, "confirmed": False})[0], 400)
        self.assertEqual(self.score_request("/api/bulk-apply", {**apply, "selected": [
            {"script_index": 0, "suggestion": "2"}]})[0], 400)
        self.assertEqual(self.score_request("/api/bulk-apply", apply)[0], 200)
        self.assertEqual(file_hash(root / "review-history" / f"{original_hash}.xlsx"), original_hash)
        workbook = load_workbook(root / "review.xlsx")
        try:
            self.assertEqual(workbook["Marks"]["K2"].value, 1)
            self.assertIsNone(workbook["Marks"]["L2"].value)
            self.assertEqual(workbook["Marks"]["L3"].value, "yes")
        finally:
            workbook.close()
        with self.assertRaises(ValueError):
            read_approved_grading(self.config, self.root, self.exam)
        self.assertEqual(self.score_request("/api/bulk-apply", apply)[0], 400)
        new_state = json.loads(self.score_request("/api/state")[1])
        stale = {**request, "revision": new_state["revision"]}
        self.assertEqual(self.score_request("/api/bulk-preview", request)[0], 409)
        status, response = self.score_request("/api/bulk-preview", stale)
        self.assertEqual(status, 200)

    def test_bulk_similarity_excludes_corrected_answers(self):
        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        workbook = load_workbook(root / "review.xlsx")
        workbook["Marks"]["D3"] = "Corrected"
        workbook.save(root / "review.xlsx")
        workbook.close()
        self.start_score_editor()
        state = json.loads(self.score_request("/api/state")[1])
        request = {"revision": state["revision"], "question_index": 0, "threshold": ".8", "step": ".25"}
        status, response = self.score_request("/api/bulk-preview", request)
        self.assertEqual(status, 200)
        preview = json.loads(response)
        self.assertIsNone(preview["suggestions"][1]["suggestion"])
        self.assertIn("original OCR", preview["suggestions"][1]["error"])
        before = file_hash(root / "review.xlsx")
        status, _ = self.score_request("/api/bulk-apply", {
            **request, "preview_token": preview["preview_token"], "confirmed": True,
            "selected": [{"script_index": 1, "suggestion": "1.00"}],
        })
        self.assertEqual(status, 400)
        self.assertEqual(file_hash(root / "review.xlsx"), before)

    def test_adjusted_crop_preview_is_bounded_and_read_only(self):
        root = self.run_grading()
        self.start_score_editor()
        state = json.loads(self.score_request("/api/state")[1])
        before = {name: file_hash(root / name) for name in ("review.xlsx", "results.json", "manifest.json")}
        request = {"revision": state["revision"], "script_index": 0, "question_index": 0,
                   "x": 1, "y": 2, "width": 15, "height": 20}
        status, content = self.score_request("/api/preview-crop", request)
        self.assertEqual(status, 200)
        with Image.open(io.BytesIO(content)) as image:
            self.assertEqual(image.size, (15, 20))
        for invalid in ({"x": -1}, {"width": 0}, {"x": 999999}, {"height": 1.5}):
            self.assertEqual(self.score_request("/api/preview-crop", {**request, **invalid})[0], 400)
        self.assertEqual(self.score_request("/api/preview-crop", {**request, "revision": "stale"})[0], 409)
        self.assertEqual(before, {name: file_hash(root / name) for name in before})
        self.assertFalse((root / "approval.json").exists())

    @unittest.skipUnless(
        importlib.util.find_spec("playwright") and shutil.which("google-chrome"),
        "Playwright and Google Chrome are required",
    )
    def test_score_editor_browser_reviews_and_approves(self):
        from playwright.sync_api import sync_playwright

        self.run_grading()
        self.start_score_editor()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=shutil.which("google-chrome"), headless=True)
            try:
                page = browser.new_page()
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(f"{self.score_server.origin}/#{self.score_server.token}")
                page.get_by_text("Review each identity and every mark before approval.").wait_for()
                self.assertNotIn(self.score_server.token, page.url)
                for index, student in enumerate(("001", "002")):
                    page.locator("#script").select_option(str(index))
                    page.locator("#section").select_option("id")
                    page.locator("#marksheet-tbody select").nth(index).select_option(student)
                    page.get_by_text(f"Script #{index + 1} identity saved and reviewed.").wait_for()
                    page.locator("#section").select_option("0")
                    if index == 0:
                        page.get_by_text("Similarity-based mark suggestion").click()
                        page.locator("#threshold").fill(".8")
                        page.locator("#increment").fill(".25")
                        page.locator("#calculate").click()
                        page.get_by_text("Suggested mark: 1.00").wait_for()
                        self.assertEqual(page.locator("#manual").input_value(), "")
                        page.locator("#apply-suggestion").click()
                        self.assertEqual(page.locator("#manual").input_value(), "1.00")
                        page.locator("#answer").fill("Corrected answer")
                        page.locator("#manual").fill("2")
                    page.locator("#review").click()
                    page.get_by_text("Saved and marked reviewed. Any earlier approval is stale.").wait_for()
                page.locator("#approve").click()
                page.get_by_text("Approved 2 students and 2 marks.").wait_for()
                reviewed = read_approved_grading(self.config, self.root, self.exam)
                self.assertEqual(reviewed[2]["marks"][0]["answer"], "Corrected answer")
                self.assertEqual(reviewed[6][(0, "1")], 2)
            finally:
                browser.close()

    @unittest.skipUnless(
        importlib.util.find_spec("playwright") and shutil.which("google-chrome"),
        "Playwright and Google Chrome are required",
    )
    def test_marksheet_table_escapes_html_and_navigates_review(self):
        from playwright.sync_api import sync_playwright

        root = self.run_grading()
        self.fill_review(root / "review.xlsx", flagged_override=2)
        workbook = load_workbook(root / "review.xlsx")
        workbook["Marks"]["D3"] = '<img src=x onerror="window.injected=true">'
        workbook.save(root / "review.xlsx")
        workbook.close()
        before = file_hash(root / "review.xlsx")
        self.start_score_editor()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=shutil.which("google-chrome"), headless=True)
            try:
                page = browser.new_page()
                page.goto(f"{self.score_server.origin}/#{self.score_server.token}")
                page.get_by_text("Review each identity and every mark before approval.").wait_for()
                page.get_by_role("button", name="Q1").click()
                self.assertEqual(page.locator("#marksheet-tbody tr").count(), 2)
                self.assertIn('<img src=x', page.locator("#marksheet-tbody").inner_text())
                page.locator("#marksheet-tbody td strong", has_text="#2").click()
                self.assertEqual(page.locator("#script").input_value(), "1")
                self.assertEqual(page.locator("#section").input_value(), "0")
                self.assertIn("<img", page.locator("#answer").input_value())
                self.assertIsNone(page.evaluate("window.injected"))
            finally:
                browser.close()
        self.assertEqual(file_hash(root / "review.xlsx"), before)
        self.assertFalse((root / "approval.json").exists())

    @unittest.skipUnless(
        importlib.util.find_spec("playwright") and shutil.which("google-chrome"),
        "Playwright and Google Chrome are required",
    )
    def test_browser_crop_adjustment_is_only_a_visual_preview(self):
        from playwright.sync_api import sync_playwright

        root = self.run_grading()
        before = file_hash(root / "review.xlsx")
        self.start_score_editor()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=shutil.which("google-chrome"), headless=True)
            try:
                page = browser.new_page()
                page.goto(f"{self.score_server.origin}/#{self.score_server.token}")
                page.get_by_text("Review each identity and every mark before approval.").wait_for()
                page.locator("#section").select_option("0")
                page.get_by_text("Adjust crop for visual inspection").click()
                page.locator("#crop-x").fill("1")
                page.locator("#crop-y").fill("2")
                page.locator("#crop-width").fill("15")
                page.locator("#crop-height").fill("20")
                page.locator("#preview-crop").click()
                page.get_by_text("Temporary visual preview only; grading inputs are unchanged.").wait_for()
                page.wait_for_function("document.querySelector('#image').naturalWidth === 15")
            finally:
                browser.close()
        self.assertEqual(file_hash(root / "review.xlsx"), before)

    def test_preview_requires_consent_and_real_prerequisites(self):
        models = yaml.safe_load(self.models.read_text())
        models["caching"] = {"enabled": True, "scope": "shared", "agents": {"annotation": True}}
        self.models.write_text(yaml.safe_dump(models))
        arguments = [
            "grade", "--exam", str(self.exam), "--model-config", str(self.models),
            "--project", "test-project", "--location", "global",
        ]
        output = io.StringIO()
        with patch("handwriting_grader.grading.grade_exam") as run, redirect_stdout(output):
            self.assertEqual(main(arguments), 1)
            run.assert_not_called()
        self.assertIn("Cache: disabled; location=none", output.getvalue())
        self.assertIn("Up to 9 requests", output.getvalue())
        self.assertFalse((self.root / "output" / "grading").exists())

    def test_review_diagnostics_reports_multiple_issues_without_approval(self):
        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        workbook = load_workbook(root / "review.xlsx")
        workbook["Scripts"]["D3"] = "001"
        workbook["Marks"]["K2"] = None
        workbook["Marks"]["L3"] = None
        workbook.save(root / "review.xlsx")
        workbook.close()
        before = {name: file_hash(root / name) for name in ("review.xlsx", "results.json", "manifest.json")}
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(["check-grading-review", "--exam", str(self.exam)])
        self.assertEqual(result, 1)
        self.assertIn("duplicate roster ID", output.getvalue())
        self.assertIn("1 present roster ID(s) not assigned", output.getvalue())
        self.assertIn("manual mark required", output.getvalue())
        self.assertIn("mark not reviewed", output.getvalue())
        self.assertEqual(before, {name: file_hash(root / name) for name in before})
        self.assertFalse((root / "approval.json").exists())
        self.fill_review(root / "review.xlsx")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["check-grading-review", "--exam", str(self.exam)]), 0)
        self.assertIn("explicit approval", output.getvalue())
        self.assertFalse((root / "approval.json").exists())

    def test_review_diagnostics_fails_closed_for_invalid_review_and_stale_inputs(self):
        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        workbook = load_workbook(root / "review.xlsx")
        workbook["Scripts"]["D2"] = "=1+1"
        workbook.save(root / "review.xlsx")
        workbook.close()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["check-grading-review", "--exam", str(self.exam)]), 1)
        self.assertIn("cannot contain formulas", output.getvalue())
        self.assertFalse((root / "approval.json").exists())
        self.models.write_text(self.models.read_text() + "\n# changed\n")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["check-grading-review", "--exam", str(self.exam)]), 1)

    def test_review_diagnostics_finds_missing_rows_and_out_of_range_marks(self):
        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        workbook = load_workbook(root / "review.xlsx")
        workbook["Scripts"]["D3"] = "unknown"
        workbook["Marks"]["K2"] = 3
        workbook["Marks"].delete_rows(3)
        workbook.save(root / "review.xlsx")
        workbook.close()
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["check-grading-review", "--exam", str(self.exam)]), 1)
        self.assertIn("missing or unknown roster ID", output.getvalue())
        self.assertIn("mark missing or outside allowed range", output.getvalue())
        self.assertIn("missing mark row", output.getvalue())

    def test_grading_review_approval_and_input_invalidation(self):
        root = self.run_grading()
        results = json.loads((root / "results.json").read_text())
        self.assertTrue(results["completed"])
        self.assertEqual(len(results["scripts"]), 2)
        self.assertTrue(all(script["name_ocr"] == "Example name" for script in results["scripts"]))
        self.assertEqual(json.loads((root / "manifest.json").read_text())["metadata_fields"], ["ID", "CLASS", "NAME"])
        self.assertEqual(len(results["marks"]), 2)
        self.assertTrue(all(mark["similarity_score"] == 0.5 for mark in results["marks"]))
        workbook = load_workbook(root / "review.xlsx")
        try:
            self.assertEqual(workbook["Scripts"]["F2"].value, "Example name")
            self.assertEqual(workbook["Marks"]["M2"].value, 0.5)
        finally:
            workbook.close()
        self.assertTrue(results["marks"][0]["moderator_flag"])
        with self.assertRaisesRegex(ValueError, "explicit human review"):
            validate_review(self.config, self.root, self.exam)
        self.fill_review(root / "review.xlsx", flagged_override=None)
        with self.assertRaisesRegex(ValueError, "manual mark"):
            validate_review(self.config, self.root, self.exam)
        self.fill_review(root / "review.xlsx", flagged_override=3)
        with self.assertRaisesRegex(ValueError, "outside"):
            validate_review(self.config, self.root, self.exam)
        self.fill_review(root / "review.xlsx")
        self.assertEqual(approve_grading(self.config, self.root, self.exam), (2, 2))
        validated = read_approved_grading(self.config, self.root, self.exam)
        self.assertEqual(validated[6][(0, "1")], 1)
        self.models.write_text("changed")
        with self.assertRaisesRegex(ValueError, "inputs or approvals changed"):
            read_approved_grading(self.config, self.root, self.exam)

    def test_corrected_answers_require_manual_marks_and_flow_into_reports_and_analysis(self):
        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        workbook = load_workbook(root / "review.xlsx")
        marks = workbook["Marks"]
        marks["D3"] = "Corrected answer"
        marks["G3"] = "Teacher reasoning"
        workbook.save(root / "review.xlsx")
        workbook.close()
        with self.assertRaisesRegex(ValueError, "corrected answer requires a manual mark"):
            approve_grading(self.config, self.root, self.exam)
        workbook = load_workbook(root / "review.xlsx")
        workbook["Marks"]["K3"] = 2
        workbook.save(root / "review.xlsx")
        workbook.close()
        approve_grading(self.config, self.root, self.exam)
        self.assertEqual(main(["report", "--exam", str(self.exam)]), 0)
        workbook = load_workbook(root.parent / "reports" / "details_score_report.xlsx", read_only=True)
        try:
            self.assertEqual(workbook["Answers"]["D3"].value, "Corrected answer")
            self.assertEqual(workbook["Reasoning"]["D3"].value, "Teacher reasoning")
            self.assertEqual(workbook["AnswersRaw"]["C3"].value, "Example answer")
            self.assertEqual(workbook["ReasoningRaw"]["C3"].value, "Example")
            self.assertEqual(workbook["ReasoningRaw"]["F3"].value, 0.5)
            self.assertEqual(workbook["Marks"]["D3"].value, 2)
        finally:
            workbook.close()
        raw = json.loads((root / "results.json").read_text())
        self.assertEqual(raw["marks"][1]["answer"], "Example answer")
        self.assertEqual(raw["marks"][1]["reasoning"], "Example")

        model = self.root / "analysis-models.yaml"
        model.write_text(yaml.safe_dump({"vertex_ai": {"use_vertexai": True}, "models": {"analytics": "fake"}}))

        class FakeAnalyst:
            calls = []

            def __init__(self, *args):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def analyze(self, task, data):
                self.calls.append((task, data))
                return "Feedback"

        create_analysis(self.config, self.root, self.exam, model, "test-project", "global",
                        provider_factory=FakeAnalyst)
        details = FakeAnalyst.calls[1][1]["questions"][0]
        self.assertEqual(details["answer"], "Corrected answer")
        self.assertEqual(details["reviewed_reasoning"], "Teacher reasoning")
        self.assertEqual(details["reviewed_mark"], 2)

    def test_cleared_answer_requires_manual_mark(self):
        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        workbook = load_workbook(root / "review.xlsx")
        workbook["Marks"]["D3"] = None
        workbook.save(root / "review.xlsx")
        workbook.close()
        with self.assertRaisesRegex(ValueError, "corrected answer requires a manual mark"):
            validate_review(self.config, self.root, self.exam)
        workbook = load_workbook(root / "review.xlsx")
        workbook["Marks"]["K3"] = 0
        workbook.save(root / "review.xlsx")
        workbook.close()
        result = validate_review(self.config, self.root, self.exam)
        self.assertEqual(result[2]["marks"][1]["answer"], "")

    def test_excel_review_rejects_text_that_would_be_truncated(self):
        workbook = Workbook()
        try:
            with self.assertRaisesRegex(ValueError, "32,767-character"):
                safe_append(workbook.active, ["a" * 32768])
        finally:
            workbook.close()

    def test_failed_grading_is_not_accepted_as_zero(self):
        class FailingGrader(FakeGrader):
            def grade(self, question, answer):
                raise RuntimeError("Model unavailable")
        root = self.run_grading(FailingGrader)
        rows = json.loads((root / "results.json").read_text())["marks"]
        self.assertTrue(all(row["grading_failed"] and row["raw_mark"] is None for row in rows))
        self.fill_review(root / "review.xlsx")
        with self.assertRaisesRegex(ValueError, "manual mark"):
            validate_review(self.config, self.root, self.exam)

    def test_interrupted_grading_resumes_without_repeating_checkpointed_marks(self):
        class InterruptedGrader(FakeGrader):
            calls = 0

            def grade(self, question, answer):
                type(self).calls += 1
                if self.calls == 2:
                    raise KeyboardInterrupt()
                return super().grade(question, answer)

        with self.assertRaises(KeyboardInterrupt):
            self.run_grading(InterruptedGrader)
        root = self.root / "output" / "grading"
        partial = json.loads((root / "results.json").read_text())
        self.assertEqual(len(partial["scripts"]), 2)
        self.assertEqual(len(partial["marks"]), 1)
        self.assertFalse((root / "processing.lock").exists())
        with patch("handwriting_grader.grading.resume_grading") as resume:
            self.assertEqual(main(["resume-grading", "--exam", str(self.exam)]), 1)
            resume.assert_not_called()

        class ResumingGrader(FakeGrader):
            calls = 0

            def grade(self, question, answer):
                type(self).calls += 1
                return super().grade(question, answer)

        resume_grading(self.config, self.root, self.exam, self.summary, provider_factory=ResumingGrader)
        self.assertEqual(ResumingGrader.calls, 1)
        self.assertTrue(json.loads((root / "results.json").read_text())["completed"])
        self.assertTrue((root / "review.xlsx").is_file())
        with self.assertRaisesRegex(ValueError, "already exists"):
            resume_grading(self.config, self.root, self.exam, self.summary, provider_factory=ResumingGrader)

    def test_interrupted_grading_refuses_changed_inputs_and_stale_lock(self):
        class InterruptedGrader(FakeGrader):
            def grade(self, question, answer):
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_grading(InterruptedGrader)
        root = self.root / "output" / "grading"
        (root / "processing.lock").write_text("stale")
        with self.assertRaisesRegex(ValueError, "lock"):
            resume_grading(self.config, self.root, self.exam, self.summary, provider_factory=FakeGrader)
        (root / "processing.lock").unlink()
        self.models.write_text("changed")
        with self.assertRaisesRegex(ValueError, "inputs or approvals changed"):
            resume_grading(self.config, self.root, self.exam, self.summary, provider_factory=FakeGrader)

    def test_legacy_checkpoint_resume_does_not_add_name_ocr_calls(self):
        class InterruptedGrader(FakeGrader):
            ids_seen = 0

            def ocr(self, image, field):
                if field == "ID":
                    type(self).ids_seen += 1
                    if self.ids_seen == 2:
                        raise KeyboardInterrupt()
                return super().ocr(image, field)

        with self.assertRaises(KeyboardInterrupt):
            self.run_grading(InterruptedGrader)
        root = self.root / "output" / "grading"
        manifest = json.loads((root / "manifest.json").read_text())
        del manifest["metadata_fields"]
        atomic_json(root / "manifest.json", manifest)
        results = json.loads((root / "results.json").read_text())
        results["scripts"][0].pop("name_ocr")
        atomic_json(root / "results.json", results)

        class ResumingGrader(FakeGrader):
            fields = []

            def ocr(self, image, field):
                type(self).fields.append(field)
                return super().ocr(image, field)

        resume_grading(self.config, self.root, self.exam, self.summary, provider_factory=ResumingGrader)
        self.assertNotIn("NAME", ResumingGrader.fields)
        self.start_score_editor()
        state = json.loads(self.score_request("/api/state")[1])
        self.assertTrue(all(script["name_ocr"] is None for script in state["scripts"]))

    def test_rubric_and_regions_must_match(self):
        region_dir = self.root / "output" / "regions"
        draft = json.loads((region_dir / "draft.json").read_text())
        draft["annotations"]["0"][-1]["label"] = "2"
        atomic_json(region_dir / "draft.json", draft)
        atomic_json(region_dir / "annotations.json", draft["annotations"])
        approval = json.loads((region_dir / "approval.json").read_text())
        approval["annotations_sha256"] = file_hash(region_dir / "annotations.json")
        atomic_json(region_dir / "approval.json", approval)
        with self.assertRaisesRegex(ValueError, "do not match"):
            approved_inputs(self.config, self.root, self.exam)

    def test_reports_require_approval_and_use_reviewed_marks(self):
        root = self.run_grading()
        self.fill_review(root / "review.xlsx", flagged_override=2)
        arguments = ["report", "--exam", str(self.exam)]
        self.assertEqual(main(arguments), 1)
        self.assertFalse((root.parent / "reports").exists())
        approve_grading(self.config, self.root, self.exam)
        image_hash = file_hash(root / "images" / "0.jpg")
        self.assertEqual(main(arguments), 0)
        reports = root.parent / "reports"
        book = load_workbook(reports / "score_report.xlsx", read_only=True)
        try:
            rows = list(book["Marks"].values)
            self.assertEqual(rows[1], ("001", "One", "A", 2, "pdfs/script-00001.pdf"))
            self.assertEqual(rows[2][3], 1)
        finally:
            book.close()
        book = load_workbook(reports / "details_score_report.xlsx", read_only=True)
        try:
            self.assertEqual(book.sheetnames, ["Marks", "Answers", "Reasoning", "AnswersRaw", "ReasoningRaw"])
            self.assertEqual(book["Marks"]["D2"].value, 2)
            self.assertEqual(book["Answers"]["D2"].value, "Example answer")
        finally:
            book.close()
        self.assertEqual(len(PdfReader(reports / "pdfs" / "script-00001.pdf").pages), 2)
        self.assertEqual(len(PdfReader(reports / "all.pdf").pages), 4)
        self.assertEqual(file_hash(root / "images" / "0.jpg"), image_hash)
        self.assertTrue(json.loads((reports / "manifest.json").read_text())["completed"])
        self.assertEqual(main(arguments), 1)

    def test_stale_review_cannot_generate_reports(self):
        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        approve_grading(self.config, self.root, self.exam)
        self.fill_review(root / "review.xlsx", flagged_override=2)
        self.assertEqual(main(["report", "--exam", str(self.exam)]), 1)
        self.assertFalse((root.parent / "reports").exists())

    def test_analytics_uses_only_approved_reviewed_scores(self):
        root = self.run_grading()
        self.fill_review(root / "review.xlsx", flagged_override=2)
        self.assertEqual(main(["analytics", "--exam", str(self.exam)]), 1)
        self.assertFalse((root.parent / "analytics").exists())
        approve_grading(self.config, self.root, self.exam)
        values = []
        boxplot = Axes.boxplot

        def capture(axis, data, **kwargs):
            values.append(data)
            return boxplot(axis, data, **kwargs)

        with patch.object(Axes, "boxplot", autospec=True, side_effect=capture):
            self.assertEqual(main(["analytics", "--exam", str(self.exam)]), 0)
        self.assertEqual(values, [[[2, 1]]])
        output = root.parent / "analytics"
        book = load_workbook(output / "statistics.xlsx", read_only=True)
        try:
            overall = list(book["Overall"].values)[1]
            self.assertEqual(overall[:3], (2, 1.5, 1.5))
            distribution = list(book["Distribution"].values)
            self.assertEqual(distribution[6][1], 1)
            self.assertEqual(distribution[10][1], 1)
            self.assertEqual(overall[6], 2)
            self.assertIsNone(overall[9])
            question = list(book["Questions"].values)[1]
            self.assertEqual((question[0], question[2], question[7], question[8]), ("Q1", 1.5, 2, 75))
            self.assertEqual(list(book["Classes"].values)[1][:3], ("A", 2, 1.5))
        finally:
            book.close()
        book = load_workbook(output / "statistics.xlsx")
        try:
            self.assertEqual(len(book["Questions"]._charts), 1)
            self.assertEqual(len(book["Distribution"]._charts), 1)
        finally:
            book.close()
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertTrue(manifest["completed"])
        chart = manifest["question_boxplots"][0]
        self.assertEqual(chart["sha256"], file_hash(output / chart["path"]))
        with Image.open(output / chart["path"]) as image:
            self.assertEqual(image.format, "PNG")
            self.assertGreater(image.width, 100)
        self.assertEqual(main(["analytics", "--exam", str(self.exam)]), 1)

    def test_question_boxplots_handle_single_student_and_multiple_pages(self):
        questions = [{"question_number": f"Q{i}"} for i in range(21)]
        rows = [{"marks": list(range(21))}]
        paths = question_boxplots(self.root, rows, questions)
        self.assertEqual(paths, ["question-boxplots-001.png", "question-boxplots-002.png"])
        for path in paths:
            with Image.open(self.root / path) as image:
                self.assertEqual(image.format, "PNG")
                image.verify()

    def test_question_pass_thresholds_are_explicit_and_unambiguous(self):
        questions = [{"question_number": "Q1", "marks": 2}, {"question_number": "Q2", "marks": 3}]
        self.assertEqual(question_passing_policy({}, questions), ({}, None))
        config = {"reporting": {"question_pass_marks": {"Q1": 1.0}, "question_passing_includes_equal": True}}
        self.assertEqual(question_passing_policy(config, questions), ({"1": 1.0}, True))
        for invalid in (
            {"question_pass_marks": {"Q1": 1}},
            {"question_pass_marks": {"Q3": 1}, "question_passing_includes_equal": True},
            {"question_pass_marks": {"Q1": 3}, "question_passing_includes_equal": True},
            {"question_pass_marks": {"Q1": 1, "1": 1}, "question_passing_includes_equal": True},
        ):
            with self.assertRaises(ValueError):
                question_passing_policy({"reporting": invalid}, questions)

    def test_question_pass_rates_use_approved_marks_and_explicit_equality(self):
        self.config["reporting"] = {"question_pass_marks": {"Q1": 1},
                                    "question_passing_includes_equal": False}
        self.config["exam"]["output_dir"] = "policy-output"
        self.exam.write_text(yaml.safe_dump(self.config))
        import_rubric(self.config, self.root, self.exam)
        approve_rubric(self.config, self.root, self.exam)
        regions = prepare_regions(self.config, self.root, 2)
        _, manifest = verify_run(self.config, self.root, self.exam)
        draft = json.loads((self.root / "output" / "regions" / "draft.json").read_text())
        atomic_json(regions / "draft.json", draft)
        atomic_json(regions / "annotations.json", draft["annotations"])
        atomic_json(regions / "approval.json", {
            "annotations_sha256": file_hash(regions / "annotations.json"),
            "config_sha256": manifest["config_sha256"], "pdf_sha256": manifest["pdf_sha256"],
            "revision": draft["revision"],
        })
        root = self.run_grading()
        self.fill_review(root / "review.xlsx", flagged_override=2)
        approve_grading(self.config, self.root, self.exam)
        self.assertEqual(main(["analytics", "--exam", str(self.exam)]), 0)
        book = load_workbook(root.parent / "analytics" / "statistics.xlsx", read_only=True)
        try:
            question = list(book["Questions"].values)[1]
            self.assertEqual(question[9:], (1, False, 1, 50))
        finally:
            book.close()

    def test_analysis_cache_reuses_reviewed_inputs(self):
        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        approve_grading(self.config, self.root, self.exam)
        model = self.root / "analysis-models.yaml"
        model.write_text(yaml.safe_dump({
            "vertex_ai": {"use_vertexai": True}, "models": {"analytics": "test-model"},
            "caching": {"enabled": True, "scope": "shared", "directory": "shared",
                        "agents": {"analytics": True}},
        }))

        class FakeAnalyst:
            calls = []

            def __init__(self, *args):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def analyze(self, task, data):
                self.calls.append((task, data))
                return "Feedback"

        output = create_analysis(self.config, self.root, self.exam, model, "test-project", "global",
                                 provider_factory=FakeAnalyst)
        calls = len(FakeAnalyst.calls)
        self.assertGreater(calls, 0)
        output.rename(root.parent / "previous-analysis")
        create_analysis(self.config, self.root, self.exam, model, "test-project", "global",
                        provider_factory=FakeAnalyst)
        self.assertEqual(len(FakeAnalyst.calls), calls)

    def test_sampling_and_explicit_passing_policy(self):
        rows = [{"script_index": i, "total": i} for i in range(10)]
        selected = sample_rows(rows, 3)
        self.assertEqual([row["total"] for row in selected], [9, 8, 7, 6, 5, 4, 2, 1, 0])
        self.assertEqual(sample_rows(rows[:2], 3), [])
        questions = [{"marks": 10}]
        self.assertIsNone(passing_policy({}, questions))
        self.assertEqual(passing_policy({"reporting": {"pass_mark": 5, "passing_includes_equal": True}}, questions),
                         (5, True))
        self.assertEqual([row["total"] for row in passing_rows(rows, 5, True)], [5, 6, 7, 8, 9])
        self.assertEqual([row["total"] for row in passing_rows(rows, 5, False)], [6, 7, 8, 9])
        with self.assertRaisesRegex(ValueError, "pass_mark"):
            passing_policy({"reporting": {"pass_mark": 5}}, questions)

    def test_report_text_is_not_written_as_formulas(self):
        rows = [{"script_index": 0, "id": "=1+1", "name": "=2+2", "class": "A",
                 "marks": [1], "total": 1, "pdf": "pdfs/script-00001.pdf"}]
        questions = [{"question_number": "=Q", "marks": 2}]
        baselines = {(0, "=Q"): {"answer": "=3+3", "reasoning": "=4+4",
                                 "moderator_flag": False, "moderator_note": ""}}
        write_workbooks(self.root, rows, baselines, questions)
        workbook = load_workbook(self.root / "details_score_report.xlsx", data_only=False)
        try:
            for sheet, cell in [("Marks", "D1"), ("Marks", "A2"), ("Answers", "D2"),
                                ("Reasoning", "D2")]:
                self.assertEqual(workbook[sheet][cell].data_type, "s")
        finally:
            workbook.close()

    def test_vertex_sdk_grading_requests_against_fake_service(self):
        from google.genai import Client
        from google.oauth2.credentials import Credentials

        calls = []

        class FakeVertex(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, request))
                if "moderate-model" in self.path:
                    text = json.dumps({"items": [
                        {"script_index": i, "moderated_mark": 1, "flag": False, "note": ""}
                        for i in range(2)
                    ]})
                elif "grade-model" in self.path:
                    text = json.dumps({"answer": "Example answer", "mark": 1, "similarity_score": 0.5, "reasoning": "Example"})
                else:
                    text = "Example"
                body = json.dumps({"candidates": [{
                    "content": {"role": "model", "parts": [{"text": text}]},
                    "finishReason": "STOP",
                }]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        service = ThreadingHTTPServer(("127.0.0.1", 0), FakeVertex)
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        try:
            def local_client(**kwargs):
                kwargs["http_options"].base_url = f"http://127.0.0.1:{service.server_port}"
                return Client(**kwargs, credentials=Credentials(token="local-test-token"))

            with patch("handwriting_grader.grading.genai.Client", side_effect=local_client):
                root = self.run_grading(VertexGrader)
            results = json.loads((root / "results.json").read_text())
            self.assertTrue(results["completed"])
            self.assertTrue(all(not row["grading_failed"] for row in results["marks"]))
            self.assertEqual(len(calls), 9)
            self.assertTrue(all(script["name_ocr"] == "Example" for script in results["scripts"]))
            image_request = calls[0][1]
            self.assertTrue(any("inlineData" in part for content in image_request["contents"]
                                for part in content["parts"]))
            grade_request = next(body for path, body in calls if "grade-model" in path)
            self.assertEqual(grade_request["generationConfig"]["responseMimeType"], "application/json")
            self.assertIn("responseSchema", grade_request["generationConfig"])
        finally:
            service.shutdown()
            thread.join(timeout=5)
            service.server_close()

    def prepare_email(self):
        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        approve_grading(self.config, self.root, self.exam)
        self.assertEqual(main(["report", "--exam", str(self.exam)]), 0)
        settings = self.root / "email-settings.yaml"
        settings.write_text(yaml.safe_dump({
            "sender": "teacher@example.test", "smtp_host": "smtp.example.test", "smtp_port": 587,
            "smtp_username": "teacher", "subject": "{exam} result",
            "body": "Dear {name}, your score is {marks}.",
        }))
        recipients = self.root / "recipients.csv"
        recipients.write_text("ID,EMAIL\n001,one@example.test\n002,two@example.test\n")
        return root, settings, recipients

    def test_email_preview_approval_and_single_send(self):
        root, settings, recipients = self.prepare_email()
        preview = ["preview-email", "--exam", str(self.exam), "--settings", str(settings),
                   "--recipients", str(recipients)]
        approve = ["approve-email", "--exam", str(self.exam)]
        send = ["send-email", "--exam", str(self.exam)]
        with patch("handwriting_grader.distribution.smtplib.SMTP") as smtp, \
             patch.dict(os.environ, {"HANDWRITING_SMTP_PASSWORD": "fake-secret"}):
            server = smtp.return_value.__enter__.return_value
            server.sendmail.return_value = {}
            self.assertEqual(main(preview), 0)
            self.assertEqual(main(approve), 1)
            self.assertEqual(main(send), 1)
            self.assertEqual(main(send + ["--confirm-send"]), 1)
            smtp.assert_not_called()
            self.assertEqual(main(approve + ["--confirm-reviewed"]), 0)
            self.assertEqual(main(send + ["--confirm-send"]), 0)
            self.assertEqual(server.sendmail.call_count, 2)
            self.assertEqual(server.sendmail.call_args_list[0].args[1], ["one@example.test"])
            self.assertIn(b"application/pdf", server.sendmail.call_args_list[0].args[2])
            self.assertEqual(main(send + ["--confirm-send"]), 1)
            self.assertEqual(server.sendmail.call_count, 2)
        delivery = json.loads((root.parent / "email" / "delivery.json").read_text())
        self.assertEqual(delivery["status"], "completed")
        self.assertEqual(len(delivery["sent"]), 2)

    def test_email_refuses_changed_recipients_or_attachment(self):
        root, settings, recipients = self.prepare_email()
        self.assertEqual(main(["preview-email", "--exam", str(self.exam), "--settings", str(settings),
                               "--recipients", str(recipients)]), 0)
        recipients.write_text("ID,EMAIL\n001,wrong@example.test\n002,two@example.test\n")
        self.assertEqual(main(["approve-email", "--exam", str(self.exam), "--confirm-reviewed"]), 1)
        self.assertFalse((root.parent / "email" / "approval.json").exists())
        recipients.write_text("ID,EMAIL\n001,one@example.test\n002,two@example.test\n")
        (root.parent / "reports" / "pdfs" / "script-00001.pdf").write_bytes(b"changed")
        self.assertEqual(main(["approve-email", "--exam", str(self.exam), "--confirm-reviewed"]), 1)

    def test_email_delivery_failure_is_not_retried_automatically(self):
        root, settings, recipients = self.prepare_email()
        self.assertEqual(main(["preview-email", "--exam", str(self.exam), "--settings", str(settings),
                               "--recipients", str(recipients)]), 0)
        self.assertEqual(main(["approve-email", "--exam", str(self.exam), "--confirm-reviewed"]), 0)
        with patch("handwriting_grader.distribution.smtplib.SMTP") as smtp, \
             patch.dict(os.environ, {"HANDWRITING_SMTP_PASSWORD": "fake-secret"}):
            server = smtp.return_value.__enter__.return_value
            server.sendmail.side_effect = OSError("ambiguous response")
            args = ["send-email", "--exam", str(self.exam), "--confirm-send"]
            self.assertEqual(main(args), 1)
            self.assertEqual(main(args), 1)
            self.assertEqual(server.sendmail.call_count, 1)
        journal = json.loads((root.parent / "email" / "delivery.json").read_text())
        self.assertEqual(journal["in_flight"], "message-00001.eml")

    def test_ai_analysis_requires_review_and_generates_office_reports(self):
        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        approve_grading(self.config, self.root, self.exam)
        settings = yaml.safe_load(self.models.read_text())
        settings["models"]["analytics"] = "analysis-model"
        # Model configuration is bound to the grading run, so provide separate settings for analytics.
        analytics_models = self.root / "analytics-models.yaml"
        analytics_models.write_text(yaml.safe_dump(settings))
        arguments = ["ai-analysis", "--exam", str(self.exam), "--model-config", str(analytics_models),
                     "--project", "test-project", "--location", "global"]
        with patch("handwriting_grader.advanced.create_analysis") as create:
            self.assertEqual(main(arguments), 1)
            create.assert_not_called()

        class FakeAnalyst:
            calls = []

            def __init__(self, model, project, location):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

            def analyze(self, task, data):
                self.calls.append((task, data))
                return "Reviewed feedback."

        output = create_analysis(self.config, self.root, self.exam, analytics_models, "test-project", "global",
                                 provider_factory=FakeAnalyst)
        self.assertEqual(len(FakeAnalyst.calls), 4)
        self.assertEqual(main(["report", "--exam", str(self.exam)]), 0)
        email_settings = self.root / "email-settings.yaml"
        email_settings.write_text(yaml.safe_dump({
            "sender": "teacher@example.test", "smtp_host": "smtp.example.test", "smtp_port": 587,
            "smtp_username": "teacher", "subject": "{exam} result", "body": "Hello {name}\n{feedback}",
        }))
        recipients = self.root / "recipients.csv"
        recipients.write_text("ID,EMAIL\n001,one@example.test\n002,two@example.test\n")
        preview = ["preview-email", "--exam", str(self.exam), "--settings", str(email_settings),
                   "--recipients", str(recipients)]
        self.assertEqual(main(preview), 1)
        self.assertEqual(main(["approve-analysis", "--exam", str(self.exam), "--confirm-reviewed"]), 1)
        book = load_workbook(output / "review.xlsx")
        for sheet in ("Students", "Questions", "Class"):
            for row in book[sheet].iter_rows(min_row=2):
                row[-1].value = "yes"
        book.save(output / "review.xlsx")
        book.close()
        self.assertEqual(main(["approve-analysis", "--exam", str(self.exam), "--confirm-reviewed"]), 0)
        self.assertEqual(main(preview), 0)
        mail = json.loads((root.parent / "email" / "preview.json").read_text())
        self.assertIn("Reviewed feedback.", mail["messages"][0]["body"])
        self.assertEqual(main(["approve-email", "--exam", str(self.exam), "--confirm-reviewed"]), 0)
        settings["models"]["analytics_image"] = "image-model"
        image_models = self.root / "image-models.yaml"
        image_models.write_text(yaml.safe_dump(settings))
        image_args = ["infographics", "--exam", str(self.exam), "--model-config", str(image_models),
                      "--project", "test-project", "--location", "global"]
        with patch("handwriting_grader.advanced.create_infographics") as create:
            self.assertEqual(main(image_args), 1)
            create.assert_not_called()
        from google.genai import Client
        from google.oauth2.credentials import Credentials
        calls = []

        class FakeImageVertex(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                image = Image.new("RGB", (100, 60), "white")
                buffer = io.BytesIO()
                image.save(buffer, "PNG")
                image.close()
                text = base64.b64encode(buffer.getvalue()).decode()
                body = json.dumps({"candidates": [{
                    "content": {"role": "model", "parts": [{"inlineData": {"mimeType": "image/png", "data": text}}]},
                    "finishReason": "STOP",
                }]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        service = ThreadingHTTPServer(("127.0.0.1", 0), FakeImageVertex)
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        try:
            def local_client(**kwargs):
                kwargs["http_options"].base_url = f"http://127.0.0.1:{service.server_port}"
                return Client(**kwargs, credentials=Credentials(token="local-test-token"))
            with patch("handwriting_grader.advanced.genai.Client", side_effect=local_client):
                self.assertEqual(main(image_args + ["--confirm-cost"]), 0)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]["generationConfig"]["responseModalities"], ["IMAGE"])
        finally:
            service.shutdown()
            thread.join(timeout=5)
            service.server_close()
        self.assertEqual(main(["advanced-report", "--exam", str(self.exam), "--include-infographics"]), 1)
        self.assertEqual(main(["advanced-report", "--exam", str(self.exam), "--include-infographics",
                               "--confirm-images-reviewed"]), 0)
        report = output / "reports"
        self.assertTrue(any("Reviewed feedback." in p.text for p in Document(report / "teacher-report.docx").paragraphs))
        self.assertEqual(len(Presentation(report / "class-presentation.pptx").slides), 5)
        self.assertTrue(json.loads((report / "manifest.json").read_text())["completed"])
        book = load_workbook(output / "review.xlsx")
        book["Class"]["A2"] = "Changed"
        book.save(output / "review.xlsx")
        book.close()
        self.assertEqual(main(["advanced-report", "--exam", str(self.exam)]), 1)
        self.assertEqual(main(["send-email", "--exam", str(self.exam), "--confirm-send"]), 1)

    def test_vertex_sdk_ai_analysis_against_local_fake_service(self):
        from google.genai import Client
        from google.oauth2.credentials import Credentials

        calls = []

        class FakeVertex(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = json.dumps({"candidates": [{
                    "content": {"role": "model", "parts": [{"text": "Feedback."}]},
                    "finishReason": "STOP",
                }]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        approve_grading(self.config, self.root, self.exam)
        settings = yaml.safe_load(self.models.read_text())
        settings["models"]["analytics"] = "analysis-model"
        model = self.root / "analytics-models.yaml"
        model.write_text(yaml.safe_dump(settings))
        service = ThreadingHTTPServer(("127.0.0.1", 0), FakeVertex)
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        try:
            def local_client(**kwargs):
                kwargs["http_options"].base_url = f"http://127.0.0.1:{service.server_port}"
                return Client(**kwargs, credentials=Credentials(token="local-test-token"))

            with patch("handwriting_grader.advanced.genai.Client", side_effect=local_client):
                output = create_analysis(self.config, self.root, self.exam, model, "test-project", "global")
            self.assertEqual(len(calls), 4)
            self.assertIn("Example answer", json.dumps(calls[0]))
            self.assertTrue((output / "review.xlsx").is_file())
        finally:
            service.shutdown()
            thread.join(timeout=5)
            service.server_close()


if __name__ == "__main__":
    unittest.main()
