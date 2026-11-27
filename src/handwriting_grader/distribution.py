"""Prepare, approve and send individually addressed score emails."""

import csv
import hashlib
import json
import os
import smtplib
import ssl
import string
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

import yaml

from .grade_review import read_approved_grading
from .regions import atomic_json, file_hash
from .reporting import report_rows


def address(value):
    if not isinstance(value, str) or not value or value != value.strip() or any(c.isspace() for c in value):
        raise ValueError("An email address is invalid")
    try:
        parsed = Address(addr_spec=value)
    except (ValueError, IndexError):
        raise ValueError("An email address is invalid") from None
    if not parsed.username or not parsed.domain or str(parsed) != value:
        raise ValueError("An email address is invalid")
    return value


def settings_file(path):
    with Path(path).open(encoding="utf-8") as file:
        settings = yaml.safe_load(file)
    if not isinstance(settings, dict):
        raise ValueError("Email settings must be a mapping")
    if set(settings) != {"sender", "subject", "body", "smtp_host", "smtp_port", "smtp_username"}:
        raise ValueError("Email settings require sender, subject, body, smtp_host, smtp_port and smtp_username only")
    address(settings["sender"])
    for key in ("subject", "body", "smtp_host", "smtp_username"):
        if not isinstance(settings[key], str) or not settings[key].strip():
            raise ValueError(f"{key} must be provided")
    if any(c in settings["subject"] for c in "\r\n") or any(c.isspace() for c in settings["smtp_host"]):
        raise ValueError("Email subject or SMTP host is invalid")
    if type(settings["smtp_port"]) is not int or not 1 <= settings["smtp_port"] <= 65535:
        raise ValueError("SMTP port must be an integer from 1 to 65535")
    for key in ("subject", "body"):
        try:
            for _, field, spec, conversion in string.Formatter().parse(settings[key]):
                permitted = {"name", "marks", "exam", "feedback"} if key == "body" else {"name", "marks", "exam"}
                if field is not None and (field not in permitted or spec or conversion):
                    raise ValueError("Email templates allow {name}, {marks}, {exam}, and {feedback} in the body")
        except ValueError as exc:
            raise ValueError(f"Invalid {key} template: {exc}") from exc
    return settings


def recipients_file(path, roster):
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fields = list(reader.fieldnames or [])
        is_std = fields == ["ID", "EMAIL"]
        is_moodle = "ID number" in fields and "Email address" in fields
        if not (is_std or is_moodle):
            raise ValueError("Recipients CSV must have headers ID,EMAIL or Moodle columns (ID number, Email address)")
        recipients = {}
        addresses = set()
        for row in reader:
            raw_id = row.get("ID") if is_std else row.get("ID number")
            raw_email = row.get("EMAIL") if is_std else row.get("Email address")
            student_id = raw_id.strip() if isinstance(raw_id, str) else ""
            if is_moodle and student_id not in roster:
                continue
            email = address(raw_email)
            if student_id not in roster or student_id in recipients or email.casefold() in addresses:
                raise ValueError("Recipients must be unique and match the present roster")
            recipients[student_id] = email
            addresses.add(email.casefold())
    if set(recipients) != set(roster):
        raise ValueError("Recipients must match the present roster")
    return recipients


def approved_feedback(settings, config, config_dir, config_path, *, run_id=None):
    fields = {field for _, field, _, _ in string.Formatter().parse(settings["body"]) if field is not None}
    if "feedback" not in fields:
        return {}, None
    from .advanced import read_approved_analysis

    root, _, students, _, _, _ = read_approved_analysis(config, config_dir, config_path, run_id=run_id)
    return students, file_hash(root / "approval.json")


def approved_report(config, config_dir, config_path, *, run_id=None):
    validated = read_approved_grading(config, config_dir, config_path, run_id=run_id)
    grading_root = validated[0]
    root = grading_root.parent / "reports"
    with (root / "manifest.json").open(encoding="utf-8") as file:
        manifest = json.load(file)
    rows, _ = report_rows(validated)
    if manifest.get("version") == 1:
        for row in rows:
            row["pdf"] = f"pdfs/script-{row['script_index'] + 1:05d}.pdf"
    expected = {row["pdf"] for row in rows}
    hashes = manifest.get("script_pdf_sha256")
    if (manifest.get("version") not in (1, 2) or manifest.get("completed") is not True
            or manifest.get("review_sha256") != file_hash(grading_root / "review.xlsx")
            or manifest.get("grading_approval_sha256") != file_hash(grading_root / "approval.json")
            or not isinstance(hashes, dict) or set(hashes) != expected):
        raise ValueError("Reports are incomplete, stale or lack PDF integrity records; regenerate them")
    for relative in expected:
        if file_hash(root / relative) != hashes[relative]:
            raise ValueError("A scored script PDF changed after report generation")
    return validated, rows, root


