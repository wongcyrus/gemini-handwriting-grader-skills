"""Generate reviewed score workbooks and scored script PDFs."""

import math
import re
import unicodedata

from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter

from .grade_review import read_approved_grading
from .grading import safe_append
from .regions import atomic_json, file_hash, resolve_path
from .rubric import question_key


def script_pdf_name(student_id, name, script_index):
    def component(value):
        text = unicodedata.normalize("NFC", str(value))
        text = re.sub(r"[^\w-]+", "_", text).strip("_") or "student"
        # Bound bytes rather than characters for portable Unicode filenames.
        return text.encode("utf-8")[:96].decode("utf-8", errors="ignore")

    return f"{component(student_id)}_{component(name)}_script-{script_index + 1:05d}.pdf"


def report_rows(validated):
    _, _, results, questions, roster, identities, marks = validated
    baselines = {
        (item["script_index"], question_key(item["question"])): item
        for item in results["marks"]
    }
    rows = []
    for index in sorted(identities):
        student_id = identities[index]
        question_marks = [marks[(index, question_key(q["question_number"]))] for q in questions]
        rows.append({
            "script_index": index,
            "id": student_id,
            "name": roster[student_id]["name"],
            "class": roster[student_id]["class"],
            "marks": question_marks,
            "total": math.fsum(question_marks),
            "pdf": f"pdfs/{script_pdf_name(student_id, roster[student_id]['name'], index)}",
        })
    return rows, baselines


def write_workbooks(root, rows, baselines, questions):
    summary = Workbook()
    sheet = summary.active
    sheet.title = "Marks"
    sheet.append(["ID", "NAME", "CLASS", "Marks", "Script"])
    for row in rows:
        safe_append(sheet, [row["id"], row["name"], row["class"], row["total"], row["pdf"]])
    summary.save(root / "score_report.xlsx")
    summary.close()

    details = Workbook()
    marks_sheet = details.active
    marks_sheet.title = "Marks"
    safe_append(marks_sheet, ["ID", "NAME", "CLASS", *[q["question_number"] for q in questions], "Marks", "Script"])
    answer_sheet = details.create_sheet("Answers")
    safe_append(answer_sheet, ["ID", "NAME", "CLASS", *[q["question_number"] for q in questions]])
    reasoning_sheet = details.create_sheet("Reasoning")
    safe_append(reasoning_sheet, ["ID", "NAME", "CLASS", *[q["question_number"] for q in questions]])
    answers_raw = details.create_sheet("AnswersRaw")
    answers_raw.append(["ID", "Question", "Answer"])
    reasoning_raw = details.create_sheet("ReasoningRaw")
    reasoning_raw.append(["ID", "Question", "Reasoning", "Moderator flag", "Moderator note", "Similarity"])
    for row in rows:
        values = [baselines[(row["script_index"], question_key(q["question_number"]))] for q in questions]
        safe_append(marks_sheet, [row["id"], row["name"], row["class"], *row["marks"], row["total"], row["pdf"]])
        safe_append(answer_sheet, [row["id"], row["name"], row["class"], *[v["answer"] for v in values]])
        safe_append(reasoning_sheet, [row["id"], row["name"], row["class"], *[v["reasoning"] for v in values]])
        for question, value in zip(questions, values):
            safe_append(answers_raw, [row["id"], question["question_number"],
                                      value.get("answer_original", value["answer"])])
            safe_append(reasoning_raw, [row["id"], question["question_number"],
                                        value.get("reasoning_original", value["reasoning"]),
                                        value["moderator_flag"], value["moderator_note"],
                                        value.get("similarity_score")])
    details.save(root / "details_score_report.xlsx")
    details.close()


