"""Validate human identity and score review before reporting."""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import load_workbook

from .grading import approved_inputs
from .regions import atomic_json, config_hash, file_hash, resolve_path
from .rubric import question_key
from .run_context import run_directory


def roster_ids(config, config_dir):
    source = resolve_path(config_dir, config["exam"]["roster"])
    workbook = load_workbook(source, read_only=True, data_only=True)
    try:
        rows = workbook[config["exam"]["roster_sheet"]].iter_rows(values_only=True)
        header = [str(value).strip().upper() if value is not None else "" for value in next(rows, ())]
        if any(header.count(name) != 1 for name in ("ID", "NAME", "CLASS")):
            raise ValueError("Roster must contain exactly one ID, NAME and CLASS column")
        columns = [header.index(name) for name in ("ID", "NAME", "CLASS")]
        students = {}
        for row in rows:
            if not any(value is not None and str(value).strip() for value in row):
                continue
            values = [row[index] if index < len(row) else None for index in columns]
            if any(value is None or not str(value).strip() for value in values):
                raise ValueError("Roster contains incomplete rows")
            student_id = str(values[0]).strip()
            if student_id in students:
                raise ValueError("Roster contains duplicate IDs")
            students[student_id] = {"name": str(values[1]).strip(), "class": str(values[2]).strip()}
        if not students:
            raise ValueError("Roster is empty")
        return students
    finally:
        workbook.close()


def verify_grading_run(config, config_dir, config_path, *, require_completed=True, run_id=None, _seen=(),
                       allow_updated_regions=False):
    from .attendance import approved_attendance

    if run_id in _seen or len(_seen) >= 32:
        raise ValueError("Invalid or cyclic grading run lineage")
    questions, regions_root, region_manifest, _ = approved_inputs(config, config_dir, config_path)
    attendance = approved_attendance(config, config_dir, config_path)
    root = run_directory(config, config_dir, run_id) / "grading"
    with (root / "manifest.json").open(encoding="utf-8") as file:
        manifest = json.load(file)
    if run_id is None:
        rubric = resolve_path(config_dir, config["exam"]["output_dir"]) / "rubric" / "rubric.xlsx"
        parent = None
    else:
        from .regrading import validate_child_lineage

        questions, rubric, parent = validate_child_lineage(config, config_dir, config_path, run_id, manifest,
                                                           _seen + (run_id,))
    regions_approval_match = (
        manifest.get("regions_approval_sha256") == file_hash(regions_root / "approval.json")
        and manifest.get("annotations_sha256") == file_hash(regions_root / "annotations.json")
    )
    if (
        manifest.get("version") != (1 if run_id is None else 2)
        or manifest.get("exam_config_sha256") != config_hash(config)
        or manifest.get("pdf_sha256") != region_manifest["pdf_sha256"]
        or manifest.get("roster_sha256") != file_hash(resolve_path(config_dir, config["exam"]["roster"]))
        or manifest.get("rubric_sha256") != file_hash(rubric)
        or (not regions_approval_match and not allow_updated_regions)
        or manifest.get("model_config_sha256") != file_hash(manifest["model_config_path"])
        or manifest.get("attendance_approval_sha256") != (attendance[1] if attendance else None)
    ):
        raise ValueError("Grading inputs or approvals changed; this run is no longer valid")
    image_hashes = manifest.get("image_sha256")
    if not isinstance(image_hashes, list) or not image_hashes:
        raise ValueError("Grading image manifest is invalid")
    for index, digest in enumerate(image_hashes):
        if file_hash(root / "images" / f"{index}.jpg") != digest:
            raise ValueError("A grading image changed")
    with (root / "results.json").open(encoding="utf-8") as file:
        results = json.load(file)
    if results.get("version") != 1 or (require_completed and results.get("completed") is not True):
        raise ValueError("Grading did not complete; the partial run cannot be approved")
    if parent is not None:
        from .regrading import validate_child_checkpoint

        validate_child_checkpoint(manifest, results, parent)
    return root, manifest, results, questions


def reviewed_rows(workbook, sheet_name, columns):
    if sheet_name not in workbook.sheetnames:
        raise ValueError(f"Review workbook is missing the {sheet_name} sheet")
    rows = workbook[sheet_name].iter_rows()
    header = [str(cell.value).strip().lower() if cell.value is not None else "" for cell in next(rows, ())]
    if any(header.count(name) != 1 for name in columns):
        raise ValueError(f"{sheet_name} sheet is missing required review columns")
    positions = [header.index(name) for name in columns]
    for row in rows:
        if not any(cell.value is not None for cell in row):
            continue
        cells = [row[index] if index < len(row) else None for index in positions]
        if any(cell is None or cell.data_type == "f" for cell in cells):
            raise ValueError("Review fields cannot contain formulas")
        yield dict(zip(columns, [cell.value for cell in cells]))


def confirmed(value):
    return isinstance(value, str) and value.strip().lower() == "yes"


