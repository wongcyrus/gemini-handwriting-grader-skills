"""Local browser review of identities, OCR and scores."""

import hmac
import json
import math
import os
import re
import secrets
import shutil
import tempfile
import threading
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, localcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.exceptions import IllegalCharacterError
from PIL import Image

from .attendance import present_roster
from .grade_review import approve_grading, confirmed, read_approved_grading, reviewed_rows, verify_grading_run
from .grading import approved_inputs, crop_image
from .regions import file_hash
from .rubric import question_key
from .run_context import run_directory


def load_state(server):
    proposed = run_directory(server.config, server.config_dir, server.run_id) / "grading"
    result_hash = file_hash(proposed / "results.json")
    manifest_hash = file_hash(proposed / "manifest.json")
    root, manifest, results, questions = verify_grading_run(server.config, server.config_dir, server.config_path,
                                                             run_id=server.run_id)
    previous = {}
    if server.run_id is not None:
        parent = read_approved_grading(server.config, server.config_dir, server.config_path,
                                       run_id=manifest.get("parent_run"))
        for item in parent[2]["marks"]:
            pair = (item["script_index"], question_key(item["question"]))
            previous[pair] = {"answer": item["answer"], "reasoning": item["reasoning"],
                              "mark": parent[6][pair], "rerun": pair[1] == manifest["question_key"]}
    roster = present_roster(server.config, server.config_dir, server.config_path)
    expected = set(range(len(roster)))
    raw_scripts = {item["script_index"]: item for item in results["scripts"]}
    raw_marks = {(item["script_index"], question_key(item["question"])): item for item in results["marks"]}
    expected_marks = {(index, question_key(q["question_number"])) for index in expected for q in questions}
    if (len(raw_scripts) != len(results["scripts"]) or set(raw_scripts) != expected
            or len(raw_marks) != len(results["marks"]) or set(raw_marks) != expected_marks):
        raise ValueError("Grading results do not cover the roster and rubric")
    path = root / "review.xlsx"
    review_hash = file_hash(path)
    book = load_workbook(path, data_only=False)
    try:
        scripts = {}
        for item in reviewed_rows(book, "Scripts", ("script_index", "reviewed_id", "reviewed")):
            index = item["script_index"]
            if type(index) is not int or index not in expected or index in scripts:
                raise ValueError("Review workbook contains invalid scripts")
            scripts[index] = {
                "script_index": index, "id_ocr": raw_scripts[index]["id_ocr"],
                "class_ocr": raw_scripts[index]["class_ocr"], "name_ocr": raw_scripts[index].get("name_ocr"),
                "errors": raw_scripts[index]["errors"],
                "reviewed_id": item["reviewed_id"], "reviewed": confirmed(item["reviewed"]),
            }
        marks = {}
        for item in reviewed_rows(book, "Marks", (
                "script_index", "question", "answer", "reasoning", "manual_mark", "reviewed")):
            pair = (item["script_index"], question_key(item["question"]))
            if type(pair[0]) is not int or pair not in expected_marks or pair in marks:
                raise ValueError("Review workbook contains invalid marks")
            original = raw_marks[pair]
            marks[pair] = {
                "answer": item["answer"] if item["answer"] is not None else "",
                "reasoning": item["reasoning"] if item["reasoning"] is not None else "",
                "manual_mark": item["manual_mark"], "reviewed": confirmed(item["reviewed"]),
                "raw_mark": original["raw_mark"], "moderated_mark": original["moderated_mark"],
                "similarity_score": original.get("similarity_score"),
                "moderator_flag": original["moderator_flag"], "moderator_note": original["moderator_note"],
                "grading_failed": original["grading_failed"], "original_answer": original["answer"],
                "previous": previous.get(pair),
            }
        if set(scripts) != expected or set(marks) != expected_marks:
            raise ValueError("Review workbook does not cover the roster and rubric")
    finally:
        book.close()
    if (file_hash(path) != review_hash or file_hash(root / "results.json") != result_hash
            or file_hash(root / "manifest.json") != manifest_hash):
        raise ValueError("Grading or review changed while loading")
    revision = f"{review_hash}:{result_hash}:{manifest_hash}"
    return root, revision, roster, questions, scripts, marks


