"""Exam preflight and local region review."""

import argparse
import subprocess
import sys
from pathlib import Path

import yaml
from openpyxl import load_workbook
from pypdf import PdfReader


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
    summary.update(exam=name, students=roster_count, pdf_pages=pdf_count, pages_per_student=pages)
    return errors, summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare a handwriting grading run.")
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight", help="Check exam inputs without making changes")
    preflight.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    prepare = commands.add_parser("prepare-regions", help="Render page layouts for manual review")
    prepare.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    review = commands.add_parser("review-regions", help="Start a local browser-based region editor")
    review.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    review.add_argument("--port", type=int, default=0, help="Local port (default: automatically selected)")
    suggest = commands.add_parser("suggest-regions", help="Request optional Vertex AI region proposals")
    suggest.add_argument("--exam", required=True, type=Path, help="Path to exam YAML")
    suggest.add_argument("--model-config", required=True, type=Path, help="Path to existing AI config.yaml")
    suggest.add_argument("--project", required=True, help="Explicit Google Cloud project")
    suggest.add_argument("--location", required=True, help="Explicit Vertex AI location")
    suggest.add_argument("--confirm-cost", action="store_true", help="Confirm sending pages to Vertex AI")
    args = parser.parse_args(argv)
    try:
        path = args.exam.expanduser().resolve()
        config = load_config(path)
        errors, summary = check_exam(config, path.parent)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"Preflight failed: {exc}", file=sys.stderr)
        return 1
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
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
    if args.command == "suggest-regions":
        from .regions import verify_run
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
            _, manifest = verify_run(config, path.parent, path)
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
        try:
            output = generate_suggestions(
                config, path.parent, path, args.model_config, args.project.strip(), args.location.strip()
            )
        except Exception as exc:
            print(
                f"Vertex suggestion generation failed ({type(exc).__name__}); "
                "requests already sent may have incurred charges.",
                file=sys.stderr,
            )
            return 1
        print(f"Saved suggestions to {output}; human review is still required.")
        return 0
    print(
        f"Preflight passed: {summary['students']} students, "
        f"{summary['pdf_pages']} pages, {summary['pages_per_student']} pages per script."
    )
    print("Human confirmation of scan order and layout is still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