def validate_review(config, config_dir, config_path, *, run_id=None, _seen=()):
    from .attendance import present_roster

    root, manifest, results, questions = verify_grading_run(config, config_dir, config_path,
                                                             run_id=run_id, _seen=_seen)
    roster = present_roster(config, config_dir, config_path)
    expected_scripts = set(range(len(roster)))
    scripts = results.get("scripts")
    raw_marks = results.get("marks")
    if not isinstance(scripts, list) or not isinstance(raw_marks, list):
        raise ValueError("Grading results have an invalid structure")
    indices = [script.get("script_index") for script in scripts]
    if (len(indices) != len(expected_scripts) or any(type(index) is not int for index in indices)
            or set(indices) != expected_scripts):
        raise ValueError("Grading results do not cover the roster")
    rubric = {question_key(q["question_number"]): q for q in questions}
    expected_marks = {(index, key) for index in expected_scripts for key in rubric}
    baseline = {}
    for item in raw_marks:
        pair = (item.get("script_index"), question_key(item.get("question", "")))
        if pair not in expected_marks or pair in baseline:
            raise ValueError("Grading results contain unexpected or duplicate marks")
        baseline[pair] = item
    if set(baseline) != expected_marks:
        raise ValueError("Grading results are incomplete")
    path = root / "review.xlsx"
    before = file_hash(path)
    workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    try:
        identities = {}
        for row in reviewed_rows(workbook, "Scripts", ("script_index", "reviewed_id", "reviewed")):
            index = row["script_index"]
            if type(index) is not int or index not in expected_scripts or index in identities:
                raise ValueError("Review contains an invalid or duplicate script")
            if not confirmed(row["reviewed"]) or row["reviewed_id"] is None:
                raise ValueError("Every script ID needs explicit human review")
            student_id = str(row["reviewed_id"]).strip()
            if student_id not in roster or student_id in identities.values():
                raise ValueError("Reviewed IDs must match distinct roster IDs")
            identities[index] = student_id
        if set(identities) != expected_scripts or set(identities.values()) != set(roster):
            raise ValueError("Reviewed IDs do not cover the complete roster")
        marks = {}
        corrections = {}
        for row in reviewed_rows(workbook, "Marks", (
                "script_index", "question", "answer", "reasoning", "manual_mark", "reviewed")):
            if type(row["script_index"]) is not int or row["question"] is None:
                raise ValueError("Review contains an invalid script or question")
            pair = (row["script_index"], question_key(row["question"]))
            if pair not in expected_marks or pair in marks:
                raise ValueError("Review contains an invalid or duplicate mark")
            if not confirmed(row["reviewed"]):
                raise ValueError("Every mark needs explicit human review")
            original = baseline[pair]
            answer = row["answer"] if row["answer"] is not None else ""
            reasoning = row["reasoning"] if row["reasoning"] is not None else ""
            if not isinstance(answer, str) or not isinstance(reasoning, str):
                raise ValueError("Reviewed answers and reasoning must be text")
            manual = row["manual_mark"]
            has_manual = manual is not None and str(manual).strip() != ""
            if (original["grading_failed"] or original["moderator_flag"]) and not has_manual:
                raise ValueError("Failed or flagged grades require a manual mark")
            if answer != original["answer"] and not has_manual:
                raise ValueError("A corrected answer requires a manual mark")
            mark = manual if has_manual else original["moderated_mark"]
            if isinstance(mark, bool):
                raise ValueError("Marks must be numeric")
            try:
                mark = float(mark)
            except (TypeError, ValueError):
                raise ValueError("Marks must be numeric") from None
            if not math.isfinite(mark) or not 0 <= mark <= rubric[pair[1]]["marks"]:
                raise ValueError("A reviewed mark is outside its question's range")
            marks[pair] = mark
            corrections[pair] = (answer, reasoning)
        if set(marks) != expected_marks:
            raise ValueError("Reviewed marks do not cover every student and question")
    finally:
        workbook.close()
    if file_hash(path) != before:
        raise ValueError("Review workbook changed during validation")
    results = dict(results)
    results["marks"] = [
        {**item, "answer_original": item["answer"], "reasoning_original": item["reasoning"],
         "answer": corrections[(item["script_index"], question_key(item["question"]))][0],
         "reasoning": corrections[(item["script_index"], question_key(item["question"]))][1]}
        for item in raw_marks
    ]
    return root, manifest, results, questions, roster, identities, marks


def approve_grading(config, config_dir, config_path, *, run_id=None):
    proposed_root = run_directory(config, config_dir, run_id) / "grading"
    reviewed_hash = file_hash(proposed_root / "review.xlsx")
    results_hash = file_hash(proposed_root / "results.json")
    root, manifest, results, questions, roster, identities, marks = validate_review(
        config, config_dir, config_path, run_id=run_id
    )
    verify_grading_run(config, config_dir, config_path, run_id=run_id)
    if file_hash(root / "review.xlsx") != reviewed_hash or file_hash(root / "results.json") != results_hash:
        raise ValueError("Grading review changed during approval")
    atomic_json(root / "approval.json", {
        "version": 1, "review_sha256": reviewed_hash, "results_sha256": results_hash,
        "manifest_sha256": file_hash(root / "manifest.json"),
        "approved_at": datetime.now(timezone.utc).isoformat(),
    })
    return len(identities), len(marks)


def read_approved_grading(config, config_dir, config_path, *, run_id=None, _seen=()):
    validated = validate_review(config, config_dir, config_path, run_id=run_id, _seen=_seen)
    root = validated[0]
    with (root / "approval.json").open(encoding="utf-8") as file:
        approval = json.load(file)
    if (
        approval.get("version") != 1
        or approval.get("review_sha256") != file_hash(root / "review.xlsx")
        or approval.get("results_sha256") != file_hash(root / "results.json")
        or approval.get("manifest_sha256") != file_hash(root / "manifest.json")
    ):
        raise ValueError("Grading review changed since approval")
    return validated