def preview_email(config, config_dir, config_path, settings_path, recipients_path, *, run_id=None):
    validated, rows, reports = approved_report(config, config_dir, config_path, run_id=run_id)
    settings_path = Path(settings_path).expanduser().resolve()
    recipients_path = Path(recipients_path).expanduser().resolve()
    settings_hash, recipients_hash = file_hash(settings_path), file_hash(recipients_path)
    settings = settings_file(settings_path)
    recipients = recipients_file(recipients_path, validated[4])
    feedback, feedback_hash = approved_feedback(settings, config, config_dir, config_path, run_id=run_id)
    target = validated[0].parent / "email"
    if any(target == p or target in p.parents for p in (settings_path, recipients_path)):
        raise ValueError("Email input files must be outside the output directory")
    try:
        target.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("An email preview already exists; preserve and inspect it") from exc
    messages = []
    for row in rows:
        values = {"name": row["name"], "marks": f"{row['total']:g}", "exam": config["exam"]["name"],
                  "feedback": feedback.get(row["script_index"], "")}
        subject, body = settings["subject"].format_map(values), settings["body"].format_map(values)
        if any(c in subject for c in "\r\n"):
            raise ValueError("Rendered email subject contains a line break")
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = settings["sender"]
        message["To"] = recipients[row["id"]]
        message["Subject"] = subject
        message.set_content(body)
        pdf = reports / row["pdf"]
        message.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=pdf.name)
        name = f"message-{row['script_index'] + 1:05d}.eml"
        with (target / name).open("xb") as file:
            file.write(message.as_bytes())
        messages.append({"id": row["id"], "recipient": recipients[row["id"]], "subject": subject,
                         "body": body, "attachment": str(pdf), "attachment_sha256": file_hash(pdf),
                         "message": name, "message_sha256": file_hash(target / name)})
    approved_report(config, config_dir, config_path, run_id=run_id)
    if (file_hash(settings_path) != settings_hash or file_hash(recipients_path) != recipients_hash
            or approved_feedback(settings, config, config_dir, config_path, run_id=run_id)[1] != feedback_hash):
        raise ValueError("Email settings or recipients changed during preview")
    atomic_json(target / "preview.json", {
        "version": 1, "messages": messages, "settings_path": str(settings_path), "settings_sha256": settings_hash,
        "recipients_path": str(recipients_path), "recipients_sha256": recipients_hash,
        "reports_manifest_sha256": file_hash(reports / "manifest.json"),
        "review_sha256": file_hash(validated[0] / "review.xlsx"),
        "analysis_approval_sha256": feedback_hash,
    })
    return target


