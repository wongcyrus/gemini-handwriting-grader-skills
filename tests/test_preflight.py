import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from pypdf import PdfWriter
import yaml

from handwriting_grader.cli import check_exam, main


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Roster"
        sheet.append(["ID", "NAME", "CLASS"])
        sheet.append(["001", "Example", "A"])
        sheet.append(["002", "Example Two", "A"])
        workbook.save(self.root / "roster.xlsx")
        workbook.close()
        pdf = PdfWriter()
        for _ in range(4):
            pdf.add_blank_page(width=100, height=100)
        with (self.root / "scan.pdf").open("wb") as file:
            pdf.write(file)
        (self.root / "rubric.docx").write_bytes(b"fixture")
        self.config = {
            "exam": {
                "name": "Example",
                "roster": "roster.xlsx",
                "roster_sheet": "Roster",
                "scanned_pdf": "scan.pdf",
                "marking_scheme": "rubric.docx",
                "output_dir": "output",
            },
            "layout": {"pages_per_student": 2, "same_layout_for_all": True},
        }

    def test_valid_preflight_does_not_create_output(self):
        errors, summary = check_exam(self.config, self.root)
        self.assertEqual(errors, [])
        self.assertEqual((summary["students"], summary["pdf_pages"]), (2, 4))
        self.assertFalse((self.root / "output").exists())

    def test_page_count_and_layout_require_confirmation(self):
        self.config["layout"] = {"pages_per_student": 1, "same_layout_for_all": False}
        errors, _ = check_exam(self.config, self.root)
        self.assertTrue(any("page count" in error for error in errors))
        self.assertTrue(any("same_layout_for_all" in error for error in errors))

    def test_duplicate_roster_ids_block_preflight(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Roster"
        sheet.append(["ID", "NAME", "CLASS"])
        sheet.append(["001", "Example", "A"])
        sheet.append(["001", "Another", "A"])
        workbook.save(self.root / "roster.xlsx")
        workbook.close()
        errors, _ = check_exam(self.config, self.root)
        self.assertIn("roster contains duplicate IDs", errors)

    def test_output_must_not_contain_inputs(self):
        self.config["exam"]["output_dir"] = "."
        errors, _ = check_exam(self.config, self.root)
        self.assertTrue(any("contains an input file" in error for error in errors))

    def test_missing_exam_fails(self):
        self.assertEqual(main(["preflight", "--exam", str(self.root / "missing.yaml")]), 1)

    def test_cli_loads_config_relative_to_its_location(self):
        config_path = self.root / "exam.yaml"
        config_path.write_text(yaml.safe_dump(self.config), encoding="utf-8")
        self.assertEqual(main(["preflight", "--exam", str(config_path)]), 0)
        self.assertFalse((self.root / "output").exists())


if __name__ == "__main__":
    unittest.main()