def locate_row(sheet, index, question=None):
    headers = [str(cell.value).strip().lower() if cell.value is not None else "" for cell in sheet[1]]
    required = ("script_index", "reviewed_id", "reviewed") if question is None else (
        "script_index", "question", "answer", "reasoning", "manual_mark", "reviewed")
    if any(headers.count(name) != 1 for name in required):
        raise ValueError("Review workbook headers changed")
    positions = {key: headers.index(key) + 1 for key in required}
    matches = []
    for row in range(2, sheet.max_row + 1):
        if sheet.cell(row, positions["script_index"]).value != index:
            continue
        if question is None or question_key(sheet.cell(row, positions["question"]).value) == question:
            matches.append(row)
    if len(matches) != 1:
        raise ValueError("Review row is missing or duplicated")
    return matches[0], positions


def commit_review(server, root, book, revision):
    review_hash, result_hash, manifest_hash = revision.split(":")
    history = root / "review-history"
    history.mkdir(mode=0o700, exist_ok=True)
    backup = history / f"{review_hash}.xlsx"
    try:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as destination, (root / "review.xlsx").open("rb") as source:
            shutil.copyfileobj(source, destination)
    except FileExistsError:
        pass
    if file_hash(backup) != review_hash or file_hash(root / "review.xlsx") != review_hash:
        raise RuntimeError("Review changed during save; reload before editing")
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, suffix=".xlsx", delete=False) as file:
            temp = Path(file.name)
        book.save(temp)
        verify_grading_run(server.config, server.config_dir, server.config_path, run_id=server.run_id)
        if (file_hash(root / "review.xlsx") != review_hash or file_hash(root / "results.json") != result_hash
                or file_hash(root / "manifest.json") != manifest_hash):
            raise RuntimeError("Grading or review changed during save; reload before editing")
        os.replace(temp, root / "review.xlsx")
        return f"{file_hash(root / 'review.xlsx')}:{result_hash}:{manifest_hash}"
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def save_review(server, data):
    root, revision, roster, questions, scripts, marks = load_state(server)
    if data.get("revision") != revision:
        raise RuntimeError("Review changed in another editor; reload before saving")
    index = data.get("script_index")
    if type(index) is not int or index not in scripts or type(data.get("reviewed")) is not bool:
        raise ValueError("Invalid script or review state")
    reviewed = data["reviewed"]
    kind = data.get("kind")
    book = load_workbook(root / "review.xlsx", data_only=False)
    try:
        if kind == "script":
            student_id = data.get("reviewed_id")
            if not isinstance(student_id, str):
                raise ValueError("Reviewed ID must be text")
            student_id = student_id.strip()
            if student_id and student_id not in roster:
                raise ValueError("Reviewed ID must exist in the roster")
            if reviewed and not student_id:
                raise ValueError("Choose a roster ID before marking this script reviewed")
            if student_id and any(str(other["reviewed_id"] or "").strip() == student_id
                                  for i, other in scripts.items() if i != index):
                raise ValueError("A different script already uses this roster ID")
            sheet = book["Scripts"]
            row, pos = locate_row(sheet, index)
            sheet.cell(row, pos["reviewed_id"], student_id).data_type = "s"
        elif kind == "mark":
            question_index = data.get("question_index")
            if type(question_index) is not int or not 0 <= question_index < len(questions):
                raise ValueError("Invalid question")
            question = questions[question_index]
            key = question_key(question["question_number"])
            original = marks[(index, key)]
            answer, reasoning, manual = (data.get(key) for key in ("answer", "reasoning", "manual_mark"))
            if any(not isinstance(value, str) or len(value) > 32767 for value in (answer, reasoning)):
                raise ValueError("Answer and reasoning must be text of at most 32,767 characters")
            if not isinstance(manual, str):
                raise ValueError("Manual mark must be text")
            if manual.strip():
                try:
                    manual = float(manual)
                except ValueError:
                    raise ValueError("Manual mark must be numeric") from None
                if not math.isfinite(manual) or not 0 <= manual <= question["marks"]:
                    raise ValueError("Manual mark is outside the question's range")
            else:
                manual = None
            if reviewed and manual is None and (original["grading_failed"] or original["moderator_flag"]
                                                or answer != original["original_answer"]):
                raise ValueError("A failed, flagged or corrected answer requires a manual mark")
            sheet = book["Marks"]
            row, pos = locate_row(sheet, index, key)
            for name, value in (("answer", answer), ("reasoning", reasoning)):
                cell = sheet.cell(row, pos[name], value)
                cell.data_type = "s"
            sheet.cell(row, pos["manual_mark"]).value = manual
        else:
            raise ValueError("Invalid review kind")
        sheet.cell(row, pos["reviewed"], "yes" if reviewed else "")
        return commit_review(server, root, book, revision)
    finally:
        book.close()


