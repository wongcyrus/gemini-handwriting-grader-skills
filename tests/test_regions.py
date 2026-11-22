import json
import importlib.util
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from openpyxl import Workbook
from pypdf import PdfWriter
import yaml

from handwriting_grader.editor import RegionServer
from handwriting_grader.cli import main
from handwriting_grader.regions import approval_is_current, prepare_regions, verify_run
from handwriting_grader.suggestions import generate_suggestions


@unittest.skipUnless(shutil.which("pdftoppm"), "Poppler is required")
class RegionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        book = Workbook()
        sheet = book.active
        sheet.title = "Roster"
        sheet.append(["ID", "NAME", "CLASS"])
        sheet.append(["001", "Example", "A"])
        book.save(self.root / "roster.xlsx")
        book.close()
        pdf = PdfWriter()
        for _ in range(2):
            pdf.add_blank_page(width=200, height=300)
        with (self.root / "scan.pdf").open("wb") as file:
            pdf.write(file)
        (self.root / "rubric.docx").write_bytes(b"fixture")
        self.config = {
            "exam": {
                "name": "Example", "roster": "roster.xlsx", "roster_sheet": "Roster",
                "scanned_pdf": "scan.pdf", "marking_scheme": "rubric.docx", "output_dir": "output",
            },
            "layout": {"pages_per_student": 2, "same_layout_for_all": True},
        }
        self.regions = prepare_regions(self.config, self.root, 2)

    def start_server(self):
        self.server = RegionServer(self.config, self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def request(self, path, body=None, token=True, origin=True):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {self.server.token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
            if origin:
                headers["Origin"] = self.server.origin
            body = json.dumps(body).encode()
        req = urllib.request.Request(self.server.origin + path, data=body, headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.status, raw

    def test_prepare_renders_layout_and_refuses_overwrite(self):
        root, manifest = verify_run(self.config, self.root)
        self.assertEqual(root, self.regions)
        self.assertEqual(len(manifest["pages"]), 2)
        self.assertTrue((root / "images" / "0.jpg").is_file())
        with self.assertRaisesRegex(ValueError, "already exists"):
            prepare_regions(self.config, self.root, 2)

    def test_cli_prepares_and_serves_a_real_run(self):
        self.config["exam"]["output_dir"] = "another-run"
        config_path = self.root / "exam.yaml"
        config_path.write_text(yaml.safe_dump(self.config))
        self.assertEqual(main(["prepare-regions", "--exam", str(config_path)]), 0)
        self.assertEqual(main(["prepare-regions", "--exam", str(config_path)]), 1)
        process = subprocess.Popen(
            [sys.executable, "-m", "handwriting_grader.cli", "review-regions",
             "--exam", str(config_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            ready, _, _ = select.select([process.stdout], [], [], 10)
            self.assertTrue(ready, "CLI editor did not become ready")
            line = process.stdout.readline().strip()
            self.assertTrue(line.startswith("Open http://127.0.0.1:"), line)
            url, token = line.removeprefix("Open ").split("#", 1)
            request = urllib.request.Request(
                url + "api/state", headers={"Authorization": "Bearer " + token},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(len(json.load(response)["pages"]), 2)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
            process.communicate(timeout=10)

    def test_save_approve_and_invalidate_review(self):
        self.start_server()
        status, _ = self.request("/api/state", token=False)
        self.assertEqual(status, 403)
        status, _ = self.request("/api/page", {"revision": 0}, origin=False)
        self.assertEqual(status, 403)
        status, body = self.request("/api/state")
        self.assertEqual(status, 200)
        state = json.loads(body)
        status, _ = self.request("/api/images/0")
        self.assertEqual(status, 200)
        status, _ = self.request("/api/images/../manifest.json")
        self.assertEqual(status, 404)
        status, _ = self.request("/api/approve", {"revision": 0})
        self.assertEqual(status, 400)
        boxes = [
            {"x": i * 40, "y": 10, "width": 30, "height": 30, "label": label}
            for i, label in enumerate(["NAME", "ID", "CLASS", "1"])
        ]
        status, body = self.request("/api/page", {
            "revision": state["draft"]["revision"], "page": 0, "boxes": boxes, "reviewed": True,
        })
        self.assertEqual(status, 200, body)
        revision = json.loads(body)["revision"]
        status, _ = self.request("/api/page", {
            "revision": 0, "page": 1, "boxes": [], "reviewed": True,
        })
        self.assertEqual(status, 409)
        status, body = self.request("/api/page", {
            "revision": revision, "page": 1, "boxes": [], "reviewed": True,
        })
        self.assertEqual(status, 200, body)
        revision = json.loads(body)["revision"]
        status, body = self.request("/api/approve", {"revision": revision})
        self.assertEqual(status, 200, body)
        self.assertTrue((self.regions / "approval.json").exists())
        saved = json.loads((self.regions / "annotations.json").read_text())
        self.assertEqual(saved["0"], boxes)
        root, manifest = verify_run(self.config, self.root)
        draft = json.loads((root / "draft.json").read_text())
        self.assertTrue(approval_is_current(root, manifest, draft))
        (root / "annotations.json").write_text("{}")
        self.assertFalse(approval_is_current(root, manifest, draft))
        status, _ = self.request("/api/page", {
            "revision": revision, "page": 1, "boxes": [], "reviewed": False,
        })
        self.assertEqual(status, 200)
        self.assertFalse((self.regions / "approval.json").exists())

    def test_out_of_bounds_boxes_and_duplicate_labels_are_rejected(self):
        self.start_server()
        bad = [{"x": -1, "y": 0, "width": 1, "height": 1, "label": "ID"}]
        status, _ = self.request("/api/page", {
            "revision": 0, "page": 0, "boxes": bad, "reviewed": True,
        })
        self.assertEqual(status, 400)
        boxes = [
            {"x": i * 35, "y": 5, "width": 30, "height": 30, "label": label}
            for i, label in enumerate(["NAME", "ID", "CLASS", "1.0", "Q1"])
        ]
        status, _ = self.request("/api/page", {
            "revision": 0, "page": 0, "boxes": boxes, "reviewed": True,
        })
        self.assertEqual(status, 200)
        status, _ = self.request("/api/page", {
            "revision": 1, "page": 1, "boxes": [], "reviewed": True,
        })
        self.assertEqual(status, 200)
        status, body = self.request("/api/approve", {"revision": 2})
        self.assertEqual(status, 400)
        self.assertIn(b"Duplicate", body)

    def test_id_on_later_page_cannot_be_approved(self):
        self.start_server()
        def box(label):
            return {"x": 1, "y": 1, "width": 20, "height": 20, "label": label}
        status, _ = self.request("/api/page", {
            "revision": 0, "page": 0, "boxes": [box("NAME"), box("CLASS"), box("1")], "reviewed": True,
        })
        self.assertEqual(status, 200)
        status, _ = self.request("/api/page", {
            "revision": 1, "page": 1, "boxes": [box("ID")], "reviewed": True,
        })
        self.assertEqual(status, 200)
        status, body = self.request("/api/approve", {"revision": 2})
        self.assertEqual(status, 400)
        self.assertIn(b"first page", body)

    def test_changing_config_or_page_image_invalidates_run(self):
        self.config["exam"]["name"] = "Changed"
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            verify_run(self.config, self.root)
        self.config["exam"]["name"] = "Example"
        (self.regions / "images" / "0.jpg").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "image changed"):
            verify_run(self.config, self.root)

    def test_running_editor_detects_config_file_change(self):
        config_path = self.root / "exam.yaml"
        config_path.write_text(yaml.safe_dump(self.config))
        self.server = RegionServer(self.config, self.root, config_path=config_path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.assertEqual(self.request("/api/state")[0], 200)
        self.config["exam"]["name"] = "Changed"
        config_path.write_text(yaml.safe_dump(self.config))
        self.assertEqual(self.request("/api/state")[0], 409)

    def test_vertex_suggestions_require_consent_and_preserve_draft(self):
        config_path = self.root / "exam.yaml"
        config_path.write_text(yaml.safe_dump(self.config))
        model_path = self.root / "config.yaml"
        model_path.write_text(yaml.safe_dump({
            "vertex_ai": {"use_vertexai": True}, "models": {"annotation": "gemini-4-argon"},
        }))
        arguments = [
            "suggest-regions", "--exam", str(config_path),
            "--model-config", str(model_path), "--project", "test-project", "--location", "global",
        ]
        original_draft = (self.regions / "draft.json").read_bytes()
        with patch("handwriting_grader.suggestions.genai.Client") as constructor:
            self.assertEqual(main(arguments), 1)
            constructor.assert_not_called()
            client = constructor.return_value.__enter__.return_value
            client.models.generate_content.return_value = SimpleNamespace(parsed={
                "boxes": [{"x": 100, "y": 200, "width": 300, "height": 400, "label": "1"}]
            })
            self.assertEqual(main(arguments + ["--confirm-cost"]), 0)
            self.assertEqual(constructor.call_count, 1)
            self.assertEqual(constructor.call_args.kwargs["project"], "test-project")
            self.assertEqual(constructor.call_args.kwargs["location"], "global")
            self.assertTrue(constructor.call_args.kwargs["vertexai"])
            self.assertEqual(client.models.generate_content.call_count, 2)
        self.assertEqual((self.regions / "draft.json").read_bytes(), original_draft)
        suggested = json.loads((self.regions / "ai_suggestions.json").read_text())
        self.assertEqual(suggested["annotations"]["0"][0]["label"], "1")
        _, manifest = verify_run(self.config, self.root, config_path)
        width = manifest["pages"][0]["width"]
        self.assertEqual(suggested["annotations"]["0"][0]["x"], round(width / 10))
        with self.assertRaisesRegex(ValueError, "already exist"):
            generate_suggestions(self.config, self.root, config_path, model_path, "test-project", "global")
        self.start_server()
        status, response = self.request("/api/suggestions")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response)["annotations"], suggested["annotations"])

    def test_invalid_vertex_boxes_never_modify_draft(self):
        config_path = self.root / "exam.yaml"
        config_path.write_text(yaml.safe_dump(self.config))
        model_path = self.root / "config.yaml"
        model_path.write_text(yaml.safe_dump({
            "vertex_ai": {"use_vertexai": True}, "models": {"annotation": "gemini-4-argon"},
        }))
        original = (self.regions / "draft.json").read_bytes()
        with patch("handwriting_grader.suggestions.genai.Client") as constructor:
            client = constructor.return_value.__enter__.return_value
            client.models.generate_content.return_value = SimpleNamespace(parsed={
                "boxes": [{"x": 999, "y": 0, "width": 100, "height": 100, "label": "1"}]
            })
            with self.assertRaisesRegex(ValueError, "invalid normalized box"):
                generate_suggestions(self.config, self.root, config_path, model_path, "test-project", "global")
        self.assertFalse((self.regions / "ai_suggestions.json").exists())
        self.assertEqual((self.regions / "draft.json").read_bytes(), original)

    def test_annotation_cache_reuses_results_without_skipping_confirmation(self):
        config_path = self.root / "exam.yaml"
        config_path.write_text(yaml.safe_dump(self.config))
        model_path = self.root / "config.yaml"
        model_path.write_text(yaml.safe_dump({
            "vertex_ai": {"use_vertexai": True}, "models": {"annotation": "gemini-4-argon"},
            "caching": {"enabled": True, "agents": {"annotation": True}},
        }))
        arguments = ["suggest-regions", "--exam", str(config_path), "--model-config", str(model_path),
                     "--project", "test-project", "--location", "global"]
        with patch("handwriting_grader.suggestions.genai.Client") as constructor:
            client = constructor.return_value.__enter__.return_value
            client.models.generate_content.return_value = SimpleNamespace(parsed={
                "boxes": [{"x": 100, "y": 200, "width": 300, "height": 400, "label": "1"}]
            })
            self.assertEqual(main(arguments), 1)
            self.assertEqual(main(arguments + ["--confirm-cost"]), 0)
            calls = client.models.generate_content.call_count
            self.assertGreater(calls, 0)
            (self.regions / "ai_suggestions.json").rename(self.regions / "first-suggestions.json")
            self.assertEqual(main(arguments), 1)
            self.assertEqual(main(arguments + ["--confirm-cost"]), 0)
            self.assertEqual(client.models.generate_content.call_count, calls)

    def test_vertex_sdk_serializes_requests_against_local_fake_service(self):
        from google.oauth2.credentials import Credentials
        from google.genai import Client

        calls = []

        class FakeVertex(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                text = json.dumps({"boxes": [{
                    "x": 100, "y": 100, "width": 300, "height": 200, "label": "1",
                }]})
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
            config_path = self.root / "exam.yaml"
            config_path.write_text(yaml.safe_dump(self.config))
            model_path = self.root / "config.yaml"
            model_path.write_text(yaml.safe_dump({
                "vertex_ai": {"use_vertexai": True}, "models": {"annotation": "gemini-4-argon"},
            }))
            base_url = f"http://127.0.0.1:{service.server_port}"
            def local_client(**kwargs):
                kwargs["http_options"].base_url = base_url
                return Client(**kwargs, credentials=Credentials(token="local-test-token"))
            with patch("handwriting_grader.suggestions.genai.Client", side_effect=local_client):
                generate_suggestions(self.config, self.root, config_path, model_path, "test-project", "global")
            self.assertEqual(len(calls), 2)
            self.assertIn("contents", calls[0])
            self.assertTrue((self.regions / "ai_suggestions.json").is_file())
        finally:
            service.shutdown()
            thread.join(timeout=5)
            service.server_close()

    @unittest.skipUnless(
        importlib.util.find_spec("playwright") and shutil.which("google-chrome"),
        "Playwright and Google Chrome are required",
    )
    def test_browser_draws_reviews_and_approves(self):
        from playwright.sync_api import sync_playwright

        self.start_server()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("google-chrome"), headless=True,
            )
            try:
                page = browser.new_page(viewport={"width": 1200, "height": 900})
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(f"{self.server.origin}/#{self.server.token}")
                page.get_by_text("Review and save every page.").wait_for()
                svg = page.locator("#canvas")
                bounds = svg.bounding_box()
                self.assertIsNotNone(bounds)
                for index, value in enumerate(["NAME", "ID", "CLASS", "1"]):
                    page.locator("#new").click()
                    page.locator("#label").fill(value)
                    bounds = svg.bounding_box()
                    x = bounds["x"] + 20 + index * 75
                    y = bounds["y"] + 30
                    page.mouse.move(x, y)
                    page.mouse.down()
                    page.mouse.move(x + 50, y + 50, steps=4)
                    page.mouse.up()
                self.assertEqual(page.locator("#list button").count(), 4)
                page.locator("#list button").nth(3).click()
                before_x = page.evaluate("boxes[3].x")
                rect = page.locator('rect[data-index="3"][data-action="move"]').bounding_box()
                page.mouse.move(rect["x"] + 10, rect["y"] + 10)
                page.mouse.down()
                page.mouse.move(rect["x"] + 30, rect["y"] + 10, steps=4)
                page.mouse.up()
                self.assertGreater(page.evaluate("boxes[3].x"), before_x)
                before_width = page.evaluate("boxes[3].width")
                handle = page.locator('rect[data-index="3"][data-action="resize"]').bounding_box()
                page.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2)
                page.mouse.down()
                page.mouse.move(handle["x"] + handle["width"] / 2 + 20, handle["y"] + handle["height"] / 2, steps=4)
                page.mouse.up()
                self.assertGreater(page.evaluate("boxes[3].width"), before_width)
                page.locator("#review").click()
                page.get_by_text("Page saved and marked reviewed.").wait_for()
                page.locator("#next").click()
                page.get_by_text("Page 2 of 2").wait_for()
                page.locator("#review").click()
                page.get_by_text("Page saved and marked reviewed.").wait_for()
                page.locator("#approve").click()
                page.get_by_text("Regions approved. This does not start grading.").wait_for()
                annotations = json.loads((self.regions / "annotations.json").read_text())
                self.assertEqual([box["label"] for box in annotations["0"]], ["NAME", "ID", "CLASS", "1"])
                self.assertTrue((self.regions / "approval.json").exists())
            finally:
                browser.close()

    @unittest.skipUnless(
        importlib.util.find_spec("playwright") and shutil.which("google-chrome"),
        "Playwright and Google Chrome are required",
    )
    def test_browser_requires_confirmation_before_replacing_boxes(self):
        from playwright.sync_api import expect, sync_playwright

        config_path = self.root / "exam.yaml"
        config_path.write_text(yaml.safe_dump(self.config))
        model_path = self.root / "config.yaml"
        model_path.write_text(yaml.safe_dump({
            "vertex_ai": {"use_vertexai": True}, "models": {"annotation": "gemini-4-argon"},
        }))
        with patch("handwriting_grader.suggestions.genai.Client") as constructor:
            constructor.return_value.__enter__.return_value.models.generate_content.return_value = SimpleNamespace(
                parsed={"boxes": [{"x": 100, "y": 100, "width": 300, "height": 200, "label": "1"}]}
            )
            generate_suggestions(self.config, self.root, config_path, model_path, "test-project", "global")
        self.start_server()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=shutil.which("google-chrome"), headless=True)
            try:
                page = browser.new_page(viewport={"width": 1200, "height": 900})
                page.goto(f"{self.server.origin}/#{self.server.token}")
                page.get_by_text("Review and save every page.").wait_for()
                expect(page.locator("#list button")).to_have_text(["1"])
                page.locator("#new").click()
                page.locator("#label").fill("Manual")
                bounds = page.locator("#canvas").bounding_box()
                page.mouse.move(bounds["x"] + 20, bounds["y"] + 30)
                page.mouse.down()
                page.mouse.move(bounds["x"] + 70, bounds["y"] + 80, steps=4)
                page.mouse.up()
                self.assertEqual(page.locator("#list button").all_text_contents(), ["1", "Manual"])
                with page.expect_event("dialog") as first_dialog:
                    page.locator("#suggestions").click()
                first_dialog.value.dismiss()
                self.assertEqual(page.locator("#list button").all_text_contents(), ["1", "Manual"])
                with page.expect_event("dialog") as second_dialog:
                    page.locator("#suggestions").click()
                second_dialog.value.accept()
                expect(page.locator("#list button")).to_have_text(["1"])
                page.locator("#save").click()
                page.get_by_text("Draft saved; page is not marked reviewed.").wait_for()
                self.assertFalse((self.regions / "approval.json").exists())
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
