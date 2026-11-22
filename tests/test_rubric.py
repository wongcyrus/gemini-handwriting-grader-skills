import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from docx import Document
from openpyxl import Workbook, load_workbook

from handwriting_grader.cli import main
from handwriting_grader.regions import file_hash
from handwriting_grader.rubric import read_approved_rubric, read_rubric, write_rubric


class RubricTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = {
            "exam": {"name": "Example", "marking_scheme": "source.xlsx", "output_dir": "output"}
        }
        self.exam = self.root / "exam.yaml"
        self.exam.write_text(yaml.safe_dump(self.config))
        self.questions = [
            {"question_number": "Q1", "question_text": "Question one",
             "marking_scheme": "One point", "marks": 2},
            {"question_number": "2a", "question_text": "Question two",
             "marking_scheme": "Two points", "marks": 3},
        ]
        write_rubric(self.root / "source.xlsx", self.questions)

    def test_import_review_approve_and_invalidate_edited_workbook(self):
        self.assertEqual(main(["import-rubric", "--exam", str(self.exam)]), 0)
        path = self.root / "output" / "rubric" / "rubric.xlsx"
        self.assertEqual(read_rubric(path), self.questions)
        self.assertEqual(main(["approve-rubric", "--exam", str(self.exam)]), 1)
        self.assertFalse((path.parent / "approval.json").exists())
        self.assertEqual(main(["approve-rubric", "--exam", str(self.exam), "--confirm-reviewed"]), 0)
        self.assertEqual(read_approved_rubric(self.config, self.root, self.exam), self.questions)
        workbook = load_workbook(path)
        workbook["Marking Scheme"]["D2"] = 4
        workbook.save(path)
        workbook.close()
        with self.assertRaisesRegex(ValueError, "changed since approval"):
            read_approved_rubric(self.config, self.root, self.exam)

    def test_changed_source_invalidates_approval(self):
        self.assertEqual(main(["import-rubric", "--exam", str(self.exam)]), 0)
        self.assertEqual(main(["approve-rubric", "--exam", str(self.exam), "--confirm-reviewed"]), 0)
        (self.root / "source.xlsx").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "source marking scheme changed"):
            read_approved_rubric(self.config, self.root, self.exam)

    def test_duplicate_question_and_formula_block_import(self):
        self.questions[1]["question_number"] = "1.0"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            write_rubric(self.root / "invalid.xlsx", self.questions)
        book = Workbook()
        sheet = book.active
        sheet.title = "Marking Scheme"
        sheet.append(["question_number", "question_text", "marking_scheme", "marks"])
        sheet.append(["1", "Question", "Answer", "=1+1"])
        book.save(self.root / "source.xlsx")
        book.close()
        self.assertEqual(main(["import-rubric", "--exam", str(self.exam)]), 1)
        self.assertFalse((self.root / "output" / "rubric").exists())

    def test_rubric_rejects_text_that_would_be_truncated(self):
        self.questions[0]["marking_scheme"] = "x" * 32768
        with self.assertRaisesRegex(ValueError, "32,767-character"):
            write_rubric(self.root / "invalid.xlsx", self.questions)

    def test_ai_extraction_requires_consent_and_preserves_general_guide(self):
        document = Document()
        document.add_paragraph("Question 1: Describe a concept. Maximum 2 marks.")
        document.save(self.root / "source.docx")
        self.config["exam"]["marking_scheme"] = "source.docx"
        self.exam.write_text(yaml.safe_dump(self.config))
        model = self.root / "config.yaml"
        model.write_text(yaml.safe_dump({
            "vertex_ai": {"use_vertexai": True}, "models": {"marking_scheme": "gemini-3.8-flash"},
        }))
        arguments = [
            "extract-rubric", "--exam", str(self.exam), "--model-config", str(model),
            "--project", "test-project", "--location", "global",
        ]
        with patch("handwriting_grader.rubric.genai.Client") as constructor:
            self.assertEqual(main(arguments), 1)
            constructor.assert_not_called()
            client = constructor.return_value.__enter__.return_value
            client.models.generate_content.return_value = SimpleNamespace(parsed={
                "questions": self.questions, "general_grading_guide": "Allow partial credit.",
            })
            self.assertEqual(main(arguments + ["--confirm-cost"]), 0)
            self.assertEqual(client.models.generate_content.call_count, 1)
        path = self.root / "output" / "rubric" / "rubric.xlsx"
        questions = read_rubric(path)
        self.assertIn("Allow partial credit.", questions[0]["marking_scheme"])
        self.assertFalse((path.parent / "approval.json").exists())

    def test_extraction_cache_reuses_validated_results_across_runs(self):
        document = Document()
        document.add_paragraph("Question 1: Describe a concept. Maximum 2 marks.")
        document.save(self.root / "source.docx")
        self.config["exam"]["marking_scheme"] = "source.docx"
        self.exam.write_text(yaml.safe_dump(self.config))
        model = self.root / "config.yaml"
        model.write_text(yaml.safe_dump({
            "vertex_ai": {"use_vertexai": True}, "models": {"marking_scheme": "gemini-3.8-flash"},
            "caching": {"enabled": True, "scope": "shared", "directory": "shared",
                        "agents": {"marking_scheme": True}},
        }))
        arguments = ["extract-rubric", "--exam", str(self.exam), "--model-config", str(model),
                     "--project", "test-project", "--location", "global", "--confirm-cost"]
        with patch("handwriting_grader.rubric.genai.Client") as constructor:
            client = constructor.return_value.__enter__.return_value
            client.models.generate_content.return_value = SimpleNamespace(parsed={
                "questions": self.questions, "general_grading_guide": "Allow partial credit.",
            })
            self.assertEqual(main(arguments), 0)
            self.config["exam"]["output_dir"] = "output-second"
            self.exam.write_text(yaml.safe_dump(self.config))
            self.assertEqual(main(arguments), 0)
            self.assertEqual(client.models.generate_content.call_count, 1)
        self.assertEqual(read_rubric(self.root / "output" / "rubric" / "rubric.xlsx"),
                         read_rubric(self.root / "output-second" / "rubric" / "rubric.xlsx"))

    def test_vertex_sdk_extraction_request_against_fake_service(self):
        from google.genai import Client
        from google.oauth2.credentials import Credentials

        calls = []
        questions = self.questions

        class FakeVertex(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                text = json.dumps({"questions": questions, "general_grading_guide": "Allow partial credit."})
                body = json.dumps({"candidates": [{
                    "content": {"role": "model", "parts": [{"text": text}]},
                    "finishReason": "STOP",
                }]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        document = Document()
        document.add_paragraph("Question 1: Describe a concept. Maximum 2 marks.")
        document.save(self.root / "source.docx")
        self.config["exam"]["marking_scheme"] = "source.docx"
        self.exam.write_text(yaml.safe_dump(self.config))
        model = self.root / "config.yaml"
        model.write_text(yaml.safe_dump({
            "vertex_ai": {"use_vertexai": True}, "models": {"marking_scheme": "gemini-3.8-flash"},
        }))
        service = ThreadingHTTPServer(("127.0.0.1", 0), FakeVertex)
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        try:
            def local_client(**kwargs):
                kwargs["http_options"].base_url = f"http://127.0.0.1:{service.server_port}"
                return Client(**kwargs, credentials=Credentials(token="local-test-token"))

            with patch("handwriting_grader.rubric.genai.Client", side_effect=local_client):
                self.assertEqual(main([
                    "extract-rubric", "--exam", str(self.exam), "--model-config", str(model),
                    "--project", "test-project", "--location", "global", "--confirm-cost",
                ]), 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["generationConfig"]["responseMimeType"], "application/json")
            self.assertIn("responseSchema", calls[0]["generationConfig"])
            result = read_rubric(self.root / "output" / "rubric" / "rubric.xlsx")
            self.assertIn("Allow partial credit.", result[0]["marking_scheme"])
        finally:
            service.shutdown()
            thread.join(timeout=5)
            service.server_close()

    def test_search_grounded_verification_requires_consent_and_preserves_sources(self):
        from google.genai import Client
        from google.oauth2.credentials import Credentials

        calls = []

        class FakeVertex(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = json.dumps({"candidates": [{
                    "content": {"role": "model", "parts": [{"text": "Check the evidence."}]},
                    "finishReason": "STOP",
                    "groundingMetadata": {"groundingChunks": [
                        {"web": {"title": "Example", "uri": "https://example.test/source"}}
                    ]},
                }]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.assertEqual(main(["import-rubric", "--exam", str(self.exam)]), 0)
        model = self.root / "config.yaml"
        model.write_text(yaml.safe_dump({
            "vertex_ai": {"use_vertexai": True}, "models": {"marking_scheme": "gemini-3.8-flash"},
        }))
        arguments = [
            "verify-rubric", "--exam", str(self.exam), "--model-config", str(model),
            "--project", "test-project", "--location", "global",
        ]
        service = ThreadingHTTPServer(("127.0.0.1", 0), FakeVertex)
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        try:
            def local_client(**kwargs):
                kwargs["http_options"].base_url = f"http://127.0.0.1:{service.server_port}"
                return Client(**kwargs, credentials=Credentials(token="local-test-token"))

            with patch("handwriting_grader.verification.genai.Client", side_effect=local_client):
                self.assertEqual(main(arguments), 1)
                self.assertEqual(calls, [])
                self.assertEqual(main(arguments + ["--confirm-cost"]), 0)
            self.assertEqual(len(calls), 2)
            self.assertIn("googleSearch", calls[0]["tools"][0])
            output = self.root / "output" / "rubric" / "verification"
            book = load_workbook(output / "verification.xlsx", read_only=True)
            try:
                self.assertIn("https://example.test/source", book["Verification"]["C2"].value)
                self.assertEqual(book["Verification"]["D2"].value, "yes")
            finally:
                book.close()
            report = Document(output / "verification.docx")
            text = "\n".join(paragraph.text for paragraph in report.paragraphs)
            self.assertIn("Question Q1", text)
            self.assertIn("Question 2a", text)
            self.assertIn("Check the evidence.", text)
            self.assertIn("https://example.test/source", text)
            self.assertIn("does not approve the rubric", text)
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["verification_docx_sha256"], file_hash(output / "verification.docx"))
            self.assertEqual(manifest["verification_xlsx_sha256"], file_hash(output / "verification.xlsx"))
            self.assertFalse((output.parent / "approval.json").exists())
        finally:
            service.shutdown()
            thread.join(timeout=5)
            service.server_close()

    def test_verification_word_report_warns_when_no_sources_returned(self):
        self.assertEqual(main(["import-rubric", "--exam", str(self.exam)]), 0)
        model = self.root / "config.yaml"
        model.write_text(yaml.safe_dump({
            "vertex_ai": {"use_vertexai": True}, "models": {"marking_scheme": "gemini-3.8-flash"},
        }))
        arguments = ["verify-rubric", "--exam", str(self.exam), "--model-config", str(model),
                     "--project", "test-project", "--location", "global", "--confirm-cost"]
        with patch("handwriting_grader.verification.genai.Client") as constructor:
            client = constructor.return_value.__enter__.return_value
            client.models.generate_content.return_value = SimpleNamespace(text="Uncertain.", candidates=[])
            self.assertEqual(main(arguments), 0)
            self.assertEqual(client.models.generate_content.call_count, 2)
        output = self.root / "output" / "rubric" / "verification"
        text = "\n".join(paragraph.text for paragraph in Document(output / "verification.docx").paragraphs)
        self.assertEqual(text.count("No sources returned; human review is required."), 2)
        self.assertFalse((output.parent / "approval.json").exists())


if __name__ == "__main__":
    unittest.main()