def validate_preview(config, config_dir, config_path, *, run_id=None):
    validated, rows, reports = approved_report(config, config_dir, config_path, run_id=run_id)
    target = validated[0].parent / "email"
    with (target / "preview.json").open(encoding="utf-8") as file:
        preview = json.load(file)
    settings_path, recipients_path = Path(preview["settings_path"]), Path(preview["recipients_path"])
    if (preview.get("version") != 1 or preview["settings_sha256"] != file_hash(settings_path)
            or preview["recipients_sha256"] != file_hash(recipients_path)
            or preview["reports_manifest_sha256"] != file_hash(reports / "manifest.json")
            or preview["review_sha256"] != file_hash(validated[0] / "review.xlsx")):
        raise ValueError("Email preview inputs changed")
    settings = settings_file(settings_path)
    recipients = recipients_file(recipients_path, validated[4])
    feedback, feedback_hash = approved_feedback(settings, config, config_dir, config_path, run_id=run_id)
    if preview.get("analysis_approval_sha256") != feedback_hash:
        raise ValueError("Reviewed feedback changed since email preview")
    messages = preview.get("messages")
    if not isinstance(messages, list) or len(messages) != len(rows):
        raise ValueError("Email preview does not cover the roster")
    for row, item in zip(rows, messages):
        expected_name = f"message-{row['script_index'] + 1:05d}.eml"
        pdf = reports / row["pdf"]
        values = {"name": row["name"], "marks": f"{row['total']:g}", "exam": config["exam"]["name"],
                  "feedback": feedback.get(row["script_index"], "")}
        subject, body = settings["subject"].format_map(values), settings["body"].format_map(values)
        if (item.get("id") != row["id"] or item.get("recipient") != recipients[row["id"]]
                or item.get("message") != expected_name or item.get("attachment") != str(pdf)
                or item.get("subject") != subject or item.get("body") != body
                or item.get("message_sha256") != file_hash(target / expected_name)
                or item.get("attachment_sha256") != file_hash(pdf)):
            raise ValueError("Email preview or attachments changed")
        message = BytesParser(policy=policy.default).parsebytes((target / expected_name).read_bytes())
        parts = list(message.iter_parts()) if message.is_multipart() else []
        expected_body = body if body.endswith("\n") else body + "\n"
        if (set(message.keys()) != {"From", "To", "Subject", "MIME-Version", "Content-Type"}
                or len(message.get_all("From", [])) != 1 or len(message.get_all("To", [])) != 1
                or len(message.get_all("Subject", [])) != 1
                or str(message["From"]) != settings["sender"] or str(message["To"]) != recipients[row["id"]]
                or str(message["Subject"]) != subject or len(parts) != 2
                or parts[0].get_content_type() != "text/plain"
                or parts[0].get_content().replace("\r\n", "\n") != expected_body.replace("\r\n", "\n")
                or parts[1].get_content_type() != "application/pdf"
                or parts[1].get_filename() != pdf.name or parts[1].get_payload(decode=True) != pdf.read_bytes()):
            raise ValueError("Email message does not match the reviewed preview")
    return target, preview, settings


def approve_email(config, config_dir, config_path, *, run_id=None):
    target, _, _ = validate_preview(config, config_dir, config_path, run_id=run_id)
    atomic_json(target / "approval.json", {"version": 1, "preview_sha256": file_hash(target / "preview.json")})


def send_email(config, config_dir, config_path, *, run_id=None):
    target, preview, settings = validate_preview(config, config_dir, config_path, run_id=run_id)
    with (target / "approval.json").open(encoding="utf-8") as file:
        approval = json.load(file)
    if approval.get("version") != 1 or approval.get("preview_sha256") != file_hash(target / "preview.json"):
        raise ValueError("Email preview has not been approved or has changed")
    approval_hash = file_hash(target / "approval.json")
    password = os.environ.get("HANDWRITING_SMTP_PASSWORD")
    if not password:
        raise ValueError("Set HANDWRITING_SMTP_PASSWORD in the environment")
    journal_path = target / "delivery.json"
    journal = {"version": 1, "status": "started", "sent": [], "in_flight": None}
    try:
        with journal_path.open("x", encoding="utf-8") as file:
            json.dump(journal, file)
            file.flush()
            os.fsync(file.fileno())
    except FileExistsError as exc:
        raise ValueError("A delivery attempt already exists; reconcile it manually before any retry") from exc
    with smtplib.SMTP(settings["smtp_host"], settings["smtp_port"], timeout=30) as server:
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        server.login(settings["smtp_username"], password)
        for item in preview["messages"]:
            validate_preview(config, config_dir, config_path, run_id=run_id)
            if (file_hash(target / "preview.json") != approval["preview_sha256"]
                    or file_hash(target / "approval.json") != approval_hash):
                raise ValueError("Email approval changed during sending")
            journal["in_flight"] = item["message"]
            atomic_json(journal_path, journal)
            message_bytes = (target / item["message"]).read_bytes()
            if hashlib.sha256(message_bytes).hexdigest() != item["message_sha256"]:
                raise ValueError("Email message changed during sending")
            result = server.sendmail(settings["sender"], [item["recipient"]], message_bytes)
            if result:
                raise ValueError("SMTP server rejected a recipient; inspect the delivery journal")
            journal["sent"].append(item["message"])
            journal["in_flight"] = None
            atomic_json(journal_path, journal)
    journal["status"] = "completed"
    atomic_json(journal_path, journal)
    return len(journal["sent"])