def suggestion_parameters(question, threshold, step):
    decimal_format = re.compile(r"(?:[0-9]+(?:\.[0-9]{0,12})?|\.[0-9]{1,12})\Z")
    if (not isinstance(threshold, str) or not isinstance(step, str) or len(threshold) > 40 or len(step) > 40
            or not decimal_format.fullmatch(threshold) or not decimal_format.fullmatch(step)):
        raise ValueError("Enter a similarity threshold and mark increment")
    try:
        threshold, step = Decimal(threshold), Decimal(step)
        maximum = Decimal(str(question["marks"]))
        if (not threshold.is_finite() or not step.is_finite() or not 0 <= threshold <= 1
                or not 0 < step <= maximum):
            raise ValueError("Threshold must be 0 to 1 and increment must be positive and within maximum marks")
    except InvalidOperation:
        raise ValueError("Enter a valid similarity threshold and mark increment") from None
    return maximum, threshold, step


def suggestion_value(mark, parameters):
    similarity = mark["similarity_score"]
    if (isinstance(similarity, bool) or not isinstance(similarity, (int, float))
            or not math.isfinite(similarity) or not 0 <= similarity <= 1):
        raise ValueError("Similarity is unavailable for this answer")
    if mark["answer"] != mark["original_answer"]:
        raise ValueError("Similarity applies only to the original OCR answer")
    maximum, threshold, step = parameters
    score = Decimal(str(similarity))
    with localcontext() as context:
        context.prec = max(50, maximum.adjusted() + 50)
        suggestion = maximum if score >= threshold else ((maximum * score / step).to_integral_value(
            rounding=ROUND_FLOOR) * step)
    return format(suggestion, "f")


def suggest_mark(server, data):
    _, revision, _, questions, scripts, marks = load_state(server)
    if data.get("revision") != revision:
        raise RuntimeError("Review changed in another editor; reload before calculating")
    index, question_index = data.get("script_index"), data.get("question_index")
    if (type(index) is not int or index not in scripts or type(question_index) is not int
            or not 0 <= question_index < len(questions)):
        raise ValueError("Invalid script or question")
    question = questions[question_index]
    return suggestion_value(marks[(index, question_key(question["question_number"]))],
                            suggestion_parameters(question, data.get("threshold"), data.get("step")))


def bulk_suggestions(server, data):
    root, revision, _, questions, scripts, marks = load_state(server)
    if data.get("revision") != revision:
        raise RuntimeError("Review changed in another editor; reload before calculating")
    question_index = data.get("question_index")
    if type(question_index) is not int or not 0 <= question_index < len(questions):
        raise ValueError("Invalid question")
    question = questions[question_index]
    key = question_key(question["question_number"])
    parameters = suggestion_parameters(question, data.get("threshold"), data.get("step"))
    suggestions = []
    for index in sorted(scripts):
        try:
            value = suggestion_value(marks[(index, key)], parameters)
            suggestions.append({"script_index": index, "suggestion": value, "error": None})
        except ValueError as exc:
            suggestions.append({"script_index": index, "suggestion": None, "error": str(exc)})
    return root, revision, key, suggestions


def apply_bulk_suggestions(server, data):
    if data.get("confirmed") is not True:
        raise ValueError("Explicit confirmation is required")
    preview = server.bulk_preview
    if (preview is None or not isinstance(data.get("preview_token"), str)
            or not hmac.compare_digest(preview["token"], data["preview_token"])
            or any(data.get(key) != preview[key]
                   for key in ("revision", "question_index", "threshold", "step"))):
        raise ValueError("Preview the same suggestions before applying them")
    root, revision, key, suggestions = bulk_suggestions(server, data)
    selected = data.get("selected")
    if not isinstance(selected, list) or not selected:
        raise ValueError("Select at least one suggestion")
    eligible = {entry["script_index"]: entry["suggestion"] for entry in suggestions
                if entry["suggestion"] is not None}
    chosen = {}
    for item in selected:
        if not isinstance(item, dict):
            raise ValueError("Invalid suggestion selection")
        index, suggestion = item.get("script_index"), item.get("suggestion")
        if (type(index) is not int or index in chosen or index not in eligible
                or suggestion != eligible[index]):
            raise ValueError("A selected suggestion is invalid or changed; preview again")
        chosen[index] = suggestion
    book = load_workbook(root / "review.xlsx", data_only=False)
    try:
        sheet = book["Marks"]
        for index, value in chosen.items():
            row, pos = locate_row(sheet, index, key)
            sheet.cell(row, pos["manual_mark"]).value = float(value)
            sheet.cell(row, pos["reviewed"]).value = None
        commit_review(server, root, book, revision)
    finally:
        book.close()
    return len(chosen)


