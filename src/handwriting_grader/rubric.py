"""Extract, validate and approve structured marking schemes."""

import json
import math
import shutil
from pathlib import Path
from datetime import datetime, timezone

import html2text
import mammoth
import yaml
from google import genai
from google.genai import types
from openpyxl import Workbook, load_workbook
from pydantic import BaseModel, StrictStr

from .regions import atomic_json, config_hash, file_hash, resolve_path
from .suggestions import load_model_settings
from .cache import CachedGrader, cache_directory, enabled_agents


class RubricQuestion(BaseModel):
    question_number: StrictStr
    question_text: StrictStr
    marking_scheme: StrictStr
    marks: float


class ExtractedRubric(BaseModel):
    general_grading_guide: StrictStr = ""
    questions: list[RubricQuestion]


PROMPT = """Extract the marking scheme from the document. Return every question
with its question number, complete question text, marking criteria and total
available marks. Preserve sub-question labels. Include any general grading
guidance separately. Do not invent missing information."""

REQUIRED_COLUMNS = ("question_number", "question_text", "marking_scheme", "marks")


def question_key(value):
    label = str(value).strip().upper()
    if label.startswith("Q") and len(label) > 1:
        label = label[1:]
    try:
        number = float(label)
        if math.isfinite(number) and number.is_integer():
            return str(int(number))
    except ValueError:
        pass
    return label


def validate_questions(questions):
    if not questions:
        raise ValueError("The marking scheme contains no questions")
    seen = set()
    for question in questions:
        for field in REQUIRED_COLUMNS[:-1]:
            value = question.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Every question requires {field}")
            if len(value) > 32767:
                raise ValueError(f"{field} exceeds Excel's 32,767-character cell limit")
        label = question["question_number"].strip()
        if label in {".", ".."} or any(char in "/\\" or ord(char) < 32 for char in label):
            raise ValueError("Question numbers cannot contain path separators or control characters")
        key = question_key(label)
        if key in {"NAME", "ID", "CLASS"}:
            raise ValueError("Question numbers cannot use metadata labels")
        if not key or key in seen:
            raise ValueError("The marking scheme contains duplicate question numbers")
        seen.add(key)
        marks = question.get("marks")
        if isinstance(marks, bool) or not isinstance(marks, (int, float)):
            raise ValueError("Question marks must be numeric")
        if not math.isfinite(marks) or marks <= 0:
            raise ValueError("Question marks must be finite and greater than zero")
    return questions


def read_rubric(path):
    workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    try:
        if "Marking Scheme" not in workbook.sheetnames:
            raise ValueError("Workbook requires a Marking Scheme sheet")
        rows = workbook["Marking Scheme"].iter_rows()
        headers = [str(cell.value).strip().lower() if cell.value is not None else "" for cell in next(rows, ())]
        if any(headers.count(column) != 1 for column in REQUIRED_COLUMNS):
            raise ValueError("Marking Scheme sheet requires the four standard columns")
        positions = [headers.index(column) for column in REQUIRED_COLUMNS]
        questions = []
        for row in rows:
            if not any(cell.value is not None for cell in row):
                continue
            cells = [row[index] if index < len(row) else None for index in positions]
            if any(cell is None or cell.data_type == "f" for cell in cells):
                raise ValueError("Required rubric fields cannot be missing or formulas")
            values = [cell.value for cell in cells]
            if isinstance(values[0], (int, float)) and not isinstance(values[0], bool):
                values[0] = str(values[0])
            questions.append(dict(zip(REQUIRED_COLUMNS, values)))
        return validate_questions(questions)
    finally:
        workbook.close()


def write_rubric(path, questions):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Marking Scheme"
    sheet.append(REQUIRED_COLUMNS)
    for question in validate_questions(questions):
        sheet.append([question[key] for key in REQUIRED_COLUMNS])
        for cell in sheet[sheet.max_row][:3]:
            cell.data_type = "s"
    summary = workbook.create_sheet("Summary")
    summary.append(["Questions", "Total marks"])
    summary.append([len(questions), sum(question["marks"] for question in questions)])
    overview = workbook.create_sheet("Question Overview")
    overview.append(["question_number", "marks"])
    for question in questions:
        overview.append([question["question_number"], question["marks"]])
        overview.cell(overview.max_row, 1).data_type = "s"
    validation = workbook.create_sheet("Validation")
    validation.append(["Check", "Result"])
    validation.append(["Required fields and marks", "Passed"])
    try:
        workbook.save(path)
    finally:
        workbook.close()


def docx_to_markdown(path):
    with Path(path).open("rb") as file:
        result = mammoth.convert_to_html(file)
    converter = html2text.HTML2Text()
    converter.body_width = 0
    converter.ignore_images = True
    converter.ignore_links = False
    converter.ignore_tables = False
    text = converter.handle(result.value).strip()
    if not text:
        raise ValueError("The marking scheme document contains no readable text")
    return text


def verify_exam_config(config, config_path):
    with Path(config_path).open(encoding="utf-8") as file:
        current = yaml.safe_load(file)
    if not isinstance(current, dict) or config_hash(current) != config_hash(config):
        raise ValueError("Exam configuration changed; start again")


def rubric_root(config, config_dir):
    return resolve_path(config_dir, config["exam"]["output_dir"]) / "rubric"


