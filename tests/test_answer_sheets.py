import json
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from docx import Document
from docx.enum.section import WD_SECTION
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn
from PIL import Image
from openpyxl import Workbook, load_workbook
from pypdf import PdfReader, PdfWriter

from handwriting_grader.answer_sheets import prepare_inputs, replace_placeholders
from handwriting_grader.cli import main
from handwriting_grader.regions import file_hash


class AnswerSheetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="answer-sheet-test-", dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Roster"
        sheet.append(["ID", "NAME", "CLASS"])
        sheet.append(["001", "Alice Example", "Class A"])
        sheet.append(["002", "Bob Example", "Class B"])
        workbook.save(self.root / "roster.xlsx")
        workbook.close()
        document = Document()
        paragraph = document.add_paragraph()
        paragraph.add_run("Na")
        paragraph.add_run("me:")
        table = document.add_table(rows=2, cols=1)
        table.cell(0, 0).text = "Student ID:"
        table.cell(1, 0).text = "Class:"
        document.save(self.root / "template.docx")
        self.config = {
            "exam": {"roster": "roster.xlsx", "roster_sheet": "Roster", "answer_sheet_template": "template.docx",
                     "output_dir": "output"},
            "layout": {"pages_per_student": 1},
        }
        self.exam = self.root / "exam.yaml"
        self.exam.write_text(yaml.safe_dump(self.config))

    @unittest.skipUnless(shutil.which("libreoffice") or shutil.which("soffice"), "LibreOffice is required")
    def test_real_conversion_generates_personalized_pdfs_without_scan(self):
        source_hash = file_hash(self.root / "template.docx")
        roster_hash = file_hash(self.root / "roster.xlsx")
        self.assertEqual(main(["answer-sheets", "--exam", str(self.exam)]), 0)
        root = self.root / "output" / "answer-sheets"
        combined = PdfReader(root / "combined.pdf")
        self.assertEqual(len(combined.pages), 2)
        self.assertIn("Alice Example", combined.pages[0].extract_text())
        self.assertIn("001", combined.pages[0].extract_text())
        self.assertIn("Bob Example", combined.pages[1].extract_text())
        self.assertIn("Class B", combined.pages[1].extract_text())
        self.assertEqual(len(PdfReader(root / "pdfs" / "student-00001.pdf").pages), 1)
        book = load_workbook(root / "students.xlsx", read_only=True)
        try:
            rows = list(book["Students"].values)
            self.assertEqual(rows[1], ("001", "Alice Example", "Class A", "pdfs/student-00001.pdf", 1, 1))
            self.assertEqual(rows[2][4], 2)
        finally:
            book.close()
        self.assertTrue(json.loads((root / "manifest.json").read_text())["completed"])
        self.assertEqual(file_hash(self.root / "template.docx"), source_hash)
        self.assertEqual(file_hash(self.root / "roster.xlsx"), roster_hash)
        self.assertEqual(main(["answer-sheets", "--exam", str(self.exam)]), 1)

    def test_missing_placeholder_and_duplicate_roster_fail_without_outputs(self):
        document = Document()
        document.add_paragraph("Name:")
        document.save(self.root / "template.docx")
        self.assertEqual(main(["answer-sheets", "--exam", str(self.exam)]), 1)
        self.assertFalse((self.root / "output").exists())
        book = load_workbook(self.root / "roster.xlsx")
        book["Roster"]["D1"] = "ID"
        book.save(self.root / "roster.xlsx")
        book.close()
        self.assertEqual(main(["answer-sheets", "--exam", str(self.exam)]), 1)
        self.assertFalse((self.root / "output").exists())

    def test_replacements_preserve_user_values_that_resemble_placeholders(self):
        document = Document()
        document.add_paragraph("Name: Student ID: Class:")
        replace_placeholders(document, {"name": "Student ID: Name:", "id": "001", "class": "Class:"})
        self.assertEqual(document.paragraphs[0].text, "Name: Student ID: Name: Student ID: 001 Class: Class:")

    def rich_template(self):
        document = Document()
        paragraph = document.add_paragraph()
        run = paragraph.add_run("Name:")
        image_bytes = io.BytesIO()
        Image.new("RGB", (4, 4), "red").save(image_bytes, format="PNG")
        image_bytes.seek(0)
        run.add_picture(image_bytes)
        relationship = document.part.relate_to("https://example.invalid", RELATIONSHIP_TYPE.HYPERLINK,
                                               is_external=True)
        hyperlink = OxmlElement("w:hyperlink")
        hyperlink.set(qn("r:id"), relationship)
        for value in ("Student ", "ID:"):
            item = OxmlElement("w:r")
            text = OxmlElement("w:t")
            text.text = value
            item.append(text)
            hyperlink.append(item)
        paragraph._p.append(hyperlink)
        box = parse_xml('''<w:pict xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:v="urn:schemas-microsoft-com:vml">
            <v:shape id="box1" style="width:200pt;height:40pt">
              <v:textbox><w:txbxContent><w:p><w:r><w:t>Class:</w:t></w:r></w:p></w:txbxContent></v:textbox>
            </v:shape></w:pict>''')
        paragraph.add_run()._r.append(box)
        return document, paragraph, hyperlink, box, relationship

    def test_hyperlinks_textboxes_and_inline_drawings_preserve_template_structure(self):
        document, paragraph, hyperlink, box, relationship = self.rich_template()
        replace_placeholders(document, {"name": "Alice", "id": "001", "class": "Class A"})
        self.assertEqual(paragraph._p.xpath("count(.//w:drawing)"), 1)
        self.assertEqual(hyperlink.get(qn("r:id")), relationship)
        self.assertEqual("".join(node.text or "" for node in hyperlink.iter(qn("w:t"))), "Student ID: 001")
        self.assertEqual("".join(node.text or "" for node in box.iter(qn("w:t"))), "Class: Class A")
        self.assertIn("Name: Alice", paragraph.text)
        output = self.root / "personalized.docx"
        document.save(output)
        reopened = Document(output)
        self.assertEqual(len(reopened.element.body.xpath(".//w:drawing")), 1)
        self.assertEqual(len(reopened.element.body.xpath(".//w:txbxContent")), 1)

    @unittest.skipUnless(shutil.which("libreoffice") or shutil.which("soffice"), "LibreOffice is required")
    def test_real_conversion_includes_hyperlink_and_textbox_placeholders(self):
        document, _, _, _, _ = self.rich_template()
        document.save(self.root / "template.docx")
        self.assertEqual(main(["answer-sheets", "--exam", str(self.exam)]), 0)
        pages = PdfReader(self.root / "output" / "answer-sheets" / "combined.pdf").pages
        self.assertEqual(len(pages), 2)
        for page, student, student_id, klass in zip(
                pages, ("Alice Example", "Bob Example"), ("001", "002"), ("Class A", "Class B")):
            text = page.extract_text()
            self.assertIn(student, text)
            self.assertIn(student_id, text)
            self.assertIn(klass, text)

    def test_placeholders_cannot_cross_tab_or_line_break(self):
        document = Document()
        paragraph = document.add_paragraph()
        paragraph.add_run("Na").add_tab()
        paragraph.add_run("me:")
        document.add_paragraph("Student ID: Class:")
        with self.assertRaisesRegex(ValueError, "Name:"):
            replace_placeholders(document, {"name": "Alice", "id": "001", "class": "A"})

    def test_placeholders_in_word_fields_fail_closed(self):
        for field in (
            '<w:fldSimple w:instr="MERGEFIELD Name"><w:r><w:t>Name:</w:t></w:r></w:fldSimple>',
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText> MERGEFIELD Name </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:t>Na</w:t><w:t>me:</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>',
        ):
            document = Document()
            document.add_paragraph("Name: Student ID: Class:")
            paragraph = document.add_paragraph()
            wrapper = parse_xml(f'<w:wrapper xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                                f'{field}</w:wrapper>')
            for child in list(wrapper):
                paragraph._p.append(child)
            with self.assertRaisesRegex(ValueError, "cannot be in Word fields"):
                replace_placeholders(document, {"name": "Alice", "id": "001", "class": "A"})

    def test_unrelated_word_field_is_preserved(self):
        document = Document()
        paragraph = document.add_paragraph("Name: Student ID: Class:")
        field = parse_xml('<w:fldSimple xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                          'w:instr="PAGE"><w:r><w:t>1</w:t></w:r></w:fldSimple>')
        paragraph._p.append(field)
        replace_placeholders(document, {"name": "Alice", "id": "001", "class": "A"})
        self.assertEqual(field.get(qn("w:instr")), "PAGE")
        self.assertEqual(next(field.iter(qn("w:t"))).text, "1")

    def test_linked_headers_are_personalized_only_once(self):
        document = Document()
        document.sections[0].header.paragraphs[0].text = "Name:"
        document.add_paragraph("Student ID: Class:")
        document.add_section(WD_SECTION.NEW_PAGE)
        replace_placeholders(document, {"name": "Alice", "id": "001", "class": "A"})
        self.assertEqual(document.sections[0].header.paragraphs[0].text, "Name: Alice")
        self.assertEqual(document.sections[1].header.paragraphs[0].text, "Name: Alice")

    def test_answer_sheets_use_full_roster_even_with_attendance(self):
        self.config["exam"]["attendance"] = "attendance.csv"
        (self.root / "attendance.csv").write_text("ID,STATUS\n001,present\n002,absent\n")
        self.exam.write_text(yaml.safe_dump(self.config))
        _, _, _, roster, _, _ = prepare_inputs(self.config, self.root, self.exam)
        self.assertEqual(set(roster), {"001", "002"})

    def test_conversion_failure_leaves_no_completion_marker(self):
        with patch("handwriting_grader.answer_sheets.shutil.which", return_value="/fake/libreoffice"), \
             patch("handwriting_grader.answer_sheets.subprocess.run") as run:
            run.return_value.returncode = 1
            self.assertEqual(main(["answer-sheets", "--exam", str(self.exam)]), 1)
        self.assertFalse((self.root / "output" / "answer-sheets" / "manifest.json").exists())

    def test_inconsistent_page_counts_leave_no_completion_marker(self):
        conversions = []

        def fake_conversion(command, **kwargs):
            output = Path(command[command.index("--outdir") + 1])
            docx = Path(command[-1])
            conversions.append(docx)
            writer = PdfWriter()
            for _ in conversions:
                writer.add_blank_page(width=200, height=300)
            with (output / (docx.stem + ".pdf")).open("wb") as file:
                writer.write(file)
            writer.close()
            return type("Result", (), {"returncode": 0})()

        with patch("handwriting_grader.answer_sheets.shutil.which", return_value="/fake/libreoffice"), \
             patch("handwriting_grader.answer_sheets.subprocess.run", side_effect=fake_conversion):
            self.assertEqual(main(["answer-sheets", "--exam", str(self.exam)]), 1)
        self.assertEqual(len(conversions), 2)
        self.assertFalse((self.root / "output" / "answer-sheets" / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
