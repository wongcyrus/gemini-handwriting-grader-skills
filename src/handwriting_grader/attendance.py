"""Explicit, reviewed attendance for scanned scripts."""

import csv
import json
import os
import re
import secrets
from pathlib import Path

from .regions import atomic_json, config_hash, file_hash, resolve_path
from .rubric import verify_exam_config


def attendance_path(config, config_dir):
    value = config["exam"].get("attendance")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("attendance must be a CSV path or null")
    source = resolve_path(config_dir, value)
    output = resolve_path(config_dir, config["exam"]["output_dir"])
    if source == output or output in source.parents:
        raise ValueError("Attendance input must be outside the output directory")
    if not source.is_file():
        raise ValueError("Attendance CSV is not a readable file")
    return source


def attendance_rows(config, config_dir):
    from .grade_review import roster_ids

    roster = roster_ids(config, config_dir)
    source = attendance_path(config, config_dir)
    if source is None:
        return roster, None
    statuses = {}
    try:
        with source.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file, strict=True)
            if reader.fieldnames != ["ID", "STATUS"]:
                raise ValueError("Attendance CSV must have exactly the headers ID,STATUS")
            for row in reader:
                student_id, status = row.get("ID"), row.get("STATUS")
                if (None in row or not isinstance(student_id, str) or not isinstance(status, str)
                        or not student_id.strip() or student_id != student_id.strip()
                        or status not in {"present", "absent"}):
                    raise ValueError("Attendance rows require an ID and status present or absent")
                if student_id not in roster or student_id in statuses:
                    raise ValueError("Attendance IDs must be unique and match the roster")
                statuses[student_id] = status
    except csv.Error as exc:
        raise ValueError("Attendance CSV is malformed") from exc
    if set(statuses) != set(roster):
        raise ValueError("Attendance must cover the complete roster")
    if "present" not in statuses.values():
        raise ValueError("Attendance requires at least one present student")
    return roster, statuses


def review_data(config, config_dir, config_path):
    verify_exam_config(config, config_path)
    source = attendance_path(config, config_dir)
    if source is None:
        raise ValueError("Configure exam.attendance before preparing attendance")
    roster_path = resolve_path(config_dir, config["exam"]["roster"])
    before = (file_hash(source), file_hash(roster_path))
    roster, statuses = attendance_rows(config, config_dir)
    verify_exam_config(config, config_path)
    if before != (file_hash(source), file_hash(roster_path)):
        raise ValueError("Roster or attendance changed during review preparation")
    return {
        "version": 1, "config_sha256": config_hash(config),
        "attendance_sha256": before[0], "roster_sha256": before[1],
        "students": [{"id": student_id, **student, "status": statuses[student_id]}
                     for student_id, student in roster.items()],
    }