def reserve_rubric(config, config_dir):
    output = resolve_path(config_dir, config["exam"]["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    root = output / "rubric"
    try:
        root.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("A rubric run already exists; choose a different output directory") from exc
    return root


def import_rubric(config, config_dir, config_path):
    source = resolve_path(config_dir, config["exam"]["marking_scheme"])
    if source.suffix.lower() != ".xlsx":
        raise ValueError("Import requires an existing XLSX marking scheme")
    source_digest = file_hash(source)
    read_rubric(source)
    verify_exam_config(config, config_path)
    if file_hash(source) != source_digest:
        raise ValueError("Marking scheme changed while importing")
    root = reserve_rubric(config, config_dir)
    with source.open("rb") as input_file, (root / "rubric.xlsx").open("xb") as output_file:
        shutil.copyfileobj(input_file, output_file)
    if file_hash(root / "rubric.xlsx") != source_digest:
        raise ValueError("Imported rubric differs from source; inspect the run")
    atomic_json(root / "manifest.json", {
        "version": 1, "exam_config_sha256": config_hash(config), "source_sha256": source_digest,
        "origin": "xlsx",
    })
    return root / "rubric.xlsx"


def extract_rubric(config, config_dir, config_path, model_config_path, project, location):
    source = resolve_path(config_dir, config["exam"]["marking_scheme"])
    if source.suffix.lower() != ".docx":
        raise ValueError("AI extraction requires a DOCX marking scheme")
    if rubric_root(config, config_dir).exists():
        raise ValueError("A rubric run already exists; choose a different output directory")
    model_path = Path(model_config_path).expanduser().resolve()
    model_hash = file_hash(model_path)
    model = load_model_settings(model_path, "marking_scheme")
    if file_hash(model_path) != model_hash:
        raise ValueError("Model configuration changed while loading")
    source_hash = file_hash(source)
    content = docx_to_markdown(source)
    cache_root = resolve_path(config_dir, config["exam"]["output_dir"]) / "model-cache" / "rubric"
    agents = [name for name in enabled_agents(model_path) if name == "marking_scheme"]
    cache = CachedGrader(None, cache_directory(model_path, cache_root) if agents else cache_root / "cache", agents,
                         {"marking_scheme": model}, project, location, source_files=["rubric.py"])
    options = types.HttpOptions(timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1))
    with genai.Client(vertexai=True, project=project, location=location, http_options=options) as client:
        def compute():
            response = client.models.generate_content(
                model=model,
                contents=[PROMPT, content],
                config=types.GenerateContentConfig(
                    temperature=0, max_output_tokens=16384,
                    response_mime_type="application/json", response_schema=ExtractedRubric,
                ),
            )
            return (ExtractedRubric.model_validate(response.parsed) if response.parsed is not None
                    else ExtractedRubric.model_validate_json(response.text)).model_dump()

        def validate(value):
            extracted = ExtractedRubric.model_validate(value)
            validate_questions([question.model_dump() for question in extracted.questions])
            return extracted.model_dump()

        extracted = ExtractedRubric.model_validate(cache.call(
            "marking_scheme", {"content": content}, compute, validate))
    questions = [question.model_dump() for question in extracted.questions]
    validate_questions(questions)
    if extracted.general_grading_guide.strip():
        for question in questions:
            question["marking_scheme"] += (
                "\n\n---\n\n**General Grading Guide:**\n" + extracted.general_grading_guide.strip()
            )
    verify_exam_config(config, config_path)
    if file_hash(source) != source_hash or file_hash(model_path) != model_hash:
        raise ValueError("Source or model configuration changed during extraction")
    root = reserve_rubric(config, config_dir)
    write_rubric(root / "rubric.xlsx", questions)
    atomic_json(root / "manifest.json", {
        "version": 1, "exam_config_sha256": config_hash(config), "source_sha256": source_hash,
        "origin": "vertex", "model_config_sha256": model_hash,
        "project": project, "location": location, "model": model,
    })
    return root / "rubric.xlsx"


def verify_rubric_source(config, config_dir, config_path):
    verify_exam_config(config, config_path)
    root = rubric_root(config, config_dir)
    with (root / "manifest.json").open(encoding="utf-8") as file:
        manifest = json.load(file)
    source = resolve_path(config_dir, config["exam"]["marking_scheme"])
    if (manifest.get("version") != 1 or manifest.get("exam_config_sha256") != config_hash(config)
            or manifest.get("source_sha256") != file_hash(source)):
        raise ValueError("Exam configuration or source marking scheme changed")
    return root, manifest


def approve_rubric(config, config_dir, config_path):
    root, manifest = verify_rubric_source(config, config_dir, config_path)
    path = root / "rubric.xlsx"
    before = file_hash(path)
    questions = read_rubric(path)
    if file_hash(path) != before:
        raise ValueError("Rubric changed during validation")
    atomic_json(root / "approval.json", {
        "version": 1, "rubric_sha256": before,
        "exam_config_sha256": manifest["exam_config_sha256"],
        "source_sha256": manifest["source_sha256"],
        "approved_at": datetime.now(timezone.utc).isoformat(),
    })
    return questions


def read_approved_rubric(config, config_dir, config_path):
    root, manifest = verify_rubric_source(config, config_dir, config_path)
    with (root / "approval.json").open(encoding="utf-8") as file:
        approval = json.load(file)
    path = root / "rubric.xlsx"
    before = file_hash(path)
    if (approval.get("version") != 1 or approval.get("rubric_sha256") != before
            or approval.get("exam_config_sha256") != manifest["exam_config_sha256"]
            or approval.get("source_sha256") != manifest["source_sha256"]):
        raise ValueError("The rubric has changed since approval")
    questions = read_rubric(path)
    if file_hash(path) != before:
        raise ValueError("Rubric changed during validation")
    return questions
