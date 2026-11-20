"""Create personalized answer sheets from a roster and a DOCX template."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from openpyxl import Workbook
from pypdf import PdfReader, PdfWriter

from .grade_review import roster_ids
from .grading import safe_append
from .regions import atomic_json, config_hash, file_hash, resolve_path
from .rubric import verify_exam_config

PLACEHOLDERS = {"Name:": "name", "Student ID:": "id", "Class:": "class"}
PLACEHOLDER_PATTERN = re.compile("|".join(re.escape(key) for key in PLACEHOLDERS))


def template_paragraphs(document):
    seen = set()
    roots = [document.element.body]
    for section in document.sections:
        for area in (section.header, section.first_page_header, section.even_page_header,
                     section.footer, section.first_page_footer, section.even_page_footer):
            roots.append(area._element)
    for root in roots:
        for paragraph in root.iter(qn("w:p")):
            if paragraph not in seen:
                seen.add(paragraph)
                yield paragraph


def paragraph_text_groups(paragraph):
    groups = []
    current = []
    field_depth = 0
    field_text = []
    barriers = {qn("w:tab"), qn("w:br"), qn("w:cr"), qn("w:drawing"), qn("w:pict"),
                qn("w:instrText"), qn("w:delText"), qn("w:noBreakHyphen"),
                qn("w:softHyphen"), qn("w:del")}
    skipped = {qn("w:pPr"), qn("w:rPr"), qn("w:sdtPr")}

    def finish():
        if current:
            groups.append(current[:])
            current.clear()

    def visit(element):
        nonlocal field_depth
        if element is not paragraph and element.tag == qn("w:p"):
            finish()
            return
        if element.tag == qn("w:fldSimple"):
            finish()
            text = "".join(node.text or "" for node in element.iter(qn("w:t")))
            if PLACEHOLDER_PATTERN.search(text):
                raise ValueError("Answer sheet placeholders cannot be in Word fields")
            return
        if element.tag == qn("w:fldChar"):
            finish()
            kind = element.get(qn("w:fldCharType"))
            if kind == "begin":
                field_depth += 1
            elif kind == "end" and field_depth:
                field_depth -= 1
                if field_depth == 0:
                    if PLACEHOLDER_PATTERN.search("".join(field_text)):
                        raise ValueError("Answer sheet placeholders cannot be in Word fields")
                    field_text.clear()
            elif kind in {"separate", "end"} and not field_depth:
                raise ValueError("Multi-paragraph Word fields in answer sheet templates are unsupported")
            return
        if element.tag in barriers:
            finish()
            return
        if element.tag in skipped:
            return
        if element.tag == qn("w:t"):
            if field_depth:
                field_text.append(element.text or "")
            else:
                current.append(element)
            return
        for child in element:
            visit(child)

    visit(paragraph)
    finish()
    if field_depth:
        raise ValueError("Multi-paragraph Word fields in answer sheet templates are unsupported")
    return groups


def set_text(element, value):
    element.text = value
    if value and (value[0].isspace() or value[-1].isspace()):
        element.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def replace_placeholders(document, student):
    found = set()
    for paragraph in template_paragraphs(document):
        for nodes in paragraph_text_groups(paragraph):
            text = "".join(node.text or "" for node in nodes)
            for match in reversed(list(PLACEHOLDER_PATTERN.finditer(text))):
                start, end = match.span()
                offsets = []
                offset = 0
                for node in nodes:
                    offsets.append(offset)
                    offset += len(node.text or "")
                first = next(i for i, node in enumerate(nodes) if offsets[i] + len(node.text or "") > start)
                last = next(i for i, node in enumerate(nodes) if offsets[i] + len(node.text or "") >= end)
                prefix = (nodes[first].text or "")[:start - offsets[first]]
                suffix = (nodes[last].text or "")[end - offsets[last]:]
                key = match.group()
                replacement = prefix + key + " " + student[PLACEHOLDERS[key]]
                if first == last:
                    set_text(nodes[first], replacement + suffix)
                else:
                    set_text(nodes[first], replacement)
                    for node in nodes[first + 1:last]:
                        set_text(node, "")
                    set_text(nodes[last], suffix)
                found.add(key)
    missing = set(PLACEHOLDERS) - found
    if missing:
        raise ValueError("Answer sheet template is missing placeholders: " + ", ".join(sorted(missing)))


def prepare_inputs(config, config_dir, config_path):
    exam = config.get("exam")
    if not isinstance(exam, dict):
        raise ValueError("exam must be a mapping")
    for field in ("roster", "roster_sheet", "answer_sheet_template", "output_dir"):
        if not isinstance(exam.get(field), str) or not exam[field].strip():
            raise ValueError(f"{field} must be provided")
    source = resolve_path(config_dir, exam["roster"])
    template = resolve_path(config_dir, exam["answer_sheet_template"])
    output = resolve_path(config_dir, exam["output_dir"])
    if not source.is_file() or not template.is_file() or template.suffix.lower() != ".docx":
        raise ValueError("Provide a readable roster and DOCX answer sheet template")
    if output.exists() and not output.is_dir():
        raise ValueError("output_dir exists but is not a directory")
    if any(path == output or output in path.parents for path in (source, template)):
        raise ValueError("output_dir must not contain the input files")
    verify_exam_config(config, config_path)
    source_hash, template_hash = file_hash(source), file_hash(template)
    roster = roster_ids(config, config_dir)
    first_id = next(iter(roster))
    document = Document(template)
    replace_placeholders(document, {"id": first_id, **roster[first_id]})
    if file_hash(source) != source_hash or file_hash(template) != template_hash:
        raise ValueError("Roster or template changed during validation")
    return source, template, output, roster, source_hash, template_hash


def generate_answer_sheets(config, config_dir, config_path):
    source, template, output, roster, source_hash, template_hash = prepare_inputs(config, config_dir, config_path)
    converter = shutil.which("libreoffice") or shutil.which("soffice")
    if not converter:
        raise ValueError("LibreOffice is required to convert DOCX to PDF")
    output.mkdir(parents=True, exist_ok=True)
    target = output / "answer-sheets"
    try:
        target.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("Answer sheets already exist; do not overwrite the existing run") from exc
    pdf_dir = target / "pdfs"
    pdf_dir.mkdir(mode=0o700)
    writer = PdfWriter()
    index_book = Workbook()
    index = index_book.active
    index.title = "Students"
    index.append(["ID", "NAME", "CLASS", "PDF", "First combined page", "Pages"])
    expected_pages = None
    total_pages = 0
    try:
        with tempfile.TemporaryDirectory(prefix=".conversion-", dir=target) as temporary:
            work = Path(temporary)
            profile = (work / "profile").as_uri()
            for number, (student_id, fields) in enumerate(roster.items(), start=1):
                document = Document(template)
                replace_placeholders(document, {"id": student_id, **fields})
                stem = f"student-{number:05d}"
                docx = work / f"{stem}.docx"
                document.save(docx)
                result = subprocess.run(
                    [converter, f"-env:UserInstallation={profile}", "--headless", "--convert-to", "pdf",
                     "--outdir", str(pdf_dir), str(docx)],
                    capture_output=True, timeout=120, check=False,
                )
                pdf = pdf_dir / f"{stem}.pdf"
                if result.returncode or not pdf.is_file():
                    raise ValueError(
                        f"PDF conversion failed for roster row {number}; check LibreOffice and output directory "
                        "access (Snap installations may not access /tmp)"
                    )
                reader = PdfReader(pdf)
                pages = len(reader.pages)
                if not pages or (expected_pages is not None and pages != expected_pages):
                    raise ValueError("Generated answer sheets have inconsistent or empty page counts")
                expected_pages = pages
                for page in reader.pages:
                    writer.add_page(page)
                safe_append(index, [student_id, fields["name"], fields["class"], f"pdfs/{stem}.pdf",
                                    total_pages + 1, pages])
                total_pages += pages
        layout = config.get("layout") or {}
        configured_pages = layout.get("pages_per_student") if isinstance(layout, dict) else None
        if configured_pages is not None and (type(configured_pages) is not int or configured_pages != expected_pages):
            raise ValueError("Generated page count does not match layout.pages_per_student")
        verify_exam_config(config, config_path)
        if file_hash(source) != source_hash or file_hash(template) != template_hash:
            raise ValueError("Roster or template changed during generation")
        with (target / "combined.pdf").open("xb") as file:
            writer.write(file)
        index_book.save(target / "students.xlsx")
        if len(PdfReader(target / "combined.pdf").pages) != total_pages:
            raise ValueError("Combined answer sheet page count is incorrect")
        atomic_json(target / "manifest.json", {
            "version": 1, "completed": True, "students": len(roster), "pages_per_student": expected_pages,
            "roster_sha256": source_hash, "template_sha256": template_hash, "config_sha256": config_hash(config),
        })
    finally:
        writer.close()
        index_book.close()
    return target
