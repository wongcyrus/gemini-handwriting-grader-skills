import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml
from openpyxl import Workbook, load_workbook
from pypdf import PdfWriter

from handwriting_grader.attendance import approved_attendance, attendance_rows
from handwriting_grader.cli import check_exam, main
from handwriting_grader.distribution import preview_email
from handwriting_grader.grade_review import approve_grading, read_approved_grading
from handwriting_grader.grading import grade_exam
from handwriting_grader.regions import atomic_json, file_hash, prepare_regions, verify_run
from handwriting_grader.regrading import regrade
from handwriting_grader.reporting import create_reports
from handwriting_grader.rubric import approve_rubric, import_rubric, write_rubric
from handwriting_grader.score_editor import ScoreServer, load_state
from test_grading import FakeGrader


@unittest.skipUnless(shutil.which("pdftoppm"), "Poppler is required")
class AttendanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        book = Workbook()
        sheet = book.active
        sheet.title = "Roster"
        sheet.append(["ID", "NAME", "CLASS"])
        for student in [("001", "One", "A"), ("002", "Two", "A"), ("003", "Three", "B")]:
            sheet.append(student)
        book.save(self.root / "roster.xlsx")
        book.close()
        pdf = PdfWriter()
        for _ in range(4):
            pdf.add_blank_page(width=200, height=300)
        with (self.root / "scan.pdf").open("wb") as file:
            pdf.write(file)
        pdf.close()
        write_rubric(self.root / "source.xlsx", [{
            "question_number": "1", "question_text": "Question",
            "marking_scheme": "Award two marks", "marks": 2,
        }])
        self.config = {
            "exam": {"name": "Example", "roster": "roster.xlsx", "roster_sheet": "Roster",
                     "attendance": "attendance.csv", "scanned_pdf": "scan.pdf",
                     "marking_scheme": "source.xlsx", "output_dir": "output"},
            "layout": {"pages_per_student": 2, "same_layout_for_all": True},
        }
        self.exam = self.root / "exam.yaml"
        self.exam.write_text(yaml.safe_dump(self.config))
        self.models = self.root / "models.yaml"
        self.models.write_text(yaml.safe_dump({"vertex_ai": {"use_vertexai": True},
            "models": {"ocr": "ocr", "grading": "grading", "moderation": "moderation"}}))
        self.write_attendance([("001", "present"), ("002", "absent"), ("003", "present")])

    def write_attendance(self, rows):
        with (self.root / "attendance.csv").open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["ID", "STATUS"])
            writer.writerows(rows)

    def prepare_approved_run(self):
        self.assertEqual(main(["prepare-attendance", "--exam", str(self.exam)]), 0)
        self.assertEqual(main(["approve-attendance", "--exam", str(self.exam)]), 1)
        self.assertEqual(main(["approve-attendance", "--exam", str(self.exam), "--confirm-reviewed"]), 0)
        import_rubric(self.config, self.root, self.exam)
        approve_rubric(self.config, self.root, self.exam)
        regions = prepare_regions(self.config, self.root, 2)
        _, manifest = verify_run(self.config, self.root, self.exam)
        boxes = [{"x": i * 40, "y": 10, "width": 30, "height": 30, "label": label}
                 for i, label in enumerate(["NAME", "ID", "CLASS", "1"])]
        annotations = {"0": boxes, "1": []}
        atomic_json(regions / "draft.json", {"revision": 1, "annotations": annotations, "reviewed": [0, 1]})
        atomic_json(regions / "annotations.json", annotations)
        atomic_json(regions / "approval.json", {
            "annotations_sha256": file_hash(regions / "annotations.json"),
            "config_sha256": manifest["config_sha256"], "pdf_sha256": manifest["pdf_sha256"], "revision": 1,
        })

    def test_validation_and_review_versioning(self):
        errors, summary = check_exam(self.config, self.root)
        self.assertEqual(errors, [])
        self.assertEqual((summary["students"], summary["absent"]), (2, 1))
        self.assertEqual(main(["grade", "--exam", str(self.exam), "--model-config", str(self.models),
                               "--project", "project", "--location", "global", "--confirm-cost"]), 1)
        self.assertFalse((self.root / "output" / "grading").exists())
        for rows in [
            [("001", "present"), ("002", "absent")],
            [("001", "present"), ("002", "absent"), ("002", "present")],
            [("001", "present"), ("002", "absent"), ("999", "present")],
            [("001", "present"), ("002", ""), ("003", "present")],
            [("001", "absent"), ("002", "absent"), ("003", "absent")],
        ]:
            self.write_attendance(rows)
            with self.assertRaises(ValueError):
                attendance_rows(self.config, self.root)
        self.write_attendance([("001", "present"), ("002", "absent"), ("003", "present")])
        self.assertEqual(main(["prepare-attendance", "--exam", str(self.exam)]), 0)
        self.assertEqual(main(["approve-attendance", "--exam", str(self.exam), "--confirm-reviewed"]), 0)
        review, original_hash = approved_attendance(self.config, self.root, self.exam)
        self.assertEqual([row["id"] for row in review["students"] if row["status"] == "absent"], ["002"])
        self.write_attendance([("001", "absent"), ("002", "present"), ("003", "present")])
        with self.assertRaisesRegex(ValueError, "changed"):
            approved_attendance(self.config, self.root, self.exam)
        self.assertEqual(main(["prepare-attendance", "--exam", str(self.exam)]), 0)
        with self.assertRaises(OSError):
            approved_attendance(self.config, self.root, self.exam)
        self.assertEqual(main(["approve-attendance", "--exam", str(self.exam), "--confirm-reviewed"]), 0)
        self.assertNotEqual(approved_attendance(self.config, self.root, self.exam)[1], original_hash)
        self.assertEqual(len(list((self.root / "output" / "attendance" / "versions").iterdir())), 2)

    def test_present_only_review_reports_and_email(self):
        self.prepare_approved_run()
        _, summary = check_exam(self.config, self.root)
        root = grade_exam(self.config, self.root, self.exam, self.models, "project", "global", summary,
                          provider_factory=FakeGrader)
        server = ScoreServer(self.config, self.root, config_path=self.exam)
        self.addCleanup(server.server_close)
        self.assertEqual(set(load_state(server)[2]), {"001", "003"})
        book = load_workbook(root / "review.xlsx")
        for row, student in [(2, "001"), (3, "002")]:
            book["Scripts"].cell(row, 4, student)
            book["Scripts"].cell(row, 5, "yes")
            book["Marks"].cell(row, 12, "yes")
        book["Marks"].cell(2, 11, 1)
        book.save(root / "review.xlsx")
        book.close()
        with self.assertRaisesRegex(ValueError, "distinct roster"):
            approve_grading(self.config, self.root, self.exam)
        book = load_workbook(root / "review.xlsx")
        book["Scripts"].cell(3, 4, "003")
        book.save(root / "review.xlsx")
        book.close()
        approve_grading(self.config, self.root, self.exam)
        self.assertEqual(set(read_approved_grading(self.config, self.root, self.exam)[4]), {"001", "003"})
        child = regrade(self.config, self.root, self.exam, "revision", None, "1",
                        self.models, "project", "global", provider_factory=FakeGrader)
        child_results = json.loads((child / "grading" / "results.json").read_text())
        self.assertEqual(len(child_results["scripts"]), 2)
        self.assertEqual(len(child_results["marks"]), 2)
        reports = create_reports(self.config, self.root, self.exam)
        book = load_workbook(reports / "absences.xlsx", read_only=True)
        self.assertEqual(list(book.active.values)[1], ("002", "Two", "A", "absent"))
        book.close()
        book = load_workbook(reports / "score_report.xlsx", read_only=True)
        self.assertEqual({row[0] for row in list(book.active.values)[1:]}, {"001", "003"})
        book.close()
        settings = self.root / "email.yaml"
        settings.write_text(yaml.safe_dump({"sender": "teacher@example.test", "smtp_host": "smtp.example.test",
            "smtp_port": 587, "smtp_username": "teacher", "subject": "{exam}", "body": "Hello {name}"}))
        recipients = self.root / "recipients.csv"
        recipients.write_text("ID,EMAIL\n001,one@example.test\n002,two@example.test\n003,three@example.test\n")
        with self.assertRaises(ValueError):
            preview_email(self.config, self.root, self.exam, settings, recipients)
        recipients.write_text("ID,EMAIL\n001,one@example.test\n003,three@example.test\n")
        email = preview_email(self.config, self.root, self.exam, settings, recipients)
        self.assertEqual({item["id"] for item in json.loads((email / "preview.json").read_text())["messages"]},
                         {"001", "003"})
        self.write_attendance([("001", "absent"), ("002", "present"), ("003", "present")])
        with self.assertRaises(ValueError):
            read_approved_grading(self.config, self.root, self.exam)

    def test_change_during_grading_stops_before_more_calls(self):
        self.prepare_approved_run()
        _, summary = check_exam(self.config, self.root)
        attendance = self.root / "attendance.csv"
        calls = []

        class ChangingGrader(FakeGrader):
            def ocr(self, image, field):
                calls.append(field)
                attendance.write_text("ID,STATUS\n001,absent\n002,present\n003,present\n")
                return super().ocr(image, field)

        with self.assertRaisesRegex(ValueError, "changed"):
            grade_exam(self.config, self.root, self.exam, self.models, "project", "global", summary,
                       provider_factory=ChangingGrader)
        self.assertEqual(len(calls), 1)
        self.assertFalse((self.root / "output" / "grading" / "review.xlsx").exists())

    def test_explicit_all_present_and_multiple_absences_count_scans(self):
        self.write_attendance([("001", "present"), ("002", "present"), ("003", "present")])
        errors, summary = check_exam(self.config, self.root)
        self.assertIn("PDF page count does not equal roster count times pages_per_student", errors)
        self.assertEqual((summary["students"], summary["absent"]), (3, 0))
        self.write_attendance([("001", "present"), ("002", "absent"), ("003", "absent")])
        errors, summary = check_exam(self.config, self.root)
        self.assertIn("PDF page count does not equal roster count times pages_per_student", errors)
        pdf = PdfWriter()
        for _ in range(2):
            pdf.add_blank_page(width=200, height=300)
        with (self.root / "scan.pdf").open("wb") as file:
            pdf.write(file)
        pdf.close()
        errors, summary = check_exam(self.config, self.root)
        self.assertEqual(errors, [])
        self.assertEqual((summary["students"], summary["absent"]), (1, 2))
        self.assertEqual(main(["prepare-attendance", "--exam", str(self.exam)]), 0)
        self.assertEqual(main(["approve-attendance", "--exam", str(self.exam), "--confirm-reviewed"]), 0)
        review, _ = approved_attendance(self.config, self.root, self.exam)
        self.assertEqual(sum(row["status"] == "absent" for row in review["students"]), 2)

    def test_review_and_roster_tampering_invalidates_approval(self):
        self.assertEqual(main(["prepare-attendance", "--exam", str(self.exam)]), 0)
        self.assertEqual(main(["approve-attendance", "--exam", str(self.exam), "--confirm-reviewed"]), 0)
        pointer = json.loads((self.root / "output" / "attendance" / "current.json").read_text())
        review = self.root / "output" / "attendance" / "versions" / pointer["review_id"] / "review.json"
        original = review.read_text()
        review.write_text(original.replace('"name": "Two"', '"name": "Changed"'))
        with self.assertRaises(ValueError):
            approved_attendance(self.config, self.root, self.exam)
        review.write_text(original)
        book = load_workbook(self.root / "roster.xlsx")
        book["Roster"]["B3"] = "Changed"
        book.save(self.root / "roster.xlsx")
        book.close()
        with self.assertRaises(ValueError):
            approved_attendance(self.config, self.root, self.exam)


if __name__ == "__main__":
    unittest.main()