def _write_attendance_xlsx(path: Path, students: list[dict]) -> None:
    """Write a human-readable Excel workbook for non-technical teacher attendance review."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"
    ws.append(["ID", "Name", "Class", "Status"])
    for student in students:
        row = [student.get("id", ""), student.get("name", ""), student.get("class", ""), student.get("status", "")]
        ws.append(row)
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    with path.open("wb") as file:
        wb.save(file)
    wb.close()


def _validate_attendance_workbook(path: Path, students: list[dict]) -> None:
    from zipfile import BadZipFile

    from openpyxl import load_workbook

    try:
        workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    except BadZipFile as exc:
        raise ValueError("Attendance review workbook is invalid; prepare a new review") from exc
    try:
        if workbook.sheetnames != ["Attendance"]:
            raise ValueError("Attendance review workbook changed; prepare a new review")
        rows = []
        for row in workbook["Attendance"].iter_rows(values_only=True):
            if not any(value is not None for value in row):
                continue
            if any(value is not None for value in row[4:]):
                raise ValueError("Attendance review workbook changed; prepare a new review")
            rows.append(tuple(row[:4]))
        expected = [("ID", "Name", "Class", "Status")]
        expected.extend((student["id"], student["name"], student["class"], student["status"])
                        for student in students)
        if rows != expected:
            raise ValueError("Attendance review workbook changed; correct the source CSV and prepare a new review")
    finally:
        workbook.close()


def prepare_attendance(config, config_dir, config_path):
    review = review_data(config, config_dir, config_path)
    root = resolve_path(config_dir, config["exam"]["output_dir"]) / "attendance"
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    versions = root / "versions"
    versions.mkdir(mode=0o700, exist_ok=True)
    version = secrets.token_hex(16)
    target = versions / version
    target.mkdir(mode=0o700)
    atomic_json(target / "review.json", review)
    _write_attendance_xlsx(target / "attendance_review.xlsx", review["students"])
    if review_data(config, config_dir, config_path) != review:
        raise ValueError("Attendance changed during preparation; inspect the partial review")
    atomic_json(root / "current.json", {"version": 1, "review_id": version})
    return target / "attendance_review.xlsx"


def current_review(config, config_dir, config_path):
    root = resolve_path(config_dir, config["exam"]["output_dir"]) / "attendance"
    pointer_path = root / "current.json"
    pointer_hash = file_hash(pointer_path)
    with pointer_path.open(encoding="utf-8") as file:
        pointer = json.load(file)
    if not isinstance(pointer, dict):
        raise ValueError("Invalid attendance review pointer")
    version = pointer.get("review_id")
    if pointer.get("version") != 1 or not isinstance(version, str) or not re.fullmatch(r"[0-9a-f]{32}", version):
        raise ValueError("Invalid attendance review pointer")
    target = root / "versions" / version
    review_path = target / "review.json"
    digest = file_hash(review_path)
    with review_path.open(encoding="utf-8") as file:
        review = json.load(file)
    workbook_path = target / "attendance_review.xlsx"
    workbook_hash = file_hash(workbook_path)
    _validate_attendance_workbook(workbook_path, review["students"])
    if (review != review_data(config, config_dir, config_path)
            or file_hash(review_path) != digest or file_hash(pointer_path) != pointer_hash
            or file_hash(workbook_path) != workbook_hash):
        raise ValueError("Attendance review or its inputs changed; prepare a new review")
    return target, review, digest, pointer_hash


def approve_attendance(config, config_dir, config_path):
    target, _, digest, pointer_hash = current_review(config, config_dir, config_path)
    approval = {"version": 1, "review_sha256": digest, "review_id": target.name}
    try:
        with (target / "approval.json").open("x", encoding="utf-8") as file:
            json.dump(approval, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
    except FileExistsError as exc:
        raise ValueError("This attendance review was already approved; preserve the approval") from exc
    _, _, after, pointer_after = current_review(config, config_dir, config_path)
    if after != digest or pointer_after != pointer_hash:
        raise ValueError("Attendance changed during approval; inspect the review")
    return target / "approval.json"


def approved_attendance(config, config_dir, config_path):
    if attendance_path(config, config_dir) is None:
        return None
    target, review, digest, pointer_hash = current_review(config, config_dir, config_path)
    approval_path = target / "approval.json"
    approval_hash = file_hash(approval_path)
    with approval_path.open(encoding="utf-8") as file:
        approval = json.load(file)
    if (approval != {"version": 1, "review_sha256": digest, "review_id": target.name}
            or file_hash(approval_path) != approval_hash
            or file_hash(target.parent.parent / "current.json") != pointer_hash):
        raise ValueError("Attendance approval is missing or stale")
    return review, approval_hash


def present_roster(config, config_dir, config_path):
    from .grade_review import roster_ids

    approved = approved_attendance(config, config_dir, config_path)
    if approved is None:
        return roster_ids(config, config_dir)
    return {row["id"]: {"name": row["name"], "class": row["class"]}
            for row in approved[0]["students"] if row["status"] == "present"}