@contextmanager
def writer_lock(server):
    root = run_directory(server.config, server.config_dir, server.run_id) / "grading"
    lock = root / ".score-review.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError("Another score editor is saving; investigate a stale .score-review.lock") from None
    try:
        with os.fdopen(fd, "w") as file:
            file.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


class ScoreServer(ThreadingHTTPServer):
    def __init__(self, config, config_dir, port=0, config_path=None, *, run_id=None):
        self.config, self.config_dir, self.config_path = config, config_dir, config_path
        self.run_id = run_id
        self.token = secrets.token_urlsafe(32)
        self.bulk_preview = None
        self.lock = threading.Lock()
        load_state(self)
        super().__init__(("127.0.0.1", port), ScoreHandler)

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server_port}"


class ScoreHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def respond(self, status, data, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def json_response(self, status, data):
        self.respond(status, json.dumps(data, ensure_ascii=False).encode())

    def authorized(self):
        return (self.headers.get("Host") == self.server.origin.removeprefix("http://")
                and hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {self.server.token}"))

    def do_GET(self):
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            return self.json_response(403, {"error": "Invalid host"})
        if self.path == "/":
            return self.respond(200, Path(__file__).with_name("score_editor.html").read_bytes(),
                                "text/html; charset=utf-8")
        if not self.authorized():
            return self.json_response(403, {"error": "Unauthorized"})
        try:
            with self.server.lock:
                root, revision, roster, questions, scripts, marks = load_state(self.server)
                _, manifest, _, _ = verify_grading_run(self.server.config, self.server.config_dir,
                                                        self.server.config_path, run_id=self.server.run_id)
                if file_hash(root / "manifest.json") != revision.split(":")[2]:
                    raise ValueError("Grading manifest changed")
                effective_crops = manifest.get("effective_crops", {})
                if self.path == "/api/state":
                    _, _, _, boxes = approved_inputs(self.server.config, self.server.config_dir, self.server.config_path)
                    return self.json_response(200, {
                        "revision": revision, "roster": roster, "questions": questions,
                        "scripts": [scripts[i] for i in sorted(scripts)],
                        "marks": [[marks[(i, question_key(q["question_number"]))] for q in questions]
                                  for i in sorted(scripts)],
                        "crops": [[effective_crops.get(question_key(q["question_number"]),
                                                     [boxes[question_key(q["question_number"])][1]] * len(scripts))[i]
                                   for q in questions] for i in sorted(scripts)],
                    })
                segments = self.path.split("/")
                if len(segments) == 5 and segments[1:3] == ["api", "image"]:
                    script, field = segments[3:]
                    full_page = field.startswith("full-")
                    context_crop = field.startswith("ctx-")
                    if full_page:
                        field = field[5:]
                    elif context_crop:
                        field = field[4:]
                    if not script.isascii() or not script.isdigit() or int(script) not in scripts:
                        return self.json_response(404, {"error": "Image not found"})
                    if field in {"id", "name", "class"}:
                        key = field.upper()
                    elif field.isascii() and field.isdigit() and int(field) < len(questions):
                        key = question_key(questions[int(field)]["question_number"])
                    else:
                        return self.json_response(404, {"error": "Image not found"})
                    _, _, _, boxes = approved_inputs(self.server.config, self.server.config_dir, self.server.config_path)
                    page, box = boxes[key]
                    if key in effective_crops:
                        box = effective_crops[key][int(script)]
                    image = root / "images" / f"{int(script) * self.server.config['layout']['pages_per_student'] + page}.jpg"
                    if full_page:
                        return self.respond(200, image.read_bytes(), "image/jpeg")
                    if context_crop:
                        with Image.open(image) as source:
                            pad_x = max(80, int(box["width"] * 0.35))
                            pad_y = max(80, int(box["height"] * 0.50))
                            x0 = max(0, box["x"] - pad_x)
                            y0 = max(0, box["y"] - pad_y)
                            x1 = min(source.width, box["x"] + box["width"] + pad_x)
                            y1 = min(source.height, box["y"] + box["height"] + pad_y)
                            box = {
                                "x": x0,
                                "y": y0,
                                "width": max(1, x1 - x0),
                                "height": max(1, y1 - y0),
                            }
                    return self.respond(200, crop_image(image, box), "image/png")
        except (OSError, ValueError, KeyError, TypeError):
            return self.json_response(409, {"error": "Run or review changed; stop and inspect it"})
        return self.json_response(404, {"error": "Not found"})

    def do_POST(self):
        if not self.authorized() or self.headers.get("Origin") != self.server.origin:
            return self.json_response(403, {"error": "Unauthorized"})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.json_response(415, {"error": "Expected JSON"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1024 * 1024:
                return self.json_response(400, {"error": "Invalid request size"})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Invalid request")
            with self.server.lock:
                if self.path == "/api/preview-crop":
                    root, revision, _, questions, scripts, _ = load_state(self.server)
                    index, question_index = data.get("script_index"), data.get("question_index")
                    if data.get("revision") != revision:
                        raise RuntimeError("Review changed in another editor; reload before previewing")
                    if (type(index) is not int or index not in scripts or type(question_index) is not int
                            or not 0 <= question_index < len(questions)):
                        raise ValueError("Invalid script or question")
                    _, _, _, boxes = approved_inputs(self.server.config, self.server.config_dir, self.server.config_path)
                    page, _ = boxes[question_key(questions[question_index]["question_number"])]
                    image = root / "images" / f"{index * self.server.config['layout']['pages_per_student'] + page}.jpg"
                    coords = {key: data.get(key) for key in ("x", "y", "width", "height")}
                    if any(type(value) is not int for value in coords.values()):
                        raise ValueError("Crop coordinates must be whole numbers")
                    with Image.open(image) as source:
                        if (coords["x"] < 0 or coords["y"] < 0 or coords["width"] <= 0 or coords["height"] <= 0
                                or coords["x"] + coords["width"] > source.width
                                or coords["y"] + coords["height"] > source.height):
                            raise ValueError("Crop must fit within the approved page image")
                    return self.respond(200, crop_image(image, coords), "image/png")
                if self.path == "/api/suggest":
                    return self.json_response(200, {"suggestion": suggest_mark(self.server, data)})
                if self.path == "/api/bulk-preview":
                    _, _, _, suggestions = bulk_suggestions(self.server, data)
                    token = secrets.token_urlsafe(32)
                    self.server.bulk_preview = {"token": token, **{
                        key: data.get(key) for key in ("revision", "question_index", "threshold", "step")}}
                    return self.json_response(200, {"preview_token": token, "suggestions": suggestions})
                if self.path in {"/api/save", "/api/approve", "/api/bulk-apply"}:
                    with writer_lock(self.server):
                        if self.path == "/api/save":
                            return self.json_response(200, {"revision": save_review(self.server, data)})
                        if self.path == "/api/bulk-apply":
                            count = apply_bulk_suggestions(self.server, data)
                            self.server.bulk_preview = None
                            return self.json_response(200, {"updated": count})
                        root, revision, *_ = load_state(self.server)
                        if data.get("revision") != revision:
                            raise RuntimeError("Review changed in another editor; reload before approval")
                        if data.get("confirmed") is not True:
                            raise ValueError("Explicit confirmation is required")
                        count, marks = approve_grading(self.server.config, self.server.config_dir,
                                                       self.server.config_path, run_id=self.server.run_id)
                        return self.json_response(200, {"students": count, "marks": marks})
        except RuntimeError as exc:
            return self.json_response(409, {"error": str(exc)})
        except (ValueError, KeyError, TypeError, IllegalCharacterError) as exc:
            return self.json_response(400, {"error": str(exc)})
        except OSError:
            return self.json_response(409, {"error": "Run or review changed; stop and inspect it"})
        return self.json_response(404, {"error": "Not found"})
