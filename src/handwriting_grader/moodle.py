"""Export combined per-student PDFs, email recipients, and Moodle bulk feedback packages."""

import csv
from io import BytesIO
import json
from pathlib import Path
import re
import textwrap
import zipfile

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter

from .distribution import address, approved_report
from .regions import atomic_json, file_hash


def _safe_filename(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", (text or "").strip())
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned.strip("_") or "student"


def _load_font(size: int, bold: bool = False):
    candidates = [
        "C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _clean_markdown(text: str) -> str:
    lines = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if s == "---":
            continue
        s = re.sub(r"^#{1,6}\s+", "", s)
        s = re.sub(r"^[-*•]\s+", "• ", s)
        s = s.replace("**", "").replace("`", "")
        lines.append(s)
    return "\n".join(lines).strip()


def _render_feedback_cover_pdf(exam_name: str, student_id: str, name: str, student_class: str,
                               score: float, max_marks: float, feedback_text: str) -> bytes:
    """Render an A4 feedback summary cover page as single-page PDF bytes."""
    w, h = 1240, 1754  # A4 at 150 DPI
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    font_title = _load_font(34, bold=True)
    font_sub = _load_font(22, bold=True)
    font_meta = _load_font(20, bold=False)
    font_body = _load_font(18, bold=False)

    # Header bar
    draw.rectangle([(0, 0), (w, 120)], fill=(15, 23, 42))
    draw.rectangle([(0, 120), (w, 128)], fill=(37, 99, 235))
    draw.text((60, 38), f"{exam_name} — Student Feedback & Score Summary", fill=(255, 255, 255), font=font_title)

    # Student info box
    draw.rounded_rectangle([(60, 160), (w - 60, 290)], radius=10, fill=(248, 250, 252), outline=(203, 213, 225), width=2)
    draw.text((90, 182), f"Student ID: {student_id}    |    Name: {name}    |    Class: {student_class or '-'}",
              fill=(30, 41, 59), font=font_meta)
    score_str = format(score, ".15g")
    max_str = format(max_marks, ".15g")
    draw.text((90, 228), f"Total Score: {score_str} / {max_str}", fill=(22, 163, 74), font=font_sub)

    # Feedback body
    draw.text((60, 330), "Personalized Feedback", fill=(30, 58, 138), font=font_sub)
    draw.line([(60, 368), (w - 60, 368)], fill=(203, 213, 225), width=2)

    y = 390
    line_h = 28
    cleaned = _clean_markdown(feedback_text)
    for para in cleaned.splitlines():
        if not para.strip():
            y += 14
            continue
        wrapped = textwrap.wrap(para, width=96) or [""]
        for line in wrapped:
            if y > h - 80:
                draw.text((60, y), "... (continued in teacher report)", fill=(100, 116, 139), font=font_body)
                break
            draw.text((60, y), line, fill=(30, 41, 59), font=font_body)
            y += line_h
        if y > h - 80:
            break

    buf = BytesIO()
    img.save(buf, format="PDF", resolution=150.0)
    return buf.getvalue()


def _merge_cover_and_script(cover_pdf_bytes: bytes, script_pdf_path: Path, dest_path: Path):
    writer = PdfWriter()
    for page in PdfReader(BytesIO(cover_pdf_bytes)).pages:
        writer.add_page(page)
    for page in PdfReader(str(script_pdf_path)).pages:
        writer.add_page(page)
    with dest_path.open("wb") as out:
        writer.write(out)


def _extract_participant_id(identifier: str) -> str:
    match = re.search(r"(\d+)", str(identifier or ""))
    return match.group(1) if match else re.sub(r"\W+", "", str(identifier or ""))


def export_moodle(config, config_dir, config_path, *, worksheet=None, participants=None, run_id=None):
    """Export per-student combined PDFs, optional email recipients CSV, and Moodle Assignment CSV/ZIP."""
    validated, rows, report_root = approved_report(config, config_dir, config_path, run_id=run_id)
    grading_root = validated[0]
    exam_name = config["exam"]["name"]
    max_marks = float(sum(q["marks"] for q in validated[3]))
    report_manifest_hash = file_hash(report_root / "manifest.json")

    feedback_by_idx = {}
    analysis_approval = grading_root.parent / "ai-analysis" / "approval.json"
    analysis_hash = None
    if analysis_approval.exists():
        from .advanced import read_approved_analysis
        _, _, students_fb, _, _, _ = read_approved_analysis(config, config_dir, config_path, run_id=run_id)
        feedback_by_idx = students_fb
        analysis_hash = file_hash(analysis_approval)

    filenames = [
        f"{_safe_filename(str(row['id']))}_{_safe_filename(str(row['name']))}.pdf"
        for row in rows
    ]
    if len({name.casefold() for name in filenames}) != len(filenames):
        raise ValueError("Student names and IDs produce duplicate Moodle filenames")

    student_ids = {str(row["id"]).strip() for row in rows}
    if len(student_ids) != len(rows) or "" in student_ids:
        raise ValueError("Moodle export requires distinct, nonempty student IDs")

    matched_emails = {}
    if participants:
        with Path(participants).open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            fields = set(reader.fieldnames or [])
            if not fields.intersection({"ID number", "ID"}) or not fields.intersection({"Email address", "EMAIL"}):
                raise ValueError("Participants CSV must contain an ID and email column")
            for p_row in reader:
                pid = str(p_row.get("ID number") or p_row.get("ID") or "").strip()
                email_val = str(p_row.get("Email address") or p_row.get("EMAIL") or "").strip()
                if pid in student_ids and email_val:
                    if pid in matched_emails:
                        raise ValueError(f"Participants CSV contains duplicate student ID: {pid}")
                    matched_emails[pid] = address(email_val)

    fieldnames, ws_rows = [], []
    if worksheet:
        with Path(worksheet).open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            fieldnames = list(reader.fieldnames or [])
            ws_rows = list(reader)
        if not {"ID number", "Identifier", "Grade"}.issubset(fieldnames):
            raise ValueError("Moodle grading worksheet CSV must contain 'Identifier', 'ID number' and 'Grade' columns")
        seen_students, seen_identifiers = set(), set()
        for ws_row in ws_rows:
            sid = str(ws_row.get("ID number") or "").strip()
            if sid not in student_ids:
                continue
            participant_id = _extract_participant_id(ws_row.get("Identifier", ""))
            if not participant_id:
                raise ValueError(f"Moodle worksheet has no participant identifier for student ID: {sid}")
            if sid in seen_students or participant_id in seen_identifiers:
                raise ValueError("Moodle worksheet contains duplicate student or participant identifiers")
            seen_students.add(sid)
            seen_identifiers.add(participant_id)

    moodle_dir = grading_root.parent / "moodle"
    try:
        moodle_dir.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("Moodle export already exists; preserve the existing output") from exc
    pdfs_dir = moodle_dir / "student_pdfs"
    pdfs_dir.mkdir(mode=0o700)

    student_records = {}
    for row, out_name in zip(rows, filenames):
        sid = str(row["id"]).strip()
        name = str(row.get("name") or "").strip()
        sclass = str(row.get("class") or "").strip()
        idx = row["script_index"]
        script_pdf = report_root / row["pdf"]
        dest_pdf = pdfs_dir / out_name
        fb_text = feedback_by_idx.get(idx, "")
        if fb_text:
            cover = _render_feedback_cover_pdf(exam_name, sid, name or sid, sclass, float(row["total"]), max_marks, fb_text)
            _merge_cover_and_script(cover, script_pdf, dest_pdf)
        else:
            dest_pdf.write_bytes(script_pdf.read_bytes())
        student_records[sid] = {
            "id": sid,
            "name": name,
            "class": sclass,
            "total": float(row["total"]),
            "feedback": fb_text,
            "pdf": dest_pdf,
            "pdf_name": out_name,
        }

    recipients_out = None
    if participants:
        recipients_out = moodle_dir / "recipients.csv"
        with recipients_out.open("w", encoding="utf-8", newline="") as out:
            writer = csv.writer(out)
            writer.writerow(["ID", "EMAIL"])
            for sid in sorted(matched_emails):
                writer.writerow([sid, matched_emails[sid]])

    worksheet_out = None
    zip_out = None
    matched_ws = 0
    if worksheet:
        zip_out = moodle_dir / "feedback_files.zip"
        with zipfile.ZipFile(zip_out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for ws_row in ws_rows:
                sid = str(ws_row.get("ID number") or "").strip()
                rec = student_records.get(sid)
                if not rec:
                    continue
                matched_ws += 1
                if "Grade" in ws_row:
                    score_val = format(rec["total"], ".15g")
                    ws_row["Grade"] = score_val
                if "Feedback comments" in ws_row and rec["feedback"]:
                    ws_row["Feedback comments"] = _clean_markdown(rec["feedback"])
                part_id = _extract_participant_id(ws_row.get("Identifier", ""))
                full_name = str(ws_row.get("Full name") or rec["name"] or sid).strip()
                folder = f"{_safe_filename(full_name)}_{part_id}_assignsubmission_file_"
                zf.write(rec["pdf"], arcname=f"{folder}/{rec['pdf_name']}")

        worksheet_out = moodle_dir / "grading_worksheet_filled.csv"
        with worksheet_out.open("w", encoding="utf-8-sig", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(ws_rows)

    approved_report(config, config_dir, config_path, run_id=run_id)
    if file_hash(report_root / "manifest.json") != report_manifest_hash:
        raise ValueError("Reports changed during Moodle export; inspect the partial output")
    if analysis_hash is not None:
        read_approved_analysis(config, config_dir, config_path, run_id=run_id)
        if file_hash(analysis_approval) != analysis_hash:
            raise ValueError("Analysis changed during Moodle export; inspect the partial output")
    atomic_json(moodle_dir / "manifest.json", {
        "version": 1,
        "students_exported": len(student_records),
        "has_ai_feedback": bool(feedback_by_idx),
        "recipients_csv": str(recipients_out) if recipients_out else None,
        "worksheet_matched": matched_ws,
        "worksheet_csv": str(worksheet_out) if worksheet_out else None,
        "feedback_zip": str(zip_out) if zip_out else None,
    })
    return moodle_dir
