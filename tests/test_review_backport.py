"""Provider-neutral review, export and workflow regression cases."""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml
from openpyxl import load_workbook
from pptx import Presentation

import test_grading
from handwriting_grader.advanced import approve_analysis, create_analysis, create_advanced_report
from handwriting_grader.cli import main
from handwriting_grader.grade_review import approve_grading, read_approved_grading
from handwriting_grader.regions import file_hash


class ReviewBackportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_grading.GradingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def __getattr__(self, name):
        fixture = self.__dict__.get("fixture")
        if fixture is None:
            raise AttributeError(name)
        return getattr(fixture, name)

    def prepare_analysis(self):
        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        approve_grading(self.config, self.root, self.exam)
        settings = yaml.safe_load(self.models.read_text())
        settings["models"]["analytics"] = "gemini-4-argon"
        settings["models"]["analytics_image"] = "gemini-3-pro-image"
        path = self.root / "analysis-models.yaml"
        path.write_text(yaml.safe_dump(settings))

        class Analyst:
            def __init__(self, *args):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def analyze(self, task, data):
                return "Complete reviewed analysis. " * 100

        output = create_analysis(self.config, self.root, self.exam, path, "test-project", "global",
                                 provider_factory=Analyst)
        book = load_workbook(output / "review.xlsx")
        for sheet in book:
            for row in sheet.iter_rows(min_row=2):
                row[-1].value = "yes"
        book.save(output / "review.xlsx")
        book.close()
        approve_analysis(self.config, self.root, self.exam)
        return output

    def test_embedding_preview_and_stale_revision_make_no_provider_calls(self):
        root = self.run_grading()
        before = file_hash(root / "review.xlsx")
        settings = self.root / "embedding.yaml"
        settings.write_text(yaml.safe_dump({
            "vertex_ai": {"use_vertexai": True, "project_id": "test-project", "location": "global"},
            "models": {"embedding": "gemini-embedding-2"},
            "caching": {"enabled": False},
        }))
        arguments = ["cluster-answers", "--exam", str(self.exam), "--model-config", str(settings)]
        with patch("google.genai.Client", side_effect=AssertionError("No provider call allowed")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(main(arguments + ["--preview-embedding"]), 0)
            self.assertEqual(main(arguments + ["--confirm-cost", "--revision", "stale"]), 1)
        self.assertEqual(file_hash(root / "review.xlsx"), before)
        self.assertFalse((root / "clusters.json").exists())

    def test_embedding_consent_is_bound_to_the_selected_configuration(self):
        from handwriting_grader.score_editor import ScoreServer, load_state

        self.run_grading()
        settings = self.root / "embedding.yaml"
        data = {"vertex_ai": {"use_vertexai": True, "project_id": "first-project", "location": "global"},
                "models": {"embedding": "gemini-embedding-2"}}
        settings.write_text(yaml.safe_dump(data))
        with ScoreServer(self.config, self.root, config_path=self.exam,
                         embedding_model_config=settings) as server:
            revision = server.embedding_preview_revision(load_state(server)[1])
        data["vertex_ai"]["project_id"] = "different-project"
        settings.write_text(yaml.safe_dump(data))
        with patch("google.genai.Client", side_effect=AssertionError("Consent is stale")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(main(["cluster-answers", "--exam", str(self.exam),
                                   "--model-config", str(settings), "--confirm-cost",
                                   "--revision", revision]), 1)

    def test_consented_clustering_uses_native_vertex_and_records_unknown_usage(self):
        from handwriting_grader.score_editor import ScoreServer, load_state

        root = self.run_grading()
        settings = self.root / "embedding.yaml"
        shared = self.root / "private-embeddings"
        settings.write_text(yaml.safe_dump({
            "vertex_ai": {"use_vertexai": True, "project_id": "test-project", "location": "global"},
            "models": {"embedding": "gemini-embedding-2"},
            "caching": {"enabled": True, "scope": "shared", "directory": str(shared)},
        }))
        with ScoreServer(self.config, self.root, config_path=self.exam,
                         embedding_model_config=settings) as server:
            revision = server.embedding_preview_revision(load_state(server)[1])
        self.assertFalse(shared.exists())
        client = Mock()
        client.models.embed_content.return_value = SimpleNamespace(
            embeddings=[SimpleNamespace(values=[1.0, 0.0])],
        )
        with patch("google.genai.Client", return_value=client) as create, redirect_stdout(io.StringIO()):
            self.assertEqual(main(["cluster-answers", "--exam", str(self.exam),
                                   "--model-config", str(settings), "--confirm-cost",
                                   "--revision", revision]), 0)
        create.assert_called_once_with(vertexai=True, project="test-project", location="global")
        client.close.assert_called_once()
        if hasattr(os, "getuid"):
            self.assertEqual((shared / "embeddings").stat().st_mode & 0o077, 0)
        self.assertTrue((root / "clusters.json").is_file())
        traces = [json.loads(line) for line in (root / "telemetry" / "gemini_usage.jsonl").read_text().splitlines()]
        self.assertTrue(traces)
        self.assertTrue(all(trace["cost_usd"] is None for trace in traces))

    @unittest.skipUnless(hasattr(os, "getuid"), "POSIX permissions are required")
    def test_existing_public_embedding_cache_is_rejected_without_inference(self):
        from handwriting_grader.score_editor import ScoreServer

        self.run_grading()
        directory = self.root / "shared" / "embeddings"
        directory.mkdir(parents=True)
        directory.chmod(0o755)
        settings = self.root / "embedding.yaml"
        settings.write_text(yaml.safe_dump({
            "vertex_ai": {"use_vertexai": True, "project_id": "test-project", "location": "global"},
            "models": {"embedding": "gemini-embedding-2"},
            "caching": {"enabled": True, "scope": "shared", "directory": str(directory.parent)},
        }))
        with patch("google.genai.Client") as create, self.assertRaisesRegex(ValueError, "private"):
            ScoreServer(self.config, self.root, config_path=self.exam, embedding_model_config=settings)
        create.assert_not_called()

    def test_offline_report_variants_preserve_full_analysis_notes(self):
        root = self.prepare_analysis()
        with patch("handwriting_grader.advanced.genai.Client",
                   side_effect=AssertionError("Offline reports cannot invoke a provider")):
            first = create_advanced_report(self.config, self.root, self.exam)
            second = create_advanced_report(self.config, self.root, self.exam, report_name="reports-text-v2")
        self.assertTrue((first / "teacher-report.docx").is_file())
        self.assertTrue((second / "class-presentation.pptx").is_file())
        deck = Presentation(second / "class-presentation.pptx")
        self.assertGreater(len(deck.slides[0].notes_slide.notes_text_frame.text), 1800)
        self.assertIn("Reviewed marks:", "\n".join(shape.text for shape in deck.slides[0].shapes
                                                  if shape.has_text_frame))
        with self.assertRaisesRegex(ValueError, "already exist"):
            create_advanced_report(self.config, self.root, self.exam)
        self.assertTrue((root / "approval.json").is_file())

    def test_image_preview_preserves_approved_configuration_and_creates_no_report(self):
        root = self.prepare_analysis()
        config_hash = file_hash(self.root / "analysis-models.yaml")
        with patch("handwriting_grader.advanced.genai.Client",
                   side_effect=AssertionError("Image preview cannot invoke a provider")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(main(["advanced-report", "--exam", str(self.exam), "--infographics",
                                   "--report-name", "reports-images"]), 1)
        self.assertFalse((root / "reports-images").exists())
        self.assertEqual(file_hash(self.root / "analysis-models.yaml"), config_hash)

    def test_report_names_cannot_escape_analysis_directory(self):
        self.prepare_analysis()
        with self.assertRaisesRegex(ValueError, "Report name"):
            create_advanced_report(self.config, self.root, self.exam, report_name="../outside")

    @unittest.skipUnless(importlib.util.find_spec("playwright") and shutil.which("google-chrome"),
                         "Playwright and Google Chrome are required")
    def test_group_marks_and_save_aware_keyboard_preserve_teacher_review(self):
        from playwright.sync_api import expect, sync_playwright

        root = self.run_grading()
        book = load_workbook(root / "review.xlsx")
        book["Marks"]["D2"] = book["Marks"]["D3"] = "Same answer"
        book["Marks"]["K2"] = book["Marks"]["K3"] = 1
        book.save(root / "review.xlsx")
        book.close()
        self.fill_review(root / "review.xlsx")
        approve_grading(self.config, self.root, self.exam)
        self.start_score_editor()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=shutil.which("google-chrome"), headless=True)
            try:
                page = browser.new_page()
                response = page.goto(f"{self.score_server.origin}/#{self.score_server.token}")
                self.assertNotIn("'unsafe-eval'", response.headers["content-security-policy"])
                self.assert_browser_status(page, "Review each identity and every mark before approval.")
                page.get_by_role("button", name="Q1").click()
                page.get_by_role("button", name="Apply group mark").wait_for()
                page.get_by_placeholder("Group mark").fill("0.5")
                page.get_by_role("button", name="Apply group mark").click()
                page.locator("#kb-confirm-ok").click()
                self.assert_browser_status(page, "Saved 2 group marks. Review each affected answer.")
                book = load_workbook(root / "review.xlsx")
                try:
                    self.assertEqual([book["Marks"][f"K{row}"].value for row in (2, 3)], [0.5, 0.5])
                    self.assertTrue(all(book["Marks"][f"L{row}"].value is None for row in (2, 3)))
                finally:
                    book.close()
                with self.assertRaises(ValueError):
                    read_approved_grading(self.config, self.root, self.exam)
                first, second = page.locator("#inline-mark-0"), page.locator("#inline-mark-1")
                first.fill("1.25")
                first.press("ArrowDown")
                expect(second).to_be_focused()
                self.assert_browser_status(page, "Script #1 saved and reviewed.")
                second.fill("9")
                second.press("ArrowUp")
                expect(second).to_be_focused()
                book = load_workbook(root / "review.xlsx")
                try:
                    self.assertEqual(book["Marks"]["K2"].value, 1.25)
                    self.assertEqual(book["Marks"]["K3"].value, 0.5)
                finally:
                    book.close()
            finally:
                browser.close()

    def test_infographic_cache_is_bound_to_the_image_configuration(self):
        from PIL import Image
        from unittest.mock import MagicMock

        self.prepare_analysis()
        image = io.BytesIO()
        Image.new("RGB", (160, 90), "white").save(image, format="PNG")
        client = MagicMock()
        client.__enter__.return_value = client
        client.models.generate_content.return_value = SimpleNamespace(candidates=[
            SimpleNamespace(content=SimpleNamespace(parts=[
                SimpleNamespace(inline_data=SimpleNamespace(data=image.getvalue())),
            ])),
        ])
        image_settings = self.root / "image.yaml"
        image_settings.write_text(yaml.safe_dump({
            "vertex_ai": {"use_vertexai": True, "project_id": "image-project", "location": "global"},
            "models": {"analytics_image": "gemini-3.1-flash-image"},
        }))
        with patch("handwriting_grader.advanced.genai.Client", return_value=client) as create, \
                patch("handwriting_grader.advanced.time.sleep"):
            create_advanced_report(self.config, self.root, self.exam, infographics=True,
                                   report_name="reports-images")
            self.assertEqual(client.models.generate_content.call_count, 2)
            create_advanced_report(self.config, self.root, self.exam, infographics=True,
                                   report_name="reports-images-copy")
            self.assertEqual(client.models.generate_content.call_count, 2)
            output = create_advanced_report(self.config, self.root, self.exam, infographics=True,
                                           image_model_config=image_settings, report_name="reports-other-image")
            self.assertEqual(client.models.generate_content.call_count, 4)
        self.assertEqual(create.call_args.kwargs["project"], "image-project")
        self.assertEqual(create.call_args.kwargs["http_options"].retry_options.attempts, 1)
        deck = Presentation(output / "class-presentation.pptx")
        text = "\n".join(shape.text for slide in deck.slides for shape in slide.shapes if shape.has_text_frame)
        self.assertIn("Text and numbers may be inaccurate", text)


    def test_score_editor_reset_does_not_overwrite_concurrent_review(self):
        from handwriting_grader.grading import write_review

        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        approve_grading(self.config, self.root, self.exam)
        self.start_score_editor()
        state = json.loads(self.score_request("/api/state")[1])

        def change_during_reset(*args, **kwargs):
            write_review(*args, **kwargs)
            book = load_workbook(root / "review.xlsx")
            try:
                book["Marks"]["D2"] = "Concurrent teacher edit"
                book.save(root / "review.xlsx")
            finally:
                book.close()

        with patch("handwriting_grader.grading.write_review", side_effect=change_during_reset):
            status, body = self.score_request("/api/reset", {"revision": state["revision"], "confirmed": True})
        self.assertEqual(status, 409, body)
        self.assertTrue((root / "approval.json").exists())
        book = load_workbook(root / "review.xlsx")
        try:
            self.assertEqual(book["Marks"]["D2"].value, "Concurrent teacher edit")
        finally:
            book.close()

    def test_approved_grading_remains_valid_after_default_cleanup(self):
        from handwriting_grader.cleanup import archive_and_clean

        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        approve_grading(self.config, self.root, self.exam)
        archive_and_clean(self.config, self.root)
        self.assertTrue((root / "images").is_dir())
        self.assertTrue((root.parent / "regions" / "images").is_dir())
        self.assertEqual(read_approved_grading(self.config, self.root, self.exam)[6][(0, "1")], 1)

    def test_moodle_uses_approved_rubric_and_never_overwrites_export(self):
        from handwriting_grader.moodle import export_moodle, _render_feedback_cover_pdf

        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        approve_grading(self.config, self.root, self.exam)
        self.assertEqual(main(["report", "--exam", str(self.exam)]), 0)
        analysis = root.parent / "ai-analysis"
        analysis.mkdir()
        (analysis / "approval.json").write_text("{}")
        with patch("handwriting_grader.advanced.read_approved_analysis",
                   return_value=(None, None, {0: "Reviewed feedback"}, None, None, None)), \
             patch("handwriting_grader.moodle._render_feedback_cover_pdf",
                   wraps=_render_feedback_cover_pdf) as cover:
            output = export_moodle(self.config, self.root, self.exam)
        self.assertEqual(cover.call_args.args[5], 2)
        existing = (output / "student_pdfs" / "001_One.pdf").read_bytes()
        with patch("handwriting_grader.advanced.read_approved_analysis",
                   return_value=(None, None, {0: "Reviewed feedback"}, None, None, None)):
            with self.assertRaisesRegex(ValueError, "already exists"):
                export_moodle(self.config, self.root, self.exam)
        self.assertEqual((output / "student_pdfs" / "001_One.pdf").read_bytes(), existing)

    def test_moodle_rejects_stale_feedback_and_supports_child_selection(self):
        from handwriting_grader.moodle import export_moodle

        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        approve_grading(self.config, self.root, self.exam)
        self.assertEqual(main(["report", "--exam", str(self.exam)]), 0)
        analysis = root.parent / "ai-analysis"
        analysis.mkdir()
        (analysis / "approval.json").write_text("{}")
        with patch("handwriting_grader.advanced.read_approved_analysis",
                   side_effect=ValueError("stale analysis")):
            with self.assertRaisesRegex(ValueError, "stale analysis"):
                export_moodle(self.config, self.root, self.exam)
        self.assertFalse((root.parent / "moodle").exists())
        with patch("handwriting_grader.moodle.export_moodle", return_value=root / "child-moodle") as export:
            self.assertEqual(main(["moodle-export", "--exam", str(self.exam), "--run", "revision-1"]), 0)
        self.assertEqual(export.call_args.kwargs["run_id"], "revision-1")

    def test_moodle_safe_names_and_fractional_worksheet_grades(self):
        import csv
        import zipfile
        from handwriting_grader.distribution import approved_report
        from handwriting_grader.moodle import export_moodle

        root = self.run_grading()
        self.fill_review(root / "review.xlsx", flagged_override=0.125)
        approve_grading(self.config, self.root, self.exam)
        self.assertEqual(main(["report", "--exam", str(self.exam)]), 0)
        validated, rows, reports = approved_report(self.config, self.root, self.exam)
        collision_rows = [{**rows[0], "id": "0/01"}, {**rows[1], "id": "0\\01", "name": rows[0]["name"]}]
        with patch("handwriting_grader.moodle.approved_report", return_value=(validated, collision_rows, reports)):
            with self.assertRaisesRegex(ValueError, "duplicate Moodle filenames"):
                export_moodle(self.config, self.root, self.exam)
        self.assertFalse((root.parent / "moodle").exists())

        worksheet = self.root / "worksheet.csv"
        unsafe_rows = [{**rows[0], "id": "../001"}, rows[1]]
        worksheet.write_text("ID number,Identifier,Full name,Grade\n../001,Participant 1,../../One,\n",
                             encoding="utf-8")
        with patch("handwriting_grader.moodle.approved_report", return_value=(validated, unsafe_rows, reports)):
            output = export_moodle(self.config, self.root, self.exam, worksheet=worksheet)
        with (output / "grading_worksheet_filled.csv").open(encoding="utf-8-sig", newline="") as stream:
            self.assertEqual(next(csv.DictReader(stream))["Grade"], "0.125")
        with zipfile.ZipFile(output / "feedback_files.zip") as archive:
            self.assertTrue(all(".." not in name.split("/") for name in archive.namelist()))
        self.assertEqual(len(list((output / "student_pdfs").glob("*.pdf"))), 2)

    def test_moodle_rejects_ambiguous_or_incomplete_input_before_export(self):
        from handwriting_grader.moodle import export_moodle

        root = self.run_grading()
        self.fill_review(root / "review.xlsx")
        approve_grading(self.config, self.root, self.exam)
        self.assertEqual(main(["report", "--exam", str(self.exam)]), 0)
        worksheet = self.root / "worksheet.csv"
        participants = self.root / "participants.csv"
        for contents, error in (
            ("ID number,Identifier\n001,Participant 1\n", "Grade"),
            ("ID number,Identifier,Grade\n001,,\n", "no participant identifier"),
            ("ID number,Identifier,Grade\n001,Participant 1,\n002,Participant 1,\n", "duplicate"),
            ("ID number,Identifier,Grade\n001,Participant 1,\n001,Participant 2,\n", "duplicate"),
        ):
            worksheet.write_text(contents, encoding="utf-8")
            with self.subTest(contents=contents), self.assertRaisesRegex(ValueError, error):
                export_moodle(self.config, self.root, self.exam, worksheet=worksheet)
            self.assertFalse((root.parent / "moodle").exists())
        for contents, error in (
            ("Name,Email address\nOne,one@example.com\n", "ID and email"),
            ("ID,EMAIL\n001,one@example.com\n001,other@example.com\n", "duplicate"),
        ):
            participants.write_text(contents, encoding="utf-8")
            with self.subTest(contents=contents), self.assertRaisesRegex(ValueError, error):
                export_moodle(self.config, self.root, self.exam, participants=participants)
            self.assertFalse((root.parent / "moodle").exists())


class DiscoveryBackportTests(unittest.TestCase):
    def test_default_discovery_does_not_probe_or_overwrite_configuration(self):
        from handwriting_grader.discovery import discover_best_tier_models, resolve_step_models, write_model_config

        client = Mock()
        client.models.list.return_value = [
            SimpleNamespace(name="publishers/google/models/gemini-3.8-flash"),
            SimpleNamespace(name="publishers/google/models/gemini-4-argon"),
        ]
        with patch("handwriting_grader.discovery.genai.Client", return_value=client):
            result = discover_best_tier_models("test-project", candidate_locations=["global"])
        client.models.generate_content.assert_not_called()
        self.assertEqual(result["flash"], "gemini-3.8-flash")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            original = b"Preserve this configuration"
            path.write_bytes(original)
            with self.assertRaises(FileExistsError):
                write_model_config(path, "test-project", "global", resolve_step_models(result))
            self.assertEqual(path.read_bytes(), original)

    def test_discovery_probe_requires_explicit_cost_consent(self):
        with patch("handwriting_grader.discovery.discover_and_report") as discover:
            self.assertEqual(main(["discover-models", "--project", "test-project", "--probe"]), 1)
            discover.assert_not_called()


class GroupedAnswerOrderingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_similarity_labels_describe_displayed_values(self):
        editor = Path(__file__).resolve().parents[1] / "src" / "handwriting_grader" / "score_editor.html"
        script = """
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf("function updateSimilarityLabels(");
const end = source.indexOf("\\nfunction moveBlankZeroAnswersLast(", start);
assert(start >= 0 && end > start);
const elements = {"similarity-label": {parentElement: {}}, "similarity-toggle": {}};
vm.runInNewContext(source.slice(start, end) + `
  updateSimilarityLabels(true);
  if (byId("similarity-label").textContent !== "Group Similarity") throw Error("group label");
  if (byId("similarity-toggle").textContent !== "Group Similarity") throw Error("group toggle");
  if (byId("similarity-label").parentElement.title !==
      "Click to sort by answer similarity and exit grouped view") throw Error("group sort title");
  updateSimilarityLabels(false);
`, {byId: id => elements[id]});
assert.strictEqual(elements["similarity-label"].textContent, "Answer Similarity");
assert.strictEqual(elements["similarity-toggle"].textContent, "Answer Similarity");
assert.strictEqual(elements["similarity-label"].parentElement.title, "Click to sort by answer similarity");
"""
        subprocess.run([shutil.which("node"), "-e", script, str(editor)], check=True, capture_output=True, text=True)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_blank_zero_answers_move_last_without_hiding_unscored_items(self):
        editor = Path(__file__).resolve().parents[1] / "src" / "handwriting_grader" / "score_editor.html"
        script = """
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf("function moveBlankZeroAnswersLast(");
const end = source.indexOf("\\nlet allStatusIncompleteOnly", start);
assert(start >= 0 && end > start);
vm.runInThisContext(source.slice(start, end));
const item = (id, answer, score, hasScore = true, needsManual = false) =>
  ({id, answer, score, hasScore, needsManual});
for (const model of ["Word Overlap", "text-embedding-3-small"]) {
  const clusters = [
    {repText: "(empty)", model, items: [
      item("blank-zero", "", 0), item("unscored", "", 0, false),
      item("needs-manual", "", 0, true, true), item("blank-positive", "", 1)
    ]},
    {repText: "answer", model, items: [item("zero-answer", "incorrect", 0)]},
    {repText: "", model, items: [item("whitespace-zero", "  ", 0)]}
  ];
  const result = moveBlankZeroAnswersLast(clusters);
  assert.deepStrictEqual(result.map(c => c.items.map(i => i.id)),
    [["unscored", "needs-manual", "blank-positive"], ["zero-answer"],
     ["blank-zero", "whitespace-zero"]]);
  assert.strictEqual(result[1].ungrouped, true);
  assert.strictEqual(result[2].blankZero, true);
  assert.strictEqual(clusters[0].items.length, 4);
  const separated = moveBlankZeroAnswersLast([
    {items: [item("first", "one", 1)]},
    {items: [item("same-a", "same", 1), item("same-b", "same", 1)]},
    {items: [item("second", "two", 1)]},
    {items: [item("blank", "", 0)]}
  ]);
  assert.deepStrictEqual(separated.map(c => c.items.map(i => i.id)),
    [["same-a", "same-b"], ["first", "second"], ["blank"]]);
  assert.strictEqual(separated[1].ungrouped, true);
  assert.strictEqual(separated[2].blankZero, true);
}
"""
        subprocess.run([shutil.which("node"), "-e", script, str(editor)], check=True, capture_output=True, text=True)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_confirm_question_blocks_invalid_marks_including_hidden_rows(self):
        editor = Path(__file__).resolve().parents[1] / "src" / "handwriting_grader" / "score_editor.html"
        script = """
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf('byId("mark-question-reviewed").onclick =');
const end = source.indexOf("\\nasync function loadTableImages()", start);
assert(start >= 0 && end > start);
let calls = 0, status = "";
const elements = {"mark-question-reviewed": {}, "inline-mark-0": {value: "1"},
  "review-workspace": {scrollTop: 500}};
const mark = value => ({manual_mark: null, moderated_mark: value, raw_mark: value,
  answer: "", reasoning: "", reviewed: false, grading_failed: false, moderator_flag: false});
const state = {revision: "test", questions: [{question_number: "Q1", marks: 2}],
  scripts: [{script_index: 0}, {script_index: 1}], marks: [[mark(1)], [mark(null)]]};
const context = {state, section: "0", byId: id => elements[id] || null,
  message: text => status = text, setBusy: () => {}, updateRevision: () => {},
  reload: async () => { state.marks.forEach(row => row[0].reviewed = true); },
  api: async () => { calls++; return {json: async () => ({revision: "new"})}; },
  document: {activeElement: null}, render: async () => {}};
vm.runInNewContext(source.slice(start, end), context);
(async () => {
  await elements["mark-question-reviewed"].onclick();
  assert.strictEqual(calls, 0);
  assert.match(status, /#2 \\(empty\\)/);
  assert.strictEqual(elements["review-workspace"].scrollTop, 500);
  state.marks[1][0].moderated_mark = 0;
  elements["inline-mark-0"].value = "  ";
  await elements["mark-question-reviewed"].onclick();
  assert.strictEqual(calls, 0);
  assert.match(status, /#1 \\(empty\\)/);
  for (const [value, reason] of [["abc", "not numeric"], ["-1", "outside"], ["3", "outside"]]) {
    elements["inline-mark-0"].value = value;
    await elements["mark-question-reviewed"].onclick();
    assert.strictEqual(calls, 0);
    assert(status.includes(reason));
  }
  elements["inline-mark-0"].value = "1";
  state.marks[1][0].moderated_mark = 3;
  await elements["mark-question-reviewed"].onclick();
  assert.strictEqual(calls, 0);
  assert.match(status, /#2 \\(outside/);
  state.marks[1][0].moderated_mark = 0;
  await elements["mark-question-reviewed"].onclick();
  assert.strictEqual(calls, 1);
  assert.strictEqual(elements["review-workspace"].scrollTop, 500);
  state.questions.push({question_number: "Q2", marks: 2});
  state.marks.forEach(row => { row[0].reviewed = false; row.push(mark(1)); });
  await elements["mark-question-reviewed"].onclick();
  assert.strictEqual(calls, 2);
  assert.strictEqual(context.section, "1");
  assert.strictEqual(elements["review-workspace"].scrollTop, 0);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        subprocess.run([shutil.which("node"), "-e", script, str(editor)], check=True, capture_output=True, text=True)
