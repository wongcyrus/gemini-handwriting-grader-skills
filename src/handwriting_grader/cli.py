"""Exam preflight and local region review."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml
from openpyxl import load_workbook
from pypdf import PdfReader


from .telemetry import TelemetryTracker, set_active_tracker, get_active_tracker


def _report_failed_marks(target):
    with (target / "results.json").open(encoding="utf-8") as file:
        results = json.load(file)
    failures = [row for row in results["marks"] if row.get("grading_failed")]
    if failures:
        questions = sorted({str(row["question"]) for row in failures})
        print(f"Manual review required: {len(failures)} failed answers across "
              f"{', '.join(questions)}; these are not zero marks.", file=sys.stderr)


def load_config(path):
    with path.open(encoding="utf-8") as file:
        config = yaml.safe_load(file)
    if not isinstance(config, dict):
        raise ValueError("Exam configuration must be a YAML mapping")
    return config


def check_exam(config, config_dir):
    errors = []
    summary = {}
    exam = config.get("exam")
    layout = config.get("layout")
    if not isinstance(exam, dict) or not isinstance(layout, dict):
        return ["exam and layout must be mappings"], summary

    def required_text(section, key):
        value = section.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{key} must be provided")
            return None
        return value.strip()

    name = required_text(exam, "name")
    sheet = required_text(exam, "roster_sheet")
    paths = {}
    for key in ("roster", "scanned_pdf", "marking_scheme", "output_dir"):
        value = required_text(exam, key)
        if value:
            path = Path(value).expanduser()
            paths[key] = (config_dir / path).resolve()
            if key != "output_dir" and not paths[key].is_file():
                errors.append(f"{key} is not a readable file")
    output = paths.get("output_dir")
    if output and output.exists() and not output.is_dir():
        errors.append("output_dir exists but is not a directory")
    if output and any(
        p == output or output in p.parents for key, p in paths.items() if key != "output_dir"
    ):
        errors.append("output_dir contains an input file; choose a separate output directory")

    pages = layout.get("pages_per_student")
    if isinstance(pages, bool) or not isinstance(pages, int) or pages <= 0:
        errors.append("pages_per_student must be a positive integer")
        pages = None
    if layout.get("same_layout_for_all") is not True:
        errors.append("same_layout_for_all must be confirmed true; mixed layouts are not supported")

    roster_count = None
    roster = paths.get("roster")
    if roster and roster.is_file() and sheet:
        try:
            workbook = load_workbook(roster, read_only=True, data_only=True)
            try:
                if sheet not in workbook.sheetnames:
                    errors.append("roster_sheet does not exist in the workbook")
                else:
                    rows = workbook[sheet].iter_rows(values_only=True)
                    header = next(rows, ())
                    names = [str(x).strip().upper() if x is not None else "" for x in header]
                    required = ("ID", "NAME", "CLASS")
                    if any(names.count(column) != 1 for column in required):
                        errors.append("roster must contain exactly one ID, NAME and CLASS column")
                    else:
                        columns = [names.index(column) for column in required]
                        ids = set()
                        roster_count = 0
                        for row in rows:
                            if not any(value is not None and str(value).strip() for value in row):
                                continue
                            values = [row[index] if index < len(row) else None for index in columns]
                            if any(value is None or not str(value).strip() for value in values):
                                errors.append("roster contains a row with an empty ID, NAME or CLASS")
                                continue
                            student_id = str(values[0]).strip()
                            if student_id in ids:
                                errors.append("roster contains duplicate IDs")
                            ids.add(student_id)
                            roster_count += 1
                        if not roster_count:
                            errors.append("roster contains no complete student rows")
            finally:
                workbook.close()
        except Exception as exc:
            errors.append(f"cannot read roster: {type(exc).__name__}")

    absent_count = 0
    if exam.get("attendance") is not None:
        try:
            from .attendance import attendance_rows

            _, statuses = attendance_rows(config, config_dir)
            roster_count = sum(status == "present" for status in statuses.values())
            absent_count = sum(status == "absent" for status in statuses.values())
        except (OSError, ValueError, KeyError) as exc:
            errors.append(f"attendance: {exc}")
            roster_count = None

    pdf_count = None
    pdf = paths.get("scanned_pdf")
    if pdf and pdf.is_file():
        try:
            pdf_count = len(PdfReader(pdf).pages)
            if not pdf_count:
                errors.append("scanned_pdf contains no pages")
        except Exception as exc:
            errors.append(f"cannot read scanned_pdf: {type(exc).__name__}")
    if roster_count and pdf_count is not None and pages and pdf_count != roster_count * pages:
        errors.append("PDF page count does not equal roster count times pages_per_student")
    summary.update(exam=name, students=roster_count, absent=absent_count,
                   pdf_pages=pdf_count, pages_per_student=pages)
    return errors, summary


def check_rubric_inputs(config, config_dir):
    exam = config.get("exam")
    if not isinstance(exam, dict):
        return ["exam must be a mapping"], {}
    errors = []
    paths = {}
    for key in ("name", "marking_scheme", "output_dir"):
        value = exam.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{key} must be provided")
        elif key != "name":
            paths[key] = (config_dir / Path(value.strip()).expanduser()).resolve()
    source = paths.get("marking_scheme")
    output = paths.get("output_dir")
    if source and not source.is_file():
        errors.append("marking_scheme is not a readable file")
    if output and output.exists() and not output.is_dir():
        errors.append("output_dir exists but is not a directory")
    if source and output and (source == output or output in source.parents):
        errors.append("output_dir contains the marking scheme; choose a separate output directory")
    return errors, paths


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare a handwriting grading run.")
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight", help="Check exam inputs without making changes")
    preflight.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    attendance_prepare = commands.add_parser("prepare-attendance", help="Prepare a private attendance review")
    attendance_prepare.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    attendance_approve = commands.add_parser("approve-attendance", help="Approve the reviewed attendance list")
    attendance_approve.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    attendance_approve.add_argument("--confirm-reviewed", action="store_true", help="Confirm attendance review")
    answer_sheets = commands.add_parser("answer-sheets", help="Generate personalized printable answer sheets")
    answer_sheets.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    prepare = commands.add_parser("prepare-regions", help="Render page layouts for manual review")
    prepare.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    review = commands.add_parser("review-regions", help="Start a local browser-based region editor")
    review.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    review.add_argument("--port", type=int, default=0, help="Local port (default: automatically selected)")
    regions_approve = commands.add_parser("approve-regions", help="Validate and explicitly approve page-layout regions")
    regions_approve.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    regions_approve.add_argument("--confirm-reviewed", action="store_true", help="Confirm human region review")
    check_scans_cmd = commands.add_parser("check-scans", help="Verify all scanned pages match expected orientation and page sequence (offline)")
    check_scans_cmd.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    suggest = commands.add_parser("suggest-regions", help="Request optional Vertex AI region proposals")
    suggest.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    suggest.add_argument("--model-config", required=True, type=Path, help="Path to existing AI config.yaml")
    suggest.add_argument("--project", required=True, help="Explicit Google Cloud project")
    suggest.add_argument("--location", required=True, help="Explicit Vertex AI location")
    suggest.add_argument("--confirm-cost", action="store_true", help="Confirm sending pages to Vertex AI")
    rubric_import = commands.add_parser("import-rubric", help="Import an existing structured XLSX rubric")
    rubric_import.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    rubric_extract = commands.add_parser("extract-rubric", help="Extract a DOCX rubric using Vertex AI")
    rubric_extract.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    rubric_extract.add_argument("--model-config", required=True, type=Path, help="Path to existing AI config.yaml")
    rubric_extract.add_argument("--project", required=True, help="Explicit Google Cloud project")
    rubric_extract.add_argument("--location", required=True, help="Explicit Vertex AI location")
    rubric_extract.add_argument("--confirm-cost", action="store_true", help="Confirm sending rubric to Vertex AI")
    rubric_verify = commands.add_parser("verify-rubric", help="Request optional search-grounded rubric review")
    rubric_verify.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    rubric_verify.add_argument("--model-config", required=True, type=Path, help="Path to existing AI config.yaml")
    rubric_verify.add_argument("--project", required=True, help="Explicit Google Cloud project")
    rubric_verify.add_argument("--location", required=True, help="Explicit Vertex AI location")
    rubric_verify.add_argument("--confirm-cost", action="store_true", help="Confirm Vertex and search requests")
    rubric_approve = commands.add_parser("approve-rubric", help="Validate and explicitly approve a reviewed rubric")
    rubric_approve.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    rubric_approve.add_argument("--confirm-reviewed", action="store_true", help="Confirm human review")
    grade = commands.add_parser("grade", help="OCR, grade and moderate approved regions with Vertex AI")
    grade.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    grade.add_argument("--model-config", required=True, type=Path, help="Path to existing AI config.yaml")
    grade.add_argument("--project", required=True, help="Explicit Google Cloud project")
    grade.add_argument("--location", required=True, help="Explicit Vertex AI location")
    grade.add_argument("--confirm-cost", action="store_true", help="Confirm sending student work to Vertex AI")
    grade.add_argument("--continue-on-image-policy-block", action="store_true",
                       help="Continue other questions after a typed Gemini safety block; never retry blocked images")
    grade.add_argument("--mode", choices=["batch", "item"], default="batch",
                       help="Grading mode: 'batch' (fast one-pass structured output per question) or 'item' (student-by-student with moderation)")
    grade.add_argument("--concurrency", type=int, default=3, help="Concurrent questions for batch grading (default: 3)")
    resume = commands.add_parser("resume-grading", help="Resume a supported interrupted grading checkpoint")
    resume.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    resume.add_argument("--confirm-cost", action="store_true", help="Confirm resumed paid calls")
    resume.add_argument("--continue-on-image-policy-block", action="store_true",
                        help="Continue other questions after a typed Gemini safety block")
    resume.add_argument("--mode", choices=["batch", "item"], default=None,
                        help="Override grading mode (default: from manifest or batch)")
    resume.add_argument("--concurrency", type=int, default=None, help="Concurrent questions for original batch runs")
    retry_cmd = commands.add_parser("retry-grading", help="Retry failed marks and unmoderated questions in a grading run")
    retry_cmd.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    retry_cmd.add_argument("--run", help="Target child run ID (default: original run)")
    retry_cmd.add_argument("--align-images", action="store_true", help="Align scanned page images to reference regions before cropping (offline, zero AI cost)")
    retry_cmd.add_argument("--re-ocr-id", action="store_true", help="Re-run ID OCR for all scripts using current approved box")
    retry_cmd.add_argument("--re-ocr-metadata", action="store_true", help="Re-run ID, CLASS and NAME OCR for all scripts using current approved boxes")
    retry_cmd.add_argument("--question", help="Re-grade and re-moderate a specific question using current approved box")
    retry_cmd.add_argument("--confirm-cost", action="store_true", help="Confirm sending student work to Vertex AI")
    regrade_rubric = commands.add_parser("prepare-regrade-rubric", help="Prepare a replacement rubric for review")
    regrade_rubric.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    regrade_rubric.add_argument("--proposal", required=True, help="New rubric proposal ID")
    regrade_rubric.add_argument("--parent", help="Parent run ID (default: original run)")
    regrade_rubric.add_argument("--question", required=True, help="Question to rerun")
    regrade_rubric.add_argument("--rubric", required=True, type=Path, help="Replacement XLSX rubric")
    regrade_approve = commands.add_parser("approve-regrade-rubric", help="Approve a reviewed replacement rubric")
    regrade_approve.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    regrade_approve.add_argument("--proposal", required=True, help="Rubric proposal ID")
    regrade_approve.add_argument("--confirm-reviewed", action="store_true", help="Confirm human rubric review")
    crop_prepare = commands.add_parser("prepare-regrade-crops", help="Prepare shared and per-script crops for review")
    crop_prepare.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    crop_prepare.add_argument("--proposal", required=True, help="New crop proposal ID")
    crop_prepare.add_argument("--parent", help="Parent run ID (default: original run)")
    crop_prepare.add_argument("--question", required=True, help="Question to rerun")
    crop_prepare.add_argument("--crops", required=True, type=Path, help="Private crop JSON outside output")
    crop_approve = commands.add_parser("approve-regrade-crops", help="Approve reviewed crop proposal")
    crop_approve.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    crop_approve.add_argument("--proposal", required=True, help="Crop proposal ID")
    crop_approve.add_argument("--confirm-reviewed", action="store_true", help="Confirm human crop review")
    regrade = commands.add_parser("regrade", help="Rerun one question in an isolated child run")
    regrade.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    regrade.add_argument("--run", required=True, help="New child run ID")
    regrade.add_argument("--parent", help="Parent run ID (default: original run)")
    regrade.add_argument("--question", required=True, help="Question to rerun for all present students")
    regrade.add_argument("--rubric-proposal", help="Approved replacement rubric proposal ID")
    regrade.add_argument("--crop-proposal", help="Approved shared and per-script crop proposal ID")
    regrade.add_argument("--reuse-parent-cache", action="store_true",
                         help="Reuse enabled parent cache; results may not be independently regenerated")
    regrade.add_argument("--model-config", required=True, type=Path, help="Path to existing AI config.yaml")
    regrade.add_argument("--project", required=True, help="Explicit Google Cloud project")
    regrade.add_argument("--location", required=True, help="Explicit Vertex AI location")
    regrade.add_argument("--confirm-cost", action="store_true", help="Confirm sending student work to Vertex AI")
    cluster_cmd = commands.add_parser("cluster-answers", help="Pre-compute semantic embedding clusters of student answers after grading and before review")
    cluster_cmd.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    cluster_cmd.add_argument("--threshold", type=float, default=0.82, help="Cosine similarity threshold (default: 0.82)")
    cluster_cmd.add_argument("--run", help="Optional run ID (default: current run)")
    cluster_cmd.add_argument("--model-config", type=Path, help="Separate embedding model configuration")
    cluster_cmd.add_argument("--preview-embedding", action="store_true", help="Preview paid embedding work without inference")
    cluster_cmd.add_argument("--revision", help="Exact review revision from the embedding preview")
    cluster_cmd.add_argument("--confirm-cost", action="store_true", help="Authorize paid embeddings; otherwise stay offline")
    grade_review = commands.add_parser("review-grading", help="Start a local browser-based grading editor")
    grade_review.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    grade_review.add_argument("--port", type=int, default=0, help="Local port (default: automatically selected)")
    grade_review.add_argument("--model-config", type=Path, help="Separate embedding model configuration")
    grade_review.add_argument("--confirm-cost", action="store_true", help="Authorize paid embeddings in the review editor")
    grade_check = commands.add_parser("check-grading-review", help="Diagnose review discrepancies without approval")
    grade_check.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    grade_approve = commands.add_parser("approve-grading", help="Validate and approve human-reviewed scores")
    grade_approve.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    grade_approve.add_argument("--confirm-reviewed", action="store_true", help="Confirm human review")
    report = commands.add_parser("report", help="Generate reports from approved grading")
    report.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    analytics = commands.add_parser("analytics", help="Calculate offline statistics from approved grading")
    analytics.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    ai_analysis = commands.add_parser("ai-analysis", help="Generate reviewable Vertex AI performance analyses")
    ai_analysis.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    ai_analysis.add_argument("--model-config", required=True, type=Path, help="Path to existing AI config.yaml")
    ai_analysis.add_argument("--project", required=True, help="Explicit Google Cloud project")
    ai_analysis.add_argument("--location", required=True, help="Explicit Vertex AI location")
    ai_analysis.add_argument("--confirm-cost", action="store_true", help="Confirm sending student answers")
    analysis_approve = commands.add_parser("approve-analysis", help="Approve human-reviewed AI feedback")
    analysis_approve.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    analysis_approve.add_argument("--confirm-reviewed", action="store_true", help="Confirm all feedback reviewed")
    advanced_report = commands.add_parser("advanced-report", help="Generate reviewed Word and PowerPoint reports")
    advanced_report.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    advanced_report.add_argument("--no-infographics", action="store_true",
                                 help="Keep reports offline (default); incompatible with --infographics")
    advanced_report.add_argument("--infographics", action="store_true", help="Preview optional paid Gemini infographics")
    advanced_report.add_argument("--confirm-cost", action="store_true", help="Authorize paid infographic generation")
    advanced_report.add_argument("--image-model-config", type=Path, help="Separate image model configuration")
    advanced_report.add_argument("--report-name", default="reports", help="New report directory; refuses overwrite")
    advanced_report.add_argument("--image-model", default=None,
                                 help="Override Gemini 3+ image model for infographics (e.g. gemini-3.1-flash-image)")
    cost_report = commands.add_parser("cost-report", help="Generate or display token, model and cost breakdown")
    cost_report.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    clean = commands.add_parser("clean", help="Safely archive (.zip) or purge output runs after human confirmation")
    clean.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    clean.add_argument("--no-archive", action="store_true", help="Skip creating backup .zip archive before cleaning")
    clean.add_argument("--full-archive", action="store_true",
                       help="Also include temporary grading folders and model-cache in the backup .zip")
    clean.add_argument("--purge-reports", action="store_true", help="Also delete final reports and analytics (default: preserved)")
    clean.add_argument("--confirm-delete", action="store_true", help="Explicit human confirmation to delete files")
    email_preview = commands.add_parser("preview-email", help="Create a private recipient and attachment preview")
    email_preview.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    email_preview.add_argument("--settings", required=True, type=Path, help="Path to email settings YAML")
    email_preview.add_argument("--recipients", required=True, type=Path, help="Path to ID,EMAIL CSV")
    email_approve = commands.add_parser("approve-email", help="Approve the exact email preview")
    email_approve.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    email_approve.add_argument("--confirm-reviewed", action="store_true", help="Confirm recipient and attachment review")
    email_send = commands.add_parser("send-email", help="Send individually approved emails using SMTP STARTTLS")
    email_send.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    email_send.add_argument("--confirm-send", action="store_true", help="Explicitly confirm dispatch")
    moodle_exp = commands.add_parser("moodle-export",
                                     help="Export per-student feedback PDFs, Moodle CSV/ZIP, and email recipients")
    moodle_exp.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    moodle_exp.add_argument("--worksheet", type=Path, default=None,
                            help="Optional Moodle Assignment grading worksheet CSV")
    moodle_exp.add_argument("--participants", type=Path, default=None,
                            help="Optional Moodle course participants CSV (to build recipients.csv)")
    failed_retry = commands.add_parser("retry-failed-batches", help="Retry failed answers in an incomplete batch checkpoint")
    failed_retry.add_argument("--exam", required=True, type=Path)
    failed_retry.add_argument("--exclude-question", action="append", default=[])
    failed_retry.add_argument("--checkpoint-sha256", help="Exact checkpoint hash from preview")
    failed_retry.add_argument("--confirm-cost", action="store_true")
    failed_export = commands.add_parser("export-failed-review", help="Export a separate offline failed-answer worksheet")
    failed_export.add_argument("--exam", required=True, type=Path)
    failed_export.add_argument("--output", required=True, type=Path)
    bisect = commands.add_parser("bisect-failed-batch", help="Explicitly bounded recovery of filtered batch answers")
    bisect.add_argument("--exam", required=True, type=Path)
    bisect.add_argument("--question", required=True)
    bisect.add_argument("--max-requests", required=True, type=int)
    bisect.add_argument("--revision", help="Exact review revision from preview")
    bisect.add_argument("--confirm-cost", action="store_true")
    discover_models = commands.add_parser("discover-models", help="List Vertex model metadata; inference probes are opt-in")
    discover_models.add_argument("--project", required=True, help="Explicit Google Cloud project")
    discover_models.add_argument("--location", default="global", help="Vertex AI location (default: global)")
    discover_models.add_argument("--write-config", type=Path, help="New model configuration YAML; refuses overwrite")
    discover_models.add_argument("--probe", action="store_true", help="Request potentially billed model capability probes")
    discover_models.add_argument("--confirm-cost", action="store_true", help="Authorize paid capability probes")
    for selected in (resume, grade_review, grade_check, grade_approve, report, analytics, ai_analysis, analysis_approve,
                     advanced_report, cost_report, clean, email_preview, email_approve, email_send, moodle_exp):
        selected.add_argument("--run", help="Child run ID (default: original run)")
    args = parser.parse_args(argv)
    if args.command == "discover-models":
        from .discovery import discover_and_report
        try:
            if args.write_config is not None and args.write_config.exists():
                raise ValueError("Configuration already exists; preserve it and select a new path")
            if args.probe and not args.confirm_cost:
                print("Paid capability probes require --confirm-cost; no inference sent.", file=sys.stderr)
                return 1
            if args.confirm_cost and not args.probe:
                raise ValueError("--confirm-cost requires --probe")
            print("Model metadata and configured rates do not verify quota, current retail prices or grading quality.")
            if args.probe:
                print("Capability probes send 'ping', can incur charges and try candidates across advertised regions.")
            discover_and_report(args.project, args.location, args.write_config, probe=args.probe)
            return 0
        except Exception as exc:
            print(f"Model discovery failed: {exc}", file=sys.stderr)
            return 1
    try:
        path = args.exam.expanduser().resolve()
        config = load_config(path)
        if args.command == "answer-sheets":
            errors, summary = [], {}
        elif args.command in {"import-rubric", "extract-rubric", "verify-rubric", "approve-rubric"}:
            errors, summary = check_rubric_inputs(config, path.parent)
        else:
            errors, summary = check_exam(config, path.parent)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"Preflight failed: {exc}", file=sys.stderr)
        return 1
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    if args.command == "prepare-attendance":
        from .attendance import prepare_attendance

        try:
            output = prepare_attendance(config, path.parent, path)
        except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            print(f"Attendance preparation failed: {exc}", file=sys.stderr)
            return 1
        print(f"Attendance review saved to {output}. Review every student and status before approval.")
        return 0
    if args.command == "approve-attendance":
        from .attendance import approve_attendance, current_review

        try:
            target, review, _, _ = current_review(config, path.parent, path)
            print(f"Review {target / 'attendance_review.xlsx'}: {summary['students']} present, {summary['absent']} absent.")
            if not args.confirm_reviewed:
                print("No approval recorded. Review each student and rerun with --confirm-reviewed.", file=sys.stderr)
                return 1
            approve_attendance(config, path.parent, path)
        except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            print(f"Attendance approval failed: {exc}", file=sys.stderr)
            return 1
        print("Attendance approved; changes invalidate the approval.")
        return 0
    if args.command == "answer-sheets":
        from .answer_sheets import generate_answer_sheets

        try:
            output = generate_answer_sheets(config, path.parent, path)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            print(f"Answer sheet generation failed: {exc}", file=sys.stderr)
            return 1
        print(f"Answer sheets saved to {output}. Review them before printing.")
        return 0
    if args.command == "import-rubric":
        from .rubric import import_rubric

        try:
            output = import_rubric(config, path.parent, path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            print(f"Rubric import failed: {exc}", file=sys.stderr)
            return 1
        print(f"Imported rubric to {output}. Review it before approval.")
        return 0
    if args.command == "extract-rubric":
        from .rubric import extract_rubric, rubric_root
        from .suggestions import load_model_settings

        if (not args.project.strip() or not args.location.strip()
                or any(char.isspace() for char in args.project)
                or any(char.isspace() for char in args.location)):
            print("Vertex project and location must be provided", file=sys.stderr)
            return 1
        try:
            model = load_model_settings(args.model_config, "marking_scheme")
            if summary["marking_scheme"].suffix.lower() != ".docx":
                raise ValueError("AI extraction requires a DOCX marking scheme")
            if rubric_root(config, path.parent).exists():
                raise ValueError("A rubric run already exists; choose a different output directory")
        except (OSError, ValueError, yaml.YAMLError) as exc:
            print(f"Cannot prepare rubric extraction: {exc}", file=sys.stderr)
            return 1
        print(f"Vertex target: project={args.project}, location={args.location}, model={model}. "
              "This sends the marking scheme and can incur charges.")
        if not args.confirm_cost:
            print("No request sent. Obtain user approval and rerun with --confirm-cost.", file=sys.stderr)
            return 1
        tracker = TelemetryTracker(log_dir=None, config_path=args.model_config)
        set_active_tracker(tracker)
        try:
            output = extract_rubric(
                config, path.parent, path, args.model_config, args.project.strip(), args.location.strip()
            )
            tracker.flush_jsonl(output.parent / "telemetry")
            tracker.write_cost_report(output.parent)
        except Exception as exc:
            print(f"Rubric extraction failed ({type(exc).__name__}); requests may have incurred charges.",
                  file=sys.stderr)
            return 1
        finally:
            set_active_tracker(None)
        print(f"Extracted rubric to {output}. Review it before approval.")
        return 0
    if args.command == "approve-rubric":
        from .rubric import approve_rubric, read_rubric, verify_rubric_source

        try:
            root, _ = verify_rubric_source(config, path.parent, path)
            questions = read_rubric(root / "rubric.xlsx")
            print(f"Rubric contains {len(questions)} questions worth {sum(q['marks'] for q in questions):g} marks.")
            if not args.confirm_reviewed:
                print("No approval recorded. Review the workbook and rerun with --confirm-reviewed.",
                      file=sys.stderr)
                return 1
            approve_rubric(config, path.parent, path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            print(f"Rubric approval failed: {exc}", file=sys.stderr)
            return 1
        print("Rubric approved; later changes invalidate the approval.")
        return 0
    if args.command == "verify-rubric":
        from .rubric import read_rubric, verify_rubric_source
        from .suggestions import load_model_settings
        from .verification import verify_rubric

        if (not args.project.strip() or not args.location.strip()
                or any(char.isspace() for char in args.project)
                or any(char.isspace() for char in args.location)):
            print("Vertex project and location must be provided", file=sys.stderr)
            return 1
        try:
            root, _ = verify_rubric_source(config, path.parent, path)
            questions = read_rubric(root / "rubric.xlsx")
            model = load_model_settings(args.model_config, "marking_scheme")
            if (root / "verification").exists():
                raise ValueError("Rubric verification already exists; do not overwrite it")
        except (OSError, ValueError, yaml.YAMLError) as exc:
            print(f"Cannot prepare verification: {exc}", file=sys.stderr)
            return 1
        print(f"Vertex target: project={args.project}, location={args.location}, model={model}. "
              f"Up to {len(questions)} search-grounded requests send rubric content and can incur charges.")
        if not args.confirm_cost:
            print("No request sent. Obtain user approval and rerun with --confirm-cost.", file=sys.stderr)
            return 1
        tracker = TelemetryTracker(log_dir=None, config_path=args.model_config)
        set_active_tracker(tracker)
        try:
            output = verify_rubric(config, path.parent, path, args.model_config, args.project, args.location)
            tracker.flush_jsonl(output / "telemetry")
            tracker.write_cost_report(output)
        except Exception as exc:
            print(f"Rubric verification failed ({type(exc).__name__}); requests may have incurred charges.",
                  file=sys.stderr)
            return 1
        finally:
            set_active_tracker(None)
        print(f"Verification saved to {output}. Review claims and sources; this is not rubric approval.")
        return 0
    if args.command == "prepare-regrade-rubric":
        from .regrading import prepare_rubric_proposal

        try:
            output = prepare_rubric_proposal(config, path.parent, path, args.proposal, args.parent,
                                              args.question, args.rubric)
        except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            print(f"Cannot prepare replacement rubric: {exc}", file=sys.stderr)
            return 1
        print(f"Replacement rubric saved to {output / 'rubric.xlsx'}. Review it before approval.")
        return 0
    if args.command == "approve-regrade-rubric":
        from .regrading import approve_rubric_proposal, validate_proposal

        try:
            root, manifest, _ = validate_proposal(config, path.parent, path, args.proposal,
                                                  require_approval=False)
            print(f"Review {root / 'rubric.xlsx'}; selected question: {manifest['question_key']}.")
            if not args.confirm_reviewed:
                print("No approval recorded. Review the rubric and rerun with --confirm-reviewed.", file=sys.stderr)
                return 1
            approve_rubric_proposal(config, path.parent, path, args.proposal)
        except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            print(f"Replacement rubric approval failed: {exc}", file=sys.stderr)
            return 1
        print("Replacement rubric approved; later changes invalidate the approval.")
        return 0
    if args.command == "prepare-regrade-crops":
        from .crop_proposals import prepare_crop_proposal

        try:
            output = prepare_crop_proposal(config, path.parent, path, args.proposal, args.parent,
                                           args.question, args.crops)
        except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            print(f"Cannot prepare crop proposal: {exc}", file=sys.stderr)
            return 1
        print(f"Crop proposal saved to {output}. Review every resolved crop against its script.")
        return 0
    if args.command == "approve-regrade-crops":
        from .crop_proposals import approve_crop_proposal, validate_crop_proposal

        try:
            root, manifest, crops = validate_crop_proposal(config, path.parent, path, args.proposal,
                                                            require_approval=False)
            print(f"Review {root / 'manifest.json'}; question {manifest['question_key']}, {len(crops)} scripts.")
            if not args.confirm_reviewed:
                print("No approval recorded. Review every crop and rerun with --confirm-reviewed.", file=sys.stderr)
                return 1
            approve_crop_proposal(config, path.parent, path, args.proposal)
        except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            print(f"Crop proposal approval failed: {exc}", file=sys.stderr)
            return 1
        print("Crop proposal approved; later changes invalidate the approval.")
        return 0
    if args.command == "regrade":
        from .regrading import inspect_regrade, regrade
        from .rubric import question_key

        try:
            info = inspect_regrade(config, path.parent, path, args.run, args.parent, args.question,
                                   args.model_config, args.project, args.location, args.rubric_proposal,
                                   args.crop_proposal, args.reuse_parent_cache)
        except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            print(f"Cannot prepare regrading: {exc}", file=sys.stderr)
            return 1
        count = len(info["parent"][5])
        selected = next(q for q in info["questions"] if question_key(q["question_number"]) == info["question_key"])
        print(f"Parent: {args.parent or 'original'}; child: {args.run}; question: {info['question_key']}; "
              f"rubric: {args.rubric_proposal or 'parent'}; crops: {args.crop_proposal or 'parent'}; students: {count}. "
              f"Vertex target: project={args.project}, location={args.location}, models={info['models']}. "
              f"Up to {count + 1} requests send student work or answers and may incur charges. "
              f"Cache: {info['cache_agents'] or 'disabled'}; location={info['cache_root'] or 'none'}.")
        if info["reuse_parent_cache"]:
            print("Parent cache reuse is enabled: matching model responses may be reused rather than regenerated.")
        print(f"Selected question: {selected['question_text']}\n"
              f"Maximum marks: {selected['marks']:g}\nMarking criteria: {selected['marking_scheme']}")
        if not args.confirm_cost:
            print("No request sent. Obtain user approval and rerun with --confirm-cost.", file=sys.stderr)
            return 1
        tracker = TelemetryTracker(log_dir=info["target"] / "grading" / "telemetry",
                                   config_path=args.model_config)
        set_active_tracker(tracker)
        try:
            output = regrade(config, path.parent, path, args.run, args.parent, args.question,
                             args.model_config, args.project, args.location, args.rubric_proposal,
                             args.crop_proposal, args.reuse_parent_cache)
            tracker.write_cost_report(output)
        except Exception as exc:
            print(f"Regrading failed ({type(exc).__name__}); requests may have incurred charges. "
                  "Preserve and inspect the child run.", file=sys.stderr)
            return 1
        finally:
            set_active_tracker(None)
        print(f"Child run saved to {output}. Fresh human review is required.")
        return 0
    if args.command == "grade":
        from .cache import cache_directory, enabled_agents
        from .grading import approved_inputs, grade_exam, grading_models
        from .regions import resolve_path
        from .reporting import question_passing_policy

        if (not args.project.strip() or not args.location.strip()
                or any(char.isspace() for char in args.project)
                or any(char.isspace() for char in args.location)):
            print("Vertex project and location must be provided", file=sys.stderr)
            return 1
        try:
            questions, _, _, _ = approved_inputs(config, path.parent, path)
            question_passing_policy(config, questions)
            models, _ = grading_models(args.model_config)
            cache_agents = [agent for agent in enabled_agents(args.model_config)
                            if agent in {"ocr", "grading", "moderation"}]
            cache_path = (cache_directory(args.model_config, resolve_path(path.parent, config["exam"]["output_dir"]) / "grading")
                          if cache_agents else None)
            if (resolve_path(path.parent, config["exam"]["output_dir"]) / "grading").exists():
                raise ValueError("A grading run already exists; choose a different output directory")
        except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            print(f"Cannot prepare grading: {exc}", file=sys.stderr)
            return 1
        mode = getattr(args, "mode", "batch")
        concurrency = getattr(args, "concurrency", 3)
        if args.continue_on_image_policy_block and mode != "batch":
            print("Continuing after a safety block requires an original batch run.", file=sys.stderr)
            return 1
        from math import ceil
        from .grading import MAX_IMAGES_PER_REQUEST
        metadata_requests = ceil(summary["students"] / min(35, MAX_IMAGES_PER_REQUEST // 3))
        if mode == "batch":
            chunks_per_q = ceil(summary["students"] / MAX_IMAGES_PER_REQUEST)
            request_count = metadata_requests + len(questions) * chunks_per_q
            print(f"Mode: BATCH (multimodal structured output, {concurrency} questions concurrent).")
        else:
            request_count = metadata_requests + 2 * summary["students"] * len(questions) + len(questions)
            print("Mode: ITEM (sequential student-by-student grading + moderation).")
        print(f"Vertex target: project={args.project}, location={args.location}, models={models}. "
              f"Up to {request_count} requests will send student work or answers and incur charges. "
              f"Cache: {cache_agents or 'disabled'}; location={cache_path if cache_agents else 'none'}.")
        if not args.confirm_cost:
            print("No request sent. Obtain user approval and rerun with --confirm-cost.", file=sys.stderr)
            return 1
        output_dir = resolve_path(path.parent, config["exam"]["output_dir"]) / "grading"
        tracker = TelemetryTracker(log_dir=output_dir / "telemetry", config_path=args.model_config)
        set_active_tracker(tracker)
        try:
            options = {"continue_on_image_policy_block": True} if args.continue_on_image_policy_block else {}
            output = grade_exam(
                config, path.parent, path, args.model_config, args.project.strip(),
                args.location.strip(), summary, mode=mode, batch_concurrency=concurrency, **options,
            )
            tracker.write_cost_report(output)
        except Exception as exc:
            tracker.write_cost_report(output_dir)
            print(f"Grading failed ({type(exc).__name__}): {str(exc)[:2000]}; requests may have incurred charges. "
                  "Inspect any partial run before choosing a new output directory.", file=sys.stderr)
            return 1
        finally:
            set_active_tracker(None)
        print(f"Grading results and review workbook saved to {output}. Human review is required.")
        return 0
    if args.command == "cluster-answers":
        from .regions import atomic_json
        from .score_editor import ScoreServer, load_state, writer_lock

        try:
            if not 0 <= args.threshold <= 1:
                raise ValueError("Clustering threshold must be between 0 and 1")
            if args.preview_embedding and args.confirm_cost:
                raise ValueError("Embedding preview cannot be combined with --confirm-cost")
            server = ScoreServer(config, path.parent, 0, path, run_id=args.run, allow_ai=args.confirm_cost,
                                 embedding_model_config=args.model_config)
            if args.preview_embedding:
                from .suggestions import load_model_settings

                with server.embedding_model_config.open(encoding="utf-8") as file:
                    model_config = yaml.safe_load(file)
                model_name = load_model_settings(server.embedding_model_config, "embedding")
                vertex = model_config["vertex_ai"]
                project = vertex.get("project_id")
                location = vertex.get("location", "global")
                if vertex.get("use_vertexai") is not True or not project or not location:
                    raise ValueError("Embedding preview requires a Vertex project and location")
                _, revision, _, _, _, marks = load_state(server)
                unique = {" ".join(str(item["answer"]).lower().split())
                          for item in marks.values() if item.get("answer") and str(item["answer"]).strip()}
                print(f"Embedding target: project={project}, location={location}, "
                      f"model={model_name}.")
                print(f"Up to {len(unique)} application requests, excluding SDK retries; "
                      "transcribed non-empty student answers are sent, not images.")
                print(f"Review and configuration revision: {server.embedding_preview_revision(revision)}")
                print("No request sent. Verify current pricing and obtain data and cost approval.")
                return 0
            if not args.confirm_cost:
                print("Word-overlap grouping is available in review-grading; no embeddings or output created.")
                return 0
            _, revision, _, _, _, _ = load_state(server)
            if args.revision != server.embedding_preview_revision(revision):
                raise ValueError("Embedding preview revision is missing or changed; no request sent")
            clusters_path = server.root / "clusters.json"
            if clusters_path.exists():
                raise ValueError("clusters.json already exists; preserve it before starting a new clustering run")
            server.expected_revision = revision
            _, model_name = server.get_embedding_client()
            total_q = len(server.questions)
            print(f"Pre-computing semantic clusters using {model_name} (threshold={args.threshold})...")
            print(f"Shared embedding cache: {server.shared_cache_path} ({len(server.embedding_cache)} cached entries)")
            cached_all = {}
            for q_idx in range(total_q):
                res = server.cluster_question(q_idx, threshold=args.threshold)
                q_num = server.questions[q_idx]["question_number"]
                c_count = len(res.get("clusters", []))
                cached_all[q_num] = res
                print(f"[{q_idx + 1}/{total_q}] Question {q_num}: {c_count} semantic clusters ready")
            with writer_lock(server):
                if load_state(server)[1] != revision:
                    raise ValueError("Grading or review changed; clustering results were not saved")
                if clusters_path.exists():
                    raise ValueError("clusters.json appeared during clustering; it was not overwritten")
                atomic_json(clusters_path, cached_all)
            print(f"Semantic clustering complete! Saved to {clusters_path} for review-grading.")
            return 0
        except Exception as exc:
            print(f"Semantic clustering failed: {exc}", file=sys.stderr)
            return 1
        finally:
            if "server" in locals():
                server.server_close()
    if args.command == "review-grading":
        from .score_editor import ScoreServer

        if not 0 <= args.port <= 65535:
            print("Port must be between 0 and 65535", file=sys.stderr)
            return 1
        try:
            server = ScoreServer(config, path.parent, args.port, path, run_id=args.run,
                                 allow_ai=args.confirm_cost, embedding_model_config=args.model_config)
        except (OSError, ValueError, KeyError) as exc:
            print(f"Cannot start grading editor: {exc}", file=sys.stderr)
            return 1
        print(f"Open {server.origin}/#{server.token}", flush=True)
        print("Press Ctrl+C to stop the local editor.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    if args.command == "check-grading-review":
        from .review_diagnostics import diagnose_review

        try:
            issues = diagnose_review(config, path.parent, path, run_id=args.run)
        except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            print(f"Review diagnostics failed: {exc}", file=sys.stderr)
            return 1
        if issues:
            print("Review discrepancies (private; script indices start at zero):")
            for issue in issues:
                print(f"- {issue}")
            print("No approval recorded. Correct the review and check again.")
            return 1
        print("No review discrepancies found. Human review and explicit approval are still required.")
        return 0
    if args.command == "approve-grading":
        from .grade_review import approve_grading, validate_review

        try:
            validated = validate_review(config, path.parent, path, run_id=args.run)
            print(f"Review covers {len(validated[5])} students and {len(validated[6])} marks.")
            if not args.confirm_reviewed:
                print("No approval recorded. Review the workbook and rerun with --confirm-reviewed.",
                      file=sys.stderr)
                return 1
            approve_grading(config, path.parent, path, run_id=args.run)
        except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            print(f"Grading approval failed: {exc}", file=sys.stderr)
            return 1
        print("Grading approved; later changes invalidate the approval.")
        return 0
    if args.command == "resume-grading":
        from .grade_review import verify_grading_run
        from .grading import resume_grading, MAX_IMAGES_PER_REQUEST

        try:
            root, manifest, results, questions = verify_grading_run(config, path.parent, path,
                                                                   require_completed=False, run_id=args.run)
            if results.get("checkpoint_version") != 1:
                raise ValueError("This partial run predates resumable checkpoints")
            if (root / "review.xlsx").exists() or (root / "approval.json").exists():
                raise ValueError("A review or approval already exists; do not overwrite it")
            if (root / "processing.lock").exists():
                raise ValueError("Another grading process or a stale lock exists")
            if args.run is not None and (args.mode is not None or args.concurrency is not None):
                raise ValueError("--mode and --concurrency are not supported for child runs")
            if args.concurrency is not None and args.concurrency < 1:
                raise ValueError("Concurrency must be a positive integer")
            if args.continue_on_image_policy_block and (args.run is not None
                    or (args.mode or manifest.get("grading_mode", "batch")) != "batch"):
                raise ValueError("Continuing after an image policy block requires an original batch run")
            fields = manifest.get("metadata_fields", ["ID", "CLASS"])
            if fields not in (["ID", "CLASS"], ["ID", "CLASS", "NAME"]):
                raise ValueError(f"Unsupported metadata fields in checkpoint: {fields!r}")
        except (OSError, ValueError, KeyError) as exc:
            print(f"Cannot resume grading: {exc}", file=sys.stderr)
            return 1
        print(f"Resume target: project={manifest['project']}, location={manifest['location']}, "
              f"models={manifest['models']}; run={args.run or 'original'}"
              f"{'; question=' + manifest['question_key'] if args.run else ''}. "
              "In-flight requests can be repeated and billed.")
        if not results.get("completed"):
            from math import ceil
            from .rubric import question_key

            keys = {question_key(q["question_number"]) for q in questions}
            finished = set(results.get("moderated_questions", []))
            pairs = {(row["script_index"], question_key(row["question"])) for row in results["marks"]}
            missing = summary["students"] - len(results["scripts"])
            metadata_requests = ceil(max(0, missing) / min(35, MAX_IMAGES_PER_REQUEST // len(fields)))
            mode = "batch" if args.run is not None else (args.mode or manifest.get("grading_mode", "batch"))
            pending = [sum((si, key) not in pairs for si in range(summary["students"]))
                       for key in keys - finished]
            if mode == "batch":
                requests = sum(ceil(count / MAX_IMAGES_PER_REQUEST) for count in pending)
                print(f"Remaining batch preview: up to {metadata_requests} metadata request(s) and "
                      f"{requests} question request(s), excluding SDK retries. Previously failed answers are not retried.")
            else:
                requests = 2 * sum(pending) + len(pending)
                print(f"Remaining item preview: up to {metadata_requests} metadata request(s) and "
                      f"up to {requests} item-mode request(s), including possible answer OCR and moderation; "
                      "excluding SDK retries. Previously failed answers are not retried.")
        else:
            print("The checkpoint is complete; finalizing the review requires no model requests.")
        if manifest.get("reuse_parent_cache"):
            print(f"Parent cache reuse remains enabled: {manifest['cache_root']}. "
                  "Matching responses may be reused rather than regenerated.")
        if args.continue_on_image_policy_block:
            print("Image-policy-blocked answers will be flagged for manual review; other questions will still incur requests.")
        if not args.confirm_cost:
            print("No request sent. Obtain user approval and rerun with --confirm-cost.", file=sys.stderr)
            return 1
        tracker = TelemetryTracker(log_dir=root / "telemetry", config_path=manifest["model_config_path"])
        tracker.load_existing_log(root / "telemetry" / "gemini_usage.jsonl")
        set_active_tracker(tracker)
        try:
            if args.run is None:
                options = {"continue_on_image_policy_block": True} if args.continue_on_image_policy_block else {}
                output = resume_grading(config, path.parent, path, summary, mode=args.mode,
                                        batch_concurrency=args.concurrency if args.concurrency is not None else 3, **options)
            else:
                from .regrading import resume_regrade
                output = resume_regrade(config, path.parent, path, args.run)
            tracker.write_cost_report(output)
        except Exception as exc:
            tracker.write_cost_report(root)
            detail = " ".join(str(exc).split())[:2000] or "No provider error detail was available"
            print(f"Resume failed ({type(exc).__name__}): {detail}; requests may have incurred charges.",
                  file=sys.stderr)
            return 1
        finally:
            set_active_tracker(None)
        print(f"Grading review saved to {output}. Human review is required.")
        if args.continue_on_image_policy_block:
            _report_failed_marks(output)
        return 0
    if args.command == "bisect-failed-batch":
        from .batch_recovery import inspect_batch_recovery

        try:
            if args.max_requests < 1:
                raise ValueError("Request limit must be positive")
            info = inspect_batch_recovery(config, path.parent, path, args.question)
        except (OSError, ValueError, KeyError) as exc:
            print(f"Cannot preview batch recovery: {exc}", file=sys.stderr)
            return 1
        if not info["rows"]:
            print("No unreviewed failed answers are eligible.")
            return 0
        manifest = info["manifest"]
        print(f"Recovery target: project={manifest['project']}, location={manifest['location']}, "
              f"model={manifest['models'].get('one_pass_grading', manifest['models']['grading'])}.")
        print(f"Question {info['question']['question_number']}: {len(info['rows'])} failed answer crops; "
              f"at most {min(args.max_requests, info['max_requests'])} requests, excluding SDK retries. "
              "Each request includes the question and rubric. Filtered single answers remain for manual review.")
        print(f"Review and grading revision: {info['revision']}")
        if not args.confirm_cost:
            print("No request sent. Obtain data and cost approval before retrying.", file=sys.stderr)
            return 1
        if args.revision != info["revision"]:
            print("Revision changed since preview; no request sent.", file=sys.stderr)
            return 1
        tracker = TelemetryTracker(log_dir=info["root"] / "telemetry", config_path=manifest["model_config_path"])
        set_active_tracker(tracker)
        try:
            from .batch_recovery import recover_question_batches
            result = recover_question_batches(config, path.parent, path, args.question, args.revision,
                                                 args.max_requests)
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            print(f"Batch recovery stopped: {exc}; requests may have incurred charges.", file=sys.stderr)
            return 1
        finally:
            set_active_tracker(None)
        print(f"Recovery: {result['recovered']} recovered, {result['blocked']} filtered single answers, "
              f"{result['pending']} pending; {result['requests']} calls. "
              f"History: {result['history']}. Human review remains required.")
        if result["error"]:
            print(f"Batch recovery stopped on error: {result['error']}", file=sys.stderr)
            return 1
        if result["pending"]:
            print("Request limit reached; remaining answers were not retried.", file=sys.stderr)
            return 1
        return 0

    if args.command == "retry-failed-batches":
        from .grading import inspect_failed_question_batches, retry_failed_question_batches
        from .rubric import question_key

        try:
            info = inspect_failed_question_batches(
                config, path.parent, path, summary, exclude_questions=args.exclude_question,
            )
        except (OSError, ValueError, KeyError) as exc:
            print(f"Cannot inspect batch retry: {exc}", file=sys.stderr)
            return 1
        if not info["failed"]:
            print("No failed answers to retry.")
            return 0
        manifest = info["manifest"]
        counts = [
            f"{q['question_number']}: {sum(question_key(row['question']) == question_key(q['question_number']) for row in info['failed'])}"
            for q in info["questions"] if question_key(q["question_number"]) in info["failed_keys"]
        ]
        print(
            f"Batch retry target: project={manifest['project']}, location={manifest['location']}, "
            f"model={manifest['models'].get('one_pass_grading', manifest['models']['grading'])}.\n"
            f"Failed answer crops by question: {', '.join(counts)}. "
            f"Up to {info['requests']} sequential requests, excluding SDK retries; "
            "each includes the question and rubric. Stops at the first error. "
            "Previously successful marks are preserved; the remaining questions require a separate resume.\n"
            f"Checkpoint SHA-256: {info['checkpoint_hash']}"
        )
        if args.exclude_question:
            print(f"Excluded questions remain unchanged: {', '.join(args.exclude_question)}.")
        if not args.confirm_cost:
            print("No request sent. Obtain user approval and rerun with --confirm-cost.", file=sys.stderr)
            return 1
        if args.checkpoint_sha256 != info["checkpoint_hash"]:
            print("Checkpoint hash does not match the approved preview; no request sent.", file=sys.stderr)
            return 1
        tracker = TelemetryTracker(log_dir=info["target"] / "telemetry", config_path=manifest["model_config_path"])
        set_active_tracker(tracker)
        try:
            retry_failed_question_batches(
                config, path.parent, path, summary, info["checkpoint_hash"],
                exclude_questions=args.exclude_question,
            )
        except Exception as exc:
            detail = " ".join(str(exc).split())[:2000] or "No provider error detail available"
            print(f"Batch retry failed ({type(exc).__name__}): {detail}; requests may have incurred charges.",
                  file=sys.stderr)
            return 1
        finally:
            set_active_tracker(None)
        print("Failed question batches retried; preview resume-grading to process the remaining questions.")
        return 0

    if args.command == "export-failed-review":
        from .grading import export_failed_review

        try:
            output = export_failed_review(config, path.parent, path, summary, args.output)
        except (OSError, ValueError, KeyError) as exc:
            print(f"Offline review export failed: {exc}; any partial export is not ready for use.",
                  file=sys.stderr)
            return 1
        print(f"Offline manual-review worksheet and crops saved to {output}. "
              "Marks are not imported into the grading run; no approval was recorded.")
        return 0

    if args.command == "retry-grading":
        from .grading import inspect_retry_grading, retry_grading

        fields = None
        if args.re_ocr_metadata:
            fields = ["ID", "CLASS", "NAME"]
        elif args.re_ocr_id:
            fields = ["ID"]

        try:
            info = inspect_retry_grading(
                config, path.parent, path, run_id=args.run,
                re_ocr_fields=fields, question=args.question,
                align_images=args.align_images,
            )
            failed_marks = info["failed_marks"]
            unmoderated_questions = info["unmoderated_questions"]
            manifest = info["manifest"]
            regions_updated = info["regions_updated"]
            target_question = info["target_question"]
            est_requests = info["estimated_requests"]
        except (OSError, ValueError, KeyError) as exc:
            print(f"Cannot inspect retry: {exc}", file=sys.stderr)
            return 1

        if (not failed_marks and not unmoderated_questions and not fields
                and not target_question and not regions_updated and not args.align_images):
            print("All marks, moderations, and metadata are complete; no items to retry.")
            return 0

        details = []
        if args.align_images:
            details.append(f"align {len(manifest.get('image_sha256', []))} page image(s) to reference layout (offline)")
        if regions_updated:
            details.append("approved regions updated")
        if fields:
            details.append(f"re-OCR {', '.join(fields)} ({info['num_scripts']} scripts)")
        if target_question:
            details.append(f"regrade Q{target_question['question_number']} ({info['num_scripts']} scripts + moderation)")
        if failed_marks:
            details.append(f"{len(failed_marks)} failed mark(s)")
        if unmoderated_questions:
            details.append(f"{len(unmoderated_questions)} question(s) to moderate")

        print(f"Retry target: project={manifest['project']}, location={manifest['location']}, "
              f"models={manifest['models']}; run={args.run or 'original'}.\n"
              f"Action plan: {'; '.join(details)}.\n"
              f"Estimated requests: up to {est_requests}. "
              + ("In-flight requests will be billed." if est_requests > 0 else "No AI requests needed."))

        if est_requests > 0 and not args.confirm_cost:
            print("No request sent. Obtain user approval and rerun with --confirm-cost.", file=sys.stderr)
            return 1

        target_dir = info["target"]
        tracker = TelemetryTracker(log_dir=target_dir / "telemetry",
                                   config_path=manifest["model_config_path"])
        set_active_tracker(tracker)
        try:
            output = retry_grading(
                config, path.parent, path, summary, run_id=args.run,
                re_ocr_fields=fields, question=args.question,
                align_images=args.align_images,
            )
            if est_requests > 0:
                tracker.write_cost_report(output)
        except Exception as exc:
            tracker.write_cost_report(target_dir)
            print(f"Retry failed ({type(exc).__name__}); requests may have incurred charges.", file=sys.stderr)
            return 1
        finally:
            set_active_tracker(None)

        print(f"Grading review updated at {output}. Human review is required.")
        return 0
    if args.command == "report":
        from .reporting import create_reports

        try:
            output = create_reports(config, path.parent, path, run_id=args.run)
        except (OSError, ValueError, KeyError) as exc:
            print(f"Report generation failed: {exc}", file=sys.stderr)
            return 1
        print(f"Reports saved to {output}. Review them before sharing.")
        return 0
    if args.command == "analytics":
        from .analytics import create_analytics

        try:
            output = create_analytics(config, path.parent, path, run_id=args.run)
        except (OSError, ValueError, KeyError) as exc:
            print(f"Analytics failed: {exc}", file=sys.stderr)
            return 1
        print(f"Offline statistics saved to {output}. Review them before sharing.")
        return 0
    if args.command == "ai-analysis":
        from .advanced import create_analysis
        from .grade_review import read_approved_grading
        from .suggestions import load_model_settings

        if (not args.project.strip() or not args.location.strip()
                or any(char.isspace() for char in args.project)
                or any(char.isspace() for char in args.location)):
            print("Vertex project and location must be provided", file=sys.stderr)
            return 1
        try:
            validated = read_approved_grading(config, path.parent, path, run_id=args.run)
            model = load_model_settings(args.model_config, "analytics")
            if (validated[0].parent / "ai-analysis").exists():
                raise ValueError("AI analysis already exists; preserve the existing run")
        except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            print(f"Cannot prepare AI analysis: {exc}", file=sys.stderr)
            return 1
        print(f"Vertex target: project={args.project}, location={args.location}, model={model}. "
              f"Up to {len(validated[5]) + len(validated[3]) + 1} requests send student answers and may incur charges.")
        if not args.confirm_cost:
            print("No request sent. Obtain user approval and rerun with --confirm-cost.", file=sys.stderr)
            return 1
        analysis_dir = validated[0].parent / "ai-analysis"
        tracker = TelemetryTracker(log_dir=analysis_dir / "telemetry", config_path=args.model_config)
        set_active_tracker(tracker)
        try:
            output = create_analysis(config, path.parent, path, args.model_config, args.project, args.location,
                                     run_id=args.run)
            tracker.write_cost_report(output)
        except Exception as exc:
            tracker.write_cost_report(analysis_dir)
            print(f"AI analysis failed ({type(exc).__name__}); requests may have incurred charges.", file=sys.stderr)
            return 1
        finally:
            set_active_tracker(None)
        print(f"AI analyses saved to {output}. Human review is required.")
        return 0
    if args.command == "approve-analysis":
        from .advanced import approve_analysis, validate_analysis

        try:
            _, validated, students, questions, _ = validate_analysis(config, path.parent, path, run_id=args.run)
            print(f"Review covers {len(students)} students and {len(questions)} questions.")
            if not args.confirm_reviewed:
                print("No approval recorded. Review all feedback and rerun with --confirm-reviewed.", file=sys.stderr)
                return 1
            approve_analysis(config, path.parent, path, run_id=args.run)
        except (OSError, ValueError, KeyError) as exc:
            print(f"Analysis approval failed: {exc}", file=sys.stderr)
            return 1
        print("AI analysis approved; later changes invalidate the approval.")
        return 0
    if args.command == "advanced-report":
        from .advanced import create_advanced_report, read_approved_analysis, image_generation_settings
        from .run_context import run_directory

        analysis_dir = run_directory(config, path.parent, args.run) / "ai-analysis"
        try:
            import re
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.report_name):
                raise ValueError("Report name must be a simple directory name of at most 64 characters")
            if args.no_infographics and args.infographics:
                raise ValueError("--no-infographics cannot be combined with --infographics")
            if not args.infographics and (args.image_model or args.image_model_config or args.confirm_cost):
                raise ValueError("Image overrides and cost consent require --infographics")
            root, validated, _, _, _, _ = read_approved_analysis(config, path.parent, path, run_id=args.run)
            if (root / args.report_name).exists():
                raise ValueError("Advanced reports already exist; choose a new --report-name")
            image_settings = image_generation_settings(root, args.image_model, args.image_model_config) if args.infographics else None
            model_config_path = image_settings["model_config_path"] if image_settings else None
            if image_settings:
                print(f"Infographic target: project={image_settings['project']}, "
                      f"location={image_settings['location']}, model={image_settings['model']}. "
                      f"Up to {len(validated[3]) + 1} single-attempt requests send reviewed analysis text; "
                      "cached images can reduce requests. Verify current image/token prices before consent.")
                if not args.confirm_cost:
                    print("No request sent. Obtain data and cost approval and rerun with --confirm-cost.",
                          file=sys.stderr)
                    return 1
            tracker = TelemetryTracker(log_dir=analysis_dir / "telemetry", config_path=model_config_path)
            set_active_tracker(tracker)
            output = create_advanced_report(config, path.parent, path, run_id=args.run,
                                            infographics=args.infographics, image_model=args.image_model,
                                            image_model_config=args.image_model_config, report_name=args.report_name)
            if tracker.traces:
                tracker.write_cost_report(analysis_dir)
        except (OSError, ValueError, KeyError) as exc:
            print(f"Advanced reporting failed: {exc}", file=sys.stderr)
            return 1
        finally:
            set_active_tracker(None)
        print(f"Advanced reports saved to {output}. Review them before sharing.")
        return 0
    if args.command == "cost-report":
        from .run_context import run_directory

        try:
            run_dir = run_directory(config, path.parent, args.run)
            grading_dir = run_dir / "grading"
            telemetry_files = [
                run_dir / "rubric" / "telemetry" / "gemini_usage.jsonl",
                run_dir / "rubric" / "verification" / "telemetry" / "gemini_usage.jsonl",
                run_dir / "regions" / "telemetry" / "gemini_usage.jsonl",
                grading_dir / "telemetry" / "gemini_usage.jsonl",
                run_dir / "ai-analysis" / "telemetry" / "gemini_usage.jsonl",
            ]

            tracker = TelemetryTracker(log_dir=grading_dir / "telemetry")
            for tf in telemetry_files:
                if tf.exists():
                    tracker.load_existing_log(tf)

            if not tracker.traces:
                print(f"No telemetry logs found in {run_dir}.")
                return 0

            summary = tracker.get_summary()
            cost_file = tracker.write_cost_report(grading_dir)
            print("============================================================")
            print("               GEMINI RECORDED USAGE & COST ESTIMATES       ")
            print("============================================================")
            print(f"Recorded API Calls:{summary['total_calls']}")
            print(f"Total Input Tokens: {summary['total_input_tokens']:,}")
            print(f"Total Output Tokens:{summary['total_output_tokens']:,}")
            print(f"Cached Tokens:      {summary['total_cached_tokens']:,}")
            print(f"Images Generated:   {summary['total_images']}")
            print(f"Estimated priced subtotal: ${summary['total_cost_usd']:.4f} USD")
            if summary["unpriced_calls"]:
                print(f"Unpriced calls: {summary['unpriced_calls']}; unknown usage is not free.")
            print("------------------------------------------------------------")
            print("By Phase Breakdown:")
            for phase, p_stat in summary.get("by_phase", {}).items():
                print(f"  - {phase:15}: {p_stat['calls']} calls, {p_stat['total_tokens']:,} tokens, ${p_stat['cost_usd']:.4f}")
            print("------------------------------------------------------------")
            print("By Model Breakdown:")
            for model, m_stat in summary.get("by_model", {}).items():
                print(f"  - {model:20}: {m_stat['calls']} calls, {m_stat['total_tokens']:,} tokens, ${m_stat['cost_usd']:.4f}")
            print("============================================================")
            print(f"Detailed Excel and JSON reports written to: {cost_file.parent}")
            print("Recorded usage excludes unlogged failures/retry attempts; provider billing determines actual spend.")
        except Exception as exc:
            print(f"Failed to generate cost report: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.command == "clean":
        from .cleanup import inspect_cleanup_targets, archive_and_clean

        try:
            info = inspect_cleanup_targets(config, path.parent, run_id=args.run,
                                           preserve_reports=not args.purge_reports)
            print(f"Target directory: {info['output_dir']} (run: {info['run_id']})")
            if not info["items"] and not info["deletion_targets"]:
                print("Output directory is empty or does not exist. Nothing to clean.")
                return 0
            print("Target contents:")
            for item in info["items"]:
                print(f"  - {item['name']} ({item['type']}, {item['size']:,} bytes, {item['files']} files)")
            print(f"Total size: {info['total_bytes']:,} bytes")
            print(f"Items to delete: {', '.join(info['deletion_targets']) or '(none)'}")
            if not args.confirm_delete:
                archive_note = "A cross-platform archive (.zip) will be created first." if not args.no_archive else "NO ARCHIVE will be created (--no-archive)."
                preserve_note = ("Images, reviews, approvals and final outputs will be preserved."
                                 if not args.purge_reports else
                                 "ALL selected outputs will be deleted, including an entire selected child run.")
                print(f"\nSafety gate: {archive_note} {preserve_note}")
                print("No files were deleted. Obtain explicit user confirmation and rerun with --confirm-delete.", file=sys.stderr)
                return 1
            result = archive_and_clean(
                config, path.parent, run_id=args.run,
                archive=not args.no_archive,
                preserve_reports=not args.purge_reports,
                full_archive=getattr(args, "full_archive", False),
            )
            if result.get("archive_path"):
                print(f"Backup archive created: {result['archive_path']}")
            print(f"Cleaned items: {', '.join(result['deleted_items']) or '(none)'}")
            if result.get("preserved_reports"):
                print("HIL reviews, AI outputs, and final deliverables (reports/, analytics/, ai-analysis/, moodle/) were preserved.")
        except Exception as exc:
            print(f"Cleanup failed: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.command == "preview-email":
        from .distribution import preview_email

        try:
            output = preview_email(config, path.parent, path, args.settings, args.recipients, run_id=args.run)
        except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            print(f"Email preview failed: {exc}", file=sys.stderr)
            return 1
        print(f"Email preview saved to {output}. No email was sent; inspect each message and attachment.")
        return 0
    if args.command == "approve-email":
        from .distribution import approve_email, validate_preview

        try:
            _, preview, _ = validate_preview(config, path.parent, path, run_id=args.run)
            print(f"Preview contains {len(preview['messages'])} individual messages.")
            if not args.confirm_reviewed:
                print("No approval recorded. Review every message, then rerun with --confirm-reviewed.",
                      file=sys.stderr)
                return 1
            approve_email(config, path.parent, path, run_id=args.run)
        except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            print(f"Email approval failed: {exc}", file=sys.stderr)
            return 1
        print("Email preview approved. Sending still requires separate confirmation.")
        return 0
    if args.command == "send-email":
        from .distribution import send_email, validate_preview

        try:
            _, preview, _ = validate_preview(config, path.parent, path, run_id=args.run)
            print(f"Dispatch would send {len(preview['messages'])} messages.")
            if not args.confirm_send:
                print("No email sent. Obtain explicit approval, then rerun with --confirm-send.", file=sys.stderr)
                return 1
            count = send_email(config, path.parent, path, run_id=args.run)
        except Exception as exc:
            print(f"Email sending failed ({type(exc).__name__}); inspect the delivery journal before retrying.",
                  file=sys.stderr)
            return 1
        print(f"SMTP accepted {count} messages; acceptance does not guarantee delivery.")
        return 0
    if args.command == "moodle-export":
        from .moodle import export_moodle

        try:
            output = export_moodle(config, path.parent, path, worksheet=args.worksheet,
                                   participants=args.participants, run_id=getattr(args, "run", None))
        except (OSError, ValueError, KeyError) as exc:
            print(f"Moodle export failed: {exc}", file=sys.stderr)
            return 1
        print(f"Moodle & student feedback package saved to {output}.")
        return 0
    if args.command == "prepare-regions":
        from .regions import prepare_regions

        try:
            target = prepare_regions(config, path.parent, summary["pages_per_student"])
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            print(f"Region preparation failed: {exc}", file=sys.stderr)
            return 1
        print(f"Prepared {summary['pages_per_student']} page layouts in {target}")
        print("Review the layouts before grading; no AI suggestions were generated.")
        return 0
    if args.command == "review-regions":
        from .editor import RegionServer

        if not 0 <= args.port <= 65535:
            print("Port must be between 0 and 65535", file=sys.stderr)
            return 1
        try:
            server = RegionServer(config, path.parent, args.port, config_path=path)
        except (OSError, ValueError, KeyError) as exc:
            print(f"Cannot start region editor: {exc}", file=sys.stderr)
            return 1
        print(f"Open {server.origin}/#{server.token}", flush=True)
        print("Press Ctrl+C to stop the local editor.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    if args.command == "approve-regions":
        from .regions import approve_regions, verify_run

        try:
            root, manifest = verify_run(config, path.parent, path)
            print(f"Regions cover {len(manifest['pages'])} pages.")
            if not args.confirm_reviewed:
                print("No approval recorded. Review the regions and rerun with --confirm-reviewed.",
                      file=sys.stderr)
                return 1
            approve_regions(config, path.parent, path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            print(f"Region approval failed: {exc}", file=sys.stderr)
            return 1
        print("Regions approved; later changes invalidate the approval.")
        return 0
    if args.command == "check-scans":
        import tempfile
        from .alignment import check_scan_orientation_and_sequence
        from .pdf_renderer import render_pdf_to_images
        from .regions import resolve_path

        try:
            pdf = resolve_path(path.parent, config["exam"]["scanned_pdf"])
            per_student = summary["pages_per_student"]
            with tempfile.TemporaryDirectory(prefix=".check-scans-") as temp:
                pages = render_pdf_to_images(
                    pdf_path=pdf, output_dir=Path(temp), prefix="page",
                    dpi=200, first_page=1, last_page=summary["pdf_pages"],
                )
                issues = check_scan_orientation_and_sequence(pages, None, per_student)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            print(f"Scan check failed: {exc}", file=sys.stderr)
            return 1
        if issues:
            print(f"Scan orientation/sequence issues found ({len(issues)}):", file=sys.stderr)
            for issue in issues:
                print(f"- {issue}", file=sys.stderr)
            print("Fix the flagged pages manually in the source PDF or physical scan and re-run check-scans.",
                  file=sys.stderr)
            return 1
        print(f"Scan check passed: all {summary['pdf_pages']} pages ({summary['students']} scripts) "
              "match expected orientation and sequence.")
        return 0
    if args.command == "suggest-regions":
        from .regions import resolve_path, verify_run
        from .suggestions import generate_suggestions, load_model_settings

        if (
            not args.project.strip() or not args.location.strip()
            or any(char.isspace() for char in args.project)
            or any(char.isspace() for char in args.location)
        ):
            print("Vertex project and location must be provided", file=sys.stderr)
            return 1
        try:
            model = load_model_settings(args.model_config)
            regions_root, manifest = verify_run(config, path.parent, path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            print(f"Cannot prepare Vertex suggestions: {exc}", file=sys.stderr)
            return 1
        print(
            f"Vertex target: project={args.project}, location={args.location}, model={model}. "
            f"This sends {len(manifest['pages'])} page images and can incur charges; retries may cost more."
        )
        if not args.confirm_cost:
            print("No request sent. Obtain user approval and rerun with --confirm-cost.", file=sys.stderr)
            return 1
        tracker = TelemetryTracker(log_dir=regions_root / "telemetry", config_path=args.model_config)
        set_active_tracker(tracker)
        try:
            output = generate_suggestions(
                config, path.parent, path, args.model_config, args.project.strip(), args.location.strip()
            )
            tracker.write_cost_report(regions_root)
        except Exception as exc:
            print(
                f"Vertex suggestion generation failed ({type(exc).__name__}); "
                "requests already sent may have incurred charges.",
                file=sys.stderr,
            )
            return 1
        finally:
            set_active_tracker(None)
        print(f"Saved suggestions to {output}; human review is still required.")
        return 0
    print(
        f"Preflight passed: {summary['students']} students, {summary['absent']} absent, "
        f"{summary['pdf_pages']} pages, {summary['pages_per_student']} pages per script."
    )
    print("Human confirmation of scan order and layout is still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