def scored_scripts(root, grading_root, regions_root, rows, questions, pages_per_script, effective_crops=None):
    import json

    with (regions_root / "annotations.json").open(encoding="utf-8") as file:
        annotations = json.load(file)
    boxes = {
        question_key(box["label"]): (int(page), box)
        for page, page_boxes in annotations.items() for box in page_boxes
    }
    pdf_dir = root / "pdfs"
    pdf_dir.mkdir(mode=0o700)
    for row in rows:
        images = []
        try:
            for page in range(pages_per_script):
                source = grading_root / "images" / f"{row['script_index'] * pages_per_script + page}.jpg"
                with Image.open(source) as image:
                    image = image.convert("RGB")
                draw = ImageDraw.Draw(image)
                font = ImageFont.load_default(size=max(18, image.width // 65))
                for question, mark in zip(questions, row["marks"]):
                    box_page, box = boxes[question_key(question["question_number"])]
                    key = question_key(question["question_number"])
                    if key in (effective_crops or {}):
                        box = effective_crops[key][row["script_index"]]
                    if box_page == page:
                        label = f"{mark:g}"
                        text_w = draw.textlength(label, font=font)
                        x = max(0, min(image.width - text_w - 6, box["x"] + box["width"] - text_w - 6))
                        y = max(0, box["y"] + 2)
                        draw.text((x, y), label, fill="red", font=font, stroke_width=2, stroke_fill="white")
                if page == 0:
                    draw.text((10, 10), f"Total: {row['total']:g}", fill="red",
                              font=font, stroke_width=1, stroke_fill="white")
                images.append(image)
            path = root / row["pdf"]
            images[0].save(path, "PDF", resolution=200.0, save_all=True, append_images=images[1:])
            if len(PdfReader(path).pages) != pages_per_script:
                raise ValueError("A generated PDF has the wrong number of pages")
        finally:
            for image in images:
                image.close()


def merge_pdfs(root, rows, name):
    writer = PdfWriter()
    try:
        for row in rows:
            writer.append(str(root / row["pdf"]))
        with (root / name).open("xb") as file:
            writer.write(file)
    finally:
        writer.close()


def sample_rows(rows, n):
    count = min(n, len(rows) // 3)
    if not count:
        return []
    ranked = sorted(rows, key=lambda row: (-row["total"], row["script_index"]))
    middle = max(count, min(len(rows) - 2 * count, (len(rows) - count) // 2))
    return ranked[:count] + ranked[middle:middle + count] + ranked[-count:]


def passing_policy(config, questions):
    reporting = config.get("reporting") or {}
    if not isinstance(reporting, dict):
        raise ValueError("reporting must be a mapping")
    threshold = reporting.get("pass_mark")
    inclusive = reporting.get("passing_includes_equal")
    if threshold is None and inclusive is None:
        return None
    if (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
            or not math.isfinite(threshold) or not 0 <= threshold <= sum(q["marks"] for q in questions)
            or type(inclusive) is not bool):
        raise ValueError("Set a valid pass_mark and passing_includes_equal together")
    return threshold, inclusive


def question_passing_policy(config, questions):
    reporting = config.get("reporting") or {}
    if not isinstance(reporting, dict):
        raise ValueError("reporting must be a mapping")
    thresholds = reporting.get("question_pass_marks")
    inclusive = reporting.get("question_passing_includes_equal")
    if thresholds is None and inclusive is None:
        return {}, None
    if not isinstance(thresholds, dict) or not thresholds or type(inclusive) is not bool:
        raise ValueError("Set question_pass_marks and question_passing_includes_equal together")
    maxima = {question_key(q["question_number"]): q["marks"] for q in questions}
    policy = {}
    for label, threshold in thresholds.items():
        if not isinstance(label, (str, int)) or isinstance(label, bool):
            raise ValueError("Question pass thresholds must use question labels")
        key = question_key(label)
        if (key not in maxima or key in policy or isinstance(threshold, bool)
                or not isinstance(threshold, (int, float)) or not math.isfinite(threshold)
                or not 0 <= threshold <= maxima[key]):
            raise ValueError("Question pass thresholds must be unique, known and within the question maximum")
        policy[key] = threshold
    return policy, inclusive


def passing_rows(rows, threshold, inclusive):
    if inclusive:
        return [row for row in rows if row["total"] >= threshold]
    return [row for row in rows if row["total"] > threshold]


def create_reports(config, config_dir, config_path, *, run_id=None):
    from .attendance import approved_attendance

    validated = read_approved_grading(config, config_dir, config_path, run_id=run_id)
    grading_root, manifest, _, questions, _, _, _ = validated
    policy = passing_policy(config, questions)
    output = grading_root.parent
    root = output / "reports"
    try:
        root.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("Reports already exist; choose a different output directory") from exc
    review_hash = file_hash(grading_root / "review.xlsx")
    approval_hash = file_hash(grading_root / "approval.json")
    rows, baselines = report_rows(validated)
    write_workbooks(root, rows, baselines, questions)
    attendance = approved_attendance(config, config_dir, config_path)
    if attendance:
        book = Workbook()
        sheet = book.active
        sheet.title = "Absent"
        sheet.append(["ID", "NAME", "CLASS", "STATUS"])
        for student in attendance[0]["students"]:
            if student["status"] == "absent":
                safe_append(sheet, [student["id"], student["name"], student["class"], "absent"])
        book.save(root / "absences.xlsx")
        book.close()
    regions = resolve_path(config_dir, config["exam"]["output_dir"]) / "regions"
    scored_scripts(root, grading_root, regions, rows, questions, config["layout"]["pages_per_student"],
                   manifest.get("effective_crops", {}))
    merge_pdfs(root, rows, "all.pdf")
    created_samples = set()
    for count in (3, 5):
        samples = sample_rows(rows, count)
        actual = len(samples) // 3
        if actual and actual not in created_samples:
            merge_pdfs(root, samples, f"sampleOf{actual}.pdf")
            created_samples.add(actual)
    if policy is not None:
        threshold, inclusive = policy
        passing = passing_rows(rows, threshold, inclusive)
        created_samples = set()
        for count in (3, 5):
            samples = sample_rows(passing, count)
            actual = len(samples) // 3
            if actual and actual not in created_samples:
                merge_pdfs(root, samples, f"sampleOf{actual}_only_pass.pdf")
                created_samples.add(actual)
    read_approved_grading(config, config_dir, config_path, run_id=run_id)
    if file_hash(grading_root / "review.xlsx") != review_hash or file_hash(grading_root / "approval.json") != approval_hash:
        raise ValueError("Grading approval changed during report generation; discard the partial reports")
    atomic_json(root / "manifest.json", {
        "version": 2, "review_sha256": review_hash, "grading_approval_sha256": approval_hash,
        "students": len(rows), "completed": True,
        "script_pdf_sha256": {row["pdf"]: file_hash(root / row["pdf"]) for row in rows},
    })
    return root
